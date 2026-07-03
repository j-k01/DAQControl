`timescale 1ns/1ps

// Unified IZH observation CDC.
//
// Crosses the DAC-visible neuron/current observation signals from clk_50 into
// the DAC/JESD clock as one packet:
//   - pure injected current, Q16.16
//   - four per-neuron current monitors, Q16.16
//   - four spike event bits
//
// The neuron input current itself is not changed here.  The optional gain is
// applied only to the pure-current DAC view (source 15) after the CDC.
module izh_observation_cdc #(
    parameter integer SHIFT = 8
) (
    input  wire             src_clk,
    input  wire             src_rst,
    input  wire signed [31:0] pure_current_q16,
    input  wire [127:0]     monitor_q16,
    input  wire [3:0]       spike_flags,
    input  wire             capture,

    input  wire             dst_clk,
    input  wire [15:0]      pure_gain_q8_8,
    output reg  [255:0]     mon_words,
    output reg  [63:0]      current_word,
    output reg  [3:0]       spike_start
);
    localparam integer WIDTH = 32 + 128 + 4;
    localparam integer FIFO_DEPTH = 16;

    reg [3:0]       spike_pending = 4'd0;
    reg             refresh_pending = 1'b0;
    reg [WIDTH-1:0] fifo_din_r = {WIDTH{1'b0}};
    reg             fifo_wr_en_r = 1'b0;

    wire [WIDTH-1:0] fifo_dout;
    wire             fifo_full;
    wire             fifo_empty;
    wire             fifo_wr_rst_busy;
    wire             fifo_rd_rst_busy;
    wire             fifo_rd_en = ~fifo_empty & ~fifo_rd_rst_busy;

    xpm_fifo_async #(
        .FIFO_MEMORY_TYPE   ("auto"),
        .FIFO_WRITE_DEPTH   (FIFO_DEPTH),
        .WRITE_DATA_WIDTH   (WIDTH),
        .READ_DATA_WIDTH    (WIDTH),
        .READ_MODE          ("fwft"),
        .FIFO_READ_LATENCY  (0),
        .CDC_SYNC_STAGES    (2),
        .RELATED_CLOCKS     (0),
        .USE_ADV_FEATURES   ("0000")
    ) u_obs_fifo (
        .dout          (fifo_dout),
        .empty         (fifo_empty),
        .full          (fifo_full),
        .din           (fifo_din_r),
        .wr_en         (fifo_wr_en_r),
        .wr_clk        (src_clk),
        .rd_en         (fifo_rd_en),
        .rd_clk        (dst_clk),
        .rst           (src_rst),
        .injectdbiterr (1'b0),
        .injectsbiterr (1'b0),
        .sleep         (1'b0),
        .almost_empty  (), .almost_full (), .data_valid (), .dbiterr (),
        .overflow      (), .prog_empty  (), .prog_full  (), .rd_data_count (),
        .rd_rst_busy   (fifo_rd_rst_busy), .sbiterr     (), .underflow  (), .wr_ack (),
        .wr_data_count (), .wr_rst_busy (fifo_wr_rst_busy)
    );

    wire [3:0] spike_to_send = spike_pending | spike_flags;
    wire       refresh_to_send = refresh_pending | capture;
    wire want_write = (refresh_to_send | (|spike_to_send)) &
                      ~fifo_full & ~fifo_wr_rst_busy;

    always @(posedge src_clk) begin
        if (src_rst) begin
            spike_pending  <= 4'd0;
            refresh_pending <= 1'b0;
            fifo_wr_en_r   <= 1'b0;
            fifo_din_r     <= {WIDTH{1'b0}};
        end else begin
            fifo_wr_en_r <= 1'b0;
            if (want_write) begin
                fifo_din_r <= {spike_to_send, monitor_q16, pure_current_q16};
                fifo_wr_en_r <= 1'b1;
                spike_pending <= 4'd0;
                refresh_pending <= 1'b0;
            end else begin
                spike_pending <= spike_to_send;
                refresh_pending <= refresh_to_send;
            end
        end
    end

    function [15:0] q16_to_s16;
        input signed [31:0] q;
        reg signed [31:0] v;
        begin
            v = q >>> SHIFT;
            if (v > 32'sd32767)
                q16_to_s16 = 16'h7FFF;
            else if (v < -32'sd32768)
                q16_to_s16 = 16'h8000;
            else
                q16_to_s16 = v[15:0];
        end
    endfunction

    wire signed [31:0] fifo_pure = $signed(fifo_dout[31:0]);
    wire [127:0] fifo_mon = fifo_dout[32 +: 128];
    wire [3:0] fifo_spike = fifo_dout[160 +: 4];

    // Effective DAC-view gain; 0 selects the hardware default 1.0x.  Quasi-
    // static (register write through a CDC), so it is safe to use directly
    // in the stage-1 multiply below.
    wire [15:0] gain_eff = (pure_gain_q8_8 == 16'd0) ? 16'h0100 : pure_gain_q8_8;

    // ---- staged presentation pipeline ----------------------------------
    // The Q16.16 x Q8.8 gain multiply is a DSP cascade and cannot also carry
    // the shift/saturate cone to the presentation register in one 250 MHz
    // cycle (it was the worst setup violation of the 457d227 build), so it
    // is pipelined: the pop captures the raw packet (s0), the next cycle
    // registers the DSP product (s1), and the cycle after saturates and
    // presents.  spike_start fires at s1 -- one stage after the pop -- so
    // the presented words still lag spike_start by exactly one DAC clock,
    // the relationship izh_spike_shaper's synchronous BRAM read expects.
    // Back-to-back FIFO pops stream through the stages one per cycle.
    reg               s0_valid = 1'b0;
    reg signed [31:0] s0_pure = 32'd0;
    reg [127:0]       s0_mon = 128'd0;
    reg [3:0]         s0_spike = 4'd0;

    reg signed [48:0] s1_prod = 49'd0;       // 32b x 17b signed product
    reg [255:0]       s1_mon_words = 256'd0;

    integer n;
    reg [15:0]  s;
    reg [255:0] mon_words_next;

    always @(*) begin
        mon_words_next = 256'd0;
        for (n = 0; n < 4; n = n + 1) begin
            s = q16_to_s16($signed(s0_mon[n*32 +: 32]));
            mon_words_next[n*64 +: 64] = {s, s, s, s};
        end
    end

    wire signed [48:0] gain_shifted = s1_prod >>> (8 + SHIFT);
    reg [15:0] cur_s;
    always @(*) begin
        if (gain_shifted > 49'sd32767)
            cur_s = 16'h7FFF;
        else if (gain_shifted < -49'sd32768)
            cur_s = 16'h8000;
        else
            cur_s = gain_shifted[15:0];
    end

    always @(posedge dst_clk) begin
        // stage 0: capture the popped packet
        s0_valid <= fifo_rd_en;
        if (fifo_rd_en) begin
            s0_pure  <= fifo_pure;
            s0_mon   <= fifo_mon;
            s0_spike <= fifo_spike;
        end

        // stage 1: register the DSP product + converted monitors; spike fires
        spike_start <= 4'd0;
        if (s0_valid) begin
            s1_prod      <= s0_pure * $signed({1'b0, gain_eff});
            s1_mon_words <= mon_words_next;
            spike_start  <= s0_spike;
        end

        // stage 2: saturate + present.  s1_prod/s1_mon_words only change on a
        // packet, so presenting every cycle keeps the outputs stable and
        // zero-initialized before the first packet.
        current_word <= {cur_s, cur_s, cur_s, cur_s};
        mon_words    <= s1_mon_words;
    end
endmodule
