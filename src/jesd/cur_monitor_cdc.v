`timescale 1ns/1ps

// Current-monitor CDC: brings the four per-neuron input currents from the slow
// neuron domain (clk_50) into the fast DAC domain (gth_tx_usrclk2) as ready-to-
// play 64-bit DAC words, so a DAC can show the exact current a neuron is being
// fed -- time-locked (matched transport latency) with the spike DAC.
//
// Sample-and-hold via a data+toggle handshake: on each `capture` pulse the four
// Q16.16 currents are latched and a toggle flips; the GT domain syncs the toggle
// and grabs the (now-stable) latched data.  Between captures the output holds.
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
    // ---- src_clk: latch on capture, flip the handshake toggle ----------------
    reg [N*32-1:0] hold = {(N*32){1'b0}};
    reg            tog  = 1'b0;
    always @(posedge src_clk) begin
        if (src_rst) begin
            hold <= {(N*32){1'b0}};
            tog  <= 1'b0;
        end else if (capture) begin
            hold <= i_mon;
            tog  <= ~tog;
        end
    end

    // ---- dst_clk: sync the toggle, capture the (stable) latched data ---------
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [2:0] tog_sync = 3'b000;
    always @(posedge dst_clk)
        tog_sync <= {tog_sync[1:0], tog};
    wire tog_edge = tog_sync[2] ^ tog_sync[1];

    reg [N*32-1:0] cap = {(N*32){1'b0}};
    always @(posedge dst_clk)
        if (tog_edge)
            cap <= hold;

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
