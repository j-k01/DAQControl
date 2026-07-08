`timescale 1ns/1ps

// 32:4 DAC source crossbar.  Each of the four DAC channels independently routes
// any of up to 32 source words (64-bit = 4 samples x 16b) via its own 5-bit
// select.  (Was 16:4 / 4-bit; widened to make room for the conductance-neuron
// spike sources without disturbing the original 0..15 assignments.)
//
// Source index map (the broadcast DDS is a SINGLE entry any DAC can pick):
//   0      : off (zero)
//   1      : DDS (broadcast sine)
//   2..5   : BRAM channel 0..3
//   6..9   : neuron spike pulse 0..3            (current-model neurons)
//   10..13 : neuron current monitor 0..3
//   14     : debug tag word
//   15     : injected current source (pure i_external)
//   16..19 : conductance-neuron spike pulse 0..3
//   20..31 : unused (driven to 0/off by the wrapper)
//
// sel layout: sel[4:0] -> DAC0, sel[9:5] -> DAC1, sel[14:10] -> DAC2,
//             sel[19:15] -> DAC3.  Pure routing -- one 32:1 mux per DAC.

module dac_source_crossbar (
    input  wire [32*64-1:0] sources,   // {src31, ..., src0}, src[i] at [i*64 +: 64]
    input  wire [19:0]      sel,

    output wire [63:0]      dac0_word,
    output wire [63:0]      dac1_word,
    output wire [63:0]      dac2_word,
    output wire [63:0]      dac3_word
);
    // Unpack the flat bus into a 32-entry array, then index it per DAC.  This is
    // the same 32:1 mux as a `sources[sel*64 +: 64]` part-select but uses plain
    // array indexing -- equally synthesizable and portable across simulators.
    wire [63:0] src [0:31];
    genvar gi;
    generate
        for (gi = 0; gi < 32; gi = gi + 1) begin : g_unpack
            assign src[gi] = sources[gi*64 +: 64];
        end
    endgenerate

    assign dac0_word = src[sel[ 4: 0]];
    assign dac1_word = src[sel[ 9: 5]];
    assign dac2_word = src[sel[14:10]];
    assign dac3_word = src[sel[19:15]];

endmodule
