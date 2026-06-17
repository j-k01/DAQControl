`timescale 1ns/1ps

// Programmable spike-pulse shaper (replaces the fixed izh_spike_trapezoid).
//
// On each spike rising edge it plays a programmable pulse from a beat-word
// table: NBEATS_MAX (16) "beat-words" of 64 bits each = 4 SIGNED s16 DAC samples
// per beat, at the DAC rate (4 samples per jesd_clk beat = 1 GS/s, so up to 64
// samples / 64 ns).  `nbeats` (1..NBEATS_MAX) selects how many beat-words play.
//
// Samples are full-range signed s16 (-32768..+32767): the pulse can be positive,
// negative, or biphasic.  The whole table is a flat bus (all beat-words present
// at once), so each beat is just a 16:1 mux -- no memory rate limit, and four
// independent neuron shapers can share one shape bus combinationally.  Between
// pulses the output is 0.

module izh_spike_shaper #(
    parameter integer NBEATS_MAX = 16          // 16 beats * 4 samples = 64 max
) (
    input  wire                      clk,       // jesd_clk (DAC domain)
    input  wire                      reset,
    input  wire                      spike,
    input  wire [NBEATS_MAX*64-1:0]  shape,     // beat-word b at [b*64 +: 64] = {s3,s2,s1,s0}
    input  wire [4:0]                nbeats,    // 1..NBEATS_MAX
    output wire                      active,
    output wire [63:0]               dac_word   // 4 signed s16 samples this beat
);
    // unpack the flat bus to a beat-word array (array index is simulator-portable
    // and synthesizes to the same mux as a variable part-select)
    wire [63:0] bw [0:NBEATS_MAX-1];
    genvar gi;
    generate
        for (gi = 0; gi < NBEATS_MAX; gi = gi + 1) begin : g_unpack
            assign bw[gi] = shape[gi*64 +: 64];
        end
    endgenerate

    // clamp beat count to 1..NBEATS_MAX
    wire [4:0] nb = (nbeats == 5'd0)               ? 5'd1 :
                    (nbeats > NBEATS_MAX[4:0])     ? NBEATS_MAX[4:0] :
                                                     nbeats;

    reg       spike_d  = 1'b0;
    reg       active_r = 1'b0;
    reg [4:0] bidx     = 5'd0;              // beat-word index
    wire      spike_edge = spike & ~spike_d;

    always @(posedge clk) begin
        if (reset) begin
            spike_d  <= 1'b0;
            active_r <= 1'b0;
            bidx     <= 5'd0;
        end else begin
            spike_d <= spike;
            if (spike_edge) begin
                active_r <= 1'b1;
                bidx     <= 5'd0;
            end else if (active_r) begin
                if (bidx + 5'd1 >= nb) begin
                    active_r <= 1'b0;
                    bidx     <= 5'd0;
                end else begin
                    bidx <= bidx + 5'd1;
                end
            end
        end
    end

    assign active   = active_r;
    assign dac_word = active_r ? bw[bidx[3:0]] : 64'd0;

endmodule
