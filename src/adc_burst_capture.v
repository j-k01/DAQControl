`timescale 1ns/1ps

// One-shot, full-rate (NO decimation) ADC burst capture into the S2MM DMA.
//
// The ADC writes 128-bit beats (16 B = 4 samples x 2 ch = 4 ns at 1 GS/s) at
// the beat clock `clk` (~250 MHz = 4 GB/s/chip).  An ASYNCHRONOUS block-RAM
// FIFO crosses into the faster DMA drain domain `rd_clk` (~300 MHz): the drain
// pops 1 beat/cycle at rd_clk, so it runs FASTER than the 250 MHz fill.  That
// headroom lets the FIFO drain its backlog after any transient DDR/HP-port
// backpressure (refresh, arbitration), so occupancy stays bounded and the
// capture is LOSSLESS for the full window.
//
// (History: with a single-clock FIFO the drain was capped at the fill rate, so
// every stalled DMA cycle was a *permanent* deficit -- the FIFO filled
// monotonically and overflowed past ~1.5-2 MB/chip.  A bigger FIFO only delays
// that; the fix is drain-rate headroom, hence the async FIFO + faster rd_clk.)
//
// If the FIFO ever fills while a beat is arriving, a sample was dropped:
// `overflow` latches and the capture is invalid (the firmware checks it).
//
// Both chips' instances share one `start` and one beat clock, and the two
// ADCs are SYSREF-aligned (JESD204B subclass 1), so chip0[n] and chip1[n] are
// the same instant: the two captures line up 1-to-1.

module adc_burst_capture #(
    parameter integer FIFO_DEPTH = 4096
) (
    input  wire         clk,            // ADC beat clock (write side, ~250 MHz)
    input  wire         rd_clk,         // DMA drain clock (read side, faster)
    input  wire         rst,            // reset, applied in the `clk` domain
    input  wire         start,          // 1-cycle pulse (clk): begin a capture
    input  wire [31:0]  capture_beats,  // total 128-bit beats to capture

    input  wire         data_valid,     // ADC beat valid (JESD ready)
    input  wire [127:0] frame_data,     // {ch_odd s3..s0, ch_even s3..s0}

    output wire [127:0] m_axis_tdata,
    output wire [15:0]  m_axis_tkeep,
    output wire         m_axis_tlast,
    output wire         m_axis_tvalid,
    input  wire         m_axis_tready,

    output wire [31:0]  status
);
    assign m_axis_tkeep = 16'hFFFF;

    // ---- write side (clk / ADC domain) --------------------------------------
    reg         running = 1'b0;
    reg         armed = 1'b0;
    reg         done = 1'b0;
    reg [31:0]  in_rem = 32'd0;
    reg         overflow = 1'b0;
    reg         axis_enable = 1'b0;
    reg         fifo_clear = 1'b0;
    reg [3:0]   fifo_clear_count = 4'd0;
    reg         start_pending = 1'b0;
    reg [31:0]  pending_beats = 32'd0;

    wire        last_beat = (in_rem == 32'd1);
    wire        have_beat = running & data_valid & (in_rem != 32'd0);

    wire        fifo_full;
    wire        fifo_empty;
    wire        fifo_wr_rst_busy_raw;
    wire        fifo_rd_rst_busy_raw;
    wire [128:0] fifo_dout;
    wire        fifo_wr_en = have_beat & ~fifo_full;
    wire [128:0] fifo_din  = {last_beat, frame_data};
    wire        fifo_rd_en = axis_enable & m_axis_tvalid & m_axis_tready;

    wire        fifo_rst = rst | fifo_clear;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] fifo_wr_busy_sync = 2'b11;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] fifo_rd_busy_sync = 2'b11;
    wire        fifo_ready_after_clear = ~fifo_wr_busy_sync[1] & ~fifo_rd_busy_sync[1];

    // First-word-fall-through: dout is valid whenever non-empty, straight onto
    // AXIS in the rd_clk domain.
    assign m_axis_tvalid = axis_enable & ~fifo_empty;
    assign m_axis_tdata  = fifo_dout[127:0];
    assign m_axis_tlast  = fifo_dout[128];

    xpm_fifo_async #(
        .FIFO_MEMORY_TYPE   ("block"),
        .FIFO_WRITE_DEPTH   (FIFO_DEPTH),
        .WRITE_DATA_WIDTH   (129),
        .READ_DATA_WIDTH    (129),
        .READ_MODE          ("fwft"),
        .FIFO_READ_LATENCY  (0),
        .CDC_SYNC_STAGES    (2),
        .RELATED_CLOCKS     (0),
        .USE_ADV_FEATURES   ("0000")
    ) u_fifo (
        .dout          (fifo_dout),
        .empty         (fifo_empty),
        .full          (fifo_full),
        .din           (fifo_din),
        .wr_en         (fifo_wr_en),
        .wr_clk        (clk),
        .rd_en         (fifo_rd_en),
        .rd_clk        (rd_clk),
        .rst           (fifo_rst),
        .injectdbiterr (1'b0),
        .injectsbiterr (1'b0),
        .sleep         (1'b0),
        // unused outputs
        .almost_empty  (), .almost_full (), .data_valid (), .dbiterr (),
        .overflow      (), .prog_empty  (), .prog_full  (), .rd_data_count (),
        .rd_rst_busy   (fifo_rd_rst_busy_raw), .sbiterr     (), .underflow  (),
        .wr_ack        (), .wr_data_count (), .wr_rst_busy (fifo_wr_rst_busy_raw)
    );

    // Read-side status bits synced into the clk domain for a coherent status
    // word (status is read slowly over UART / the mailbox).
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] empty_sync = 2'b11;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] tready_sync = 2'b00;
    always @(posedge clk) begin
        empty_sync  <= {empty_sync[0],  fifo_empty};
        tready_sync <= {tready_sync[0], m_axis_tready};
        fifo_wr_busy_sync <= {fifo_wr_busy_sync[0], fifo_wr_rst_busy_raw};
        fifo_rd_busy_sync <= {fifo_rd_busy_sync[0], fifo_rd_rst_busy_raw};
    end

    always @(posedge clk) begin
        if (rst) begin
            running <= 1'b0;
            armed <= 1'b0;
            done <= 1'b0;
            in_rem <= 32'd0;
            overflow <= 1'b0;
            axis_enable <= 1'b0;
            fifo_clear <= 1'b0;
            fifo_clear_count <= 4'd0;
            start_pending <= 1'b0;
            pending_beats <= 32'd0;
        end else if (start) begin
            // A DMA reset/re-arm does NOT reset this PL-side async FIFO. If any
            // previous capture beats remain, a newly armed S2MM channel will
            // drain stale data into DDR before the new ADC window starts. Gate
            // AXIS off, clear the FIFO, then begin the capture once reset has
            // propagated through both FIFO clock domains.
            running <= 1'b0;
            armed <= 1'b0;
            done <= 1'b0;
            in_rem <= 32'd0;
            overflow <= 1'b0;
            axis_enable <= 1'b0;
            fifo_clear <= 1'b1;
            fifo_clear_count <= 4'd8;
            start_pending <= 1'b1;
            pending_beats <= capture_beats;
        end else begin
            if (fifo_clear) begin
                if (fifo_clear_count != 4'd0)
                    fifo_clear_count <= fifo_clear_count - 1'b1;
                else
                    fifo_clear <= 1'b0;
            end else if (start_pending && fifo_ready_after_clear) begin
                start_pending <= 1'b0;
                if (pending_beats == 32'd0) begin
                    running <= 1'b0;
                    armed <= 1'b0;
                    done <= 1'b1;
                    in_rem <= 32'd0;
                    axis_enable <= 1'b0;
                end else begin
                    running <= 1'b1;
                    armed <= 1'b1;
                    in_rem <= pending_beats;
                    axis_enable <= 1'b1;
                end
            end

            if (have_beat & ~fifo_full) begin
                in_rem <= in_rem - 32'd1;
                if (last_beat) begin
                    running <= 1'b0;     // finished pushing the window
                end
            end
            if (have_beat & fifo_full) begin
                overflow <= 1'b1;        // dropped a beat -> capture invalid
            end
            // capture is complete once all beats are pushed and the FIFO has
            // drained into the DMA.
            if (armed & ~running & (in_rem == 32'd0) & empty_sync[1]) begin
                done <= 1'b1;
                armed <= 1'b0;
                axis_enable <= 1'b0;
            end
        end
    end

    assign status = {
        8'hBC,
        done,
        running,
        overflow,
        tready_sync[1],
        fifo_clear | start_pending,
        axis_enable,
        in_rem[17:0]
    };

endmodule
