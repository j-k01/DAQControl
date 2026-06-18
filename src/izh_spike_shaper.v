`timescale 1ns/1ps

// Programmable spike-pulse shaper (replaces the fixed izh_spike_trapezoid).
//
// On each spike rising edge it plays a programmable pulse from a BRAM-backed
// beat-word table.  One beat-word is 64 bits = 4 SIGNED s16 DAC samples at the
// DAC/JESD beat rate.  `nbeats` selects how many beat-words play.
//
// Samples are full-range signed s16 (-32768..+32767): the pulse can be positive,
// negative, or biphasic.  The shape RAM is synchronous with 1-cycle latency, so
// the first pulse beat appears one clk after the detected spike edge.  Between
// pulses the output is 0.

module izh_spike_shaper #(
    parameter integer ADDR_W = 10              // 1024 beats * 4 = 4096 samples
) (
    input  wire               clk,       // jesd_clk (DAC domain)
    input  wire               reset,
    input  wire               spike,
    output reg  [ADDR_W-1:0]  shape_addr,
    input  wire [63:0]        shape_data, // beat-word b = {s3,s2,s1,s0}
    input  wire [ADDR_W:0]    nbeats,     // 1..2**ADDR_W
    output wire               active,
    output wire [63:0]        dac_word    // 4 signed s16 samples this beat
);
    localparam [ADDR_W:0] MAX_BEATS = {1'b1, {ADDR_W{1'b0}}};

    wire [ADDR_W:0] nb = (nbeats == {(ADDR_W+1){1'b0}}) ? {{ADDR_W{1'b0}}, 1'b1} :
                         (nbeats > MAX_BEATS)         ? MAX_BEATS :
                                                        nbeats;

    reg       spike_d  = 1'b0;
    reg       active_r = 1'b0;
    reg       fetching = 1'b0;
    reg [ADDR_W:0] out_idx = {(ADDR_W+1){1'b0}};
    wire      spike_edge = spike & ~spike_d;

    always @(posedge clk) begin
        if (reset) begin
            spike_d  <= 1'b0;
            active_r <= 1'b0;
            fetching <= 1'b0;
            out_idx  <= {(ADDR_W+1){1'b0}};
            shape_addr <= {ADDR_W{1'b0}};
        end else begin
            spike_d <= spike;
            if (spike_edge) begin
                active_r <= 1'b0;               // BRAM data for addr 0 arrives next clk
                fetching <= 1'b1;
                out_idx  <= {(ADDR_W+1){1'b0}};
                shape_addr <= {ADDR_W{1'b0}};
            end else if (fetching) begin
                active_r <= 1'b1;
                if (out_idx + {{ADDR_W{1'b0}}, 1'b1} >= nb) begin
                    fetching <= 1'b0;
                    out_idx  <= {(ADDR_W+1){1'b0}};
                    shape_addr <= {ADDR_W{1'b0}};
                end else begin
                    out_idx <= out_idx + {{ADDR_W{1'b0}}, 1'b1};
                    shape_addr <= out_idx[ADDR_W-1:0] + {{(ADDR_W-1){1'b0}}, 1'b1};
                end
            end else begin
                active_r <= 1'b0;
            end
        end
    end

    assign active   = active_r;
    assign dac_word = active_r ? shape_data : 64'd0;

endmodule
