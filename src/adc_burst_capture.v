`timescale 1ns/1ps

// One-shot, full-rate (NO decimation) ADC burst capture into the S2MM DMA.
//
// On a `start` pulse it streams exactly `capture_beats` 128-bit beats
// (16 B each = 4 samples x 2 channels = 4 ns at 1 GS/s) to the DMA, asserting
// tlast on the final beat so the SG transfer terminates, then stops.
//
// A block-RAM FIFO absorbs transient DDR/HP-port backpressure (refresh,
// arbitration) so the capture is LOSSLESS even though the input rate
// (4 GB/s/chip) equals the HP-port rate -- the average DDR service rate is
// higher, so the FIFO only ever holds a transient backlog. If the FIFO ever
// fills while a beat is arriving, a sample was dropped: `overflow` latches and
// the capture is invalid (the firmware checks it).
//
// Both chips' instances share one `start` and one beat clock, and the two
// ADCs are SYSREF-aligned (JESD204B subclass 1), so chip0[n] and chip1[n] are
// the same instant: the two captures line up 1-to-1.

module adc_burst_capture #(
    parameter integer FIFO_DEPTH = 2048
) (
    input  wire         clk,
    input  wire         rst,
    input  wire         start,          // 1-cycle pulse: begin a capture
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

    reg         running = 1'b0;
    reg         done = 1'b0;
    reg [31:0]  in_rem = 32'd0;          // input beats left to push
    reg         overflow = 1'b0;

    wire        last_beat = (in_rem == 32'd1);
    wire        have_beat = running & data_valid & (in_rem != 32'd0);

    wire        fifo_full;
    wire        fifo_empty;
    wire [128:0] fifo_dout;
    wire        fifo_wr_en = have_beat & ~fifo_full;
    wire [128:0] fifo_din  = {last_beat, frame_data};
    wire        fifo_rd_en = m_axis_tvalid & m_axis_tready;

    // First-word-fall-through: data is valid on dout whenever the FIFO is
    // non-empty, so it maps straight onto AXIS.
    assign m_axis_tvalid = ~fifo_empty;
    assign m_axis_tdata  = fifo_dout[127:0];
    assign m_axis_tlast  = fifo_dout[128];

    xpm_fifo_sync #(
        .FIFO_MEMORY_TYPE   ("block"),
        .FIFO_READ_LATENCY  (0),
        .FIFO_WRITE_DEPTH   (FIFO_DEPTH),
        .READ_DATA_WIDTH    (129),
        .READ_MODE          ("fwft"),
        .USE_ADV_FEATURES   ("0000"),
        .WRITE_DATA_WIDTH   (129)
    ) u_fifo (
        .dout          (fifo_dout),
        .empty         (fifo_empty),
        .full          (fifo_full),
        .din           (fifo_din),
        .injectdbiterr (1'b0),
        .injectsbiterr (1'b0),
        .rd_en         (fifo_rd_en),
        .rst           (rst),
        .sleep         (1'b0),
        .wr_clk        (clk),
        .wr_en         (fifo_wr_en),
        // unused outputs
        .almost_empty  (), .almost_full (), .data_valid (), .dbiterr (),
        .overflow      (), .prog_empty  (), .prog_full  (), .rd_data_count (),
        .rd_rst_busy   (), .sbiterr     (), .underflow  (), .wr_ack (),
        .wr_data_count (), .wr_rst_busy ()
    );

    always @(posedge clk) begin
        if (rst) begin
            running <= 1'b0;
            done <= 1'b0;
            in_rem <= 32'd0;
            overflow <= 1'b0;
        end else if (start) begin
            running <= 1'b1;
            done <= 1'b0;
            in_rem <= capture_beats;
            overflow <= 1'b0;
        end else begin
            if (have_beat & ~fifo_full) begin
                in_rem <= in_rem - 32'd1;
                if (last_beat) begin
                    running <= 1'b0;     // finished pushing the window
                end
            end
            if (have_beat & fifo_full) begin
                overflow <= 1'b1;        // dropped a beat -> capture invalid
            end
            if (fifo_rd_en & m_axis_tlast) begin
                done <= 1'b1;            // last beat accepted by the DMA
            end
        end
    end

    assign status = {
        8'hBC,
        done,
        running,
        overflow,
        m_axis_tready,
        in_rem[19:0]
    };

endmodule
