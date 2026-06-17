`timescale 1ns/1ps

// Current CDC: brings Q16.16 current values from the slow neuron domain
// (clk_50) into the fast DAC domain (gth_tx_usrclk2) as ready-to-play 64-bit DAC
// words, so a DAC can show either per-neuron input current or the pure injected
// current source, time-locked (matched transport latency) with the spike DAC.
//
// The clocks are unrelated, so each captured Q16.16 vector crosses through an
// async FIFO.  Between captures the output holds the most recent FIFO item.
// Each Q16.16 current is scaled to a signed 16-bit DAC sample (>> SHIFT, with
// saturation) and replicated across the 4 samples of the beat (the signal is
// effectively DC over a 4 ns beat, so replicate -- never interpolate).

module cur_monitor_cdc #(
    parameter integer N     = 4,        // neurons / monitors
    parameter integer SHIFT = 8         // Q16.16 -> s16 scale (>> SHIFT, saturating)
) (
    input  wire             src_clk,    // clk_50 (neuron domain)
    input  wire             src_rst,    // reset in src_clk
    input  wire [N*32-1:0]  i_mon,      // N x Q16.16 per-neuron currents
    input  wire             capture,    // 1-cycle pulse (src_clk): latch a new sample

    input  wire             dst_clk,    // gth_tx_usrclk2 (DAC domain)
    output reg  [N*64-1:0]  mon_words   // N x 64-bit DAC words (dst_clk)
);
    localparam integer WIDTH = N * 32;
    localparam integer FIFO_DEPTH = 16;

    wire [WIDTH-1:0] fifo_dout;
    wire             fifo_full;
    wire             fifo_empty;
    wire             fifo_wr_rst_busy;
    wire             fifo_rd_rst_busy;
    wire             fifo_wr_en = capture & ~fifo_full & ~fifo_wr_rst_busy;
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
    ) u_mon_fifo (
        .dout          (fifo_dout),
        .empty         (fifo_empty),
        .full          (fifo_full),
        .din           (i_mon),
        .wr_en         (fifo_wr_en),
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

    // ---- dst_clk: hold the newest complete payload --------------------------
    reg [N*32-1:0] cap = {(N*32){1'b0}};
    always @(posedge dst_clk)
        if (fifo_rd_en)
            cap <= fifo_dout;

    // ---- scale Q16.16 -> s16 (saturating), replicate x4 into the beat --------
    genvar n;
    generate
        for (n = 0; n < N; n = n + 1) begin : g_scale
            wire signed [31:0] v = $signed(cap[n*32 +: 32]) >>> SHIFT;
            wire signed [15:0] s = (v >  32'sd32767)  ? 16'sh7FFF :
                                   (v < -32'sd32768)  ? 16'sh8000 :
                                                        v[15:0];
            always @(posedge dst_clk)
                mon_words[n*64 +: 64] <= {s, s, s, s};   // 4 identical samples
        end
    endgenerate

endmodule
