`timescale 1ns/1ps

// 16:4 DAC source crossbar.  Each of the four DAC channels independently routes
// any of 16 source words (64-bit = 4 samples x 16b) via its own 4-bit select.
//
// Source index map (the broadcast DDS is a SINGLE entry any DAC can pick):
//   0      : off (zero)
//   1      : DDS (broadcast sine)
//   2..5   : BRAM channel 0..3
//   6..9   : neuron spike pulse 0..3
//   10..13 : neuron current monitor 0..3
//   14     : debug tag word
//   15     : injected current source (pure i_external)
//
// sel layout: sel[3:0] -> DAC0, sel[7:4] -> DAC1, sel[11:8] -> DAC2,
//             sel[15:12] -> DAC3.  Pure routing -- one 16:1 mux per DAC.

module dac_source_crossbar (
    input  wire [16*64-1:0] sources,   // {src15, ..., src0}, src[i] at [i*64 +: 64]
    input  wire [15:0]      sel,

    output wire [63:0]      dac0_word,
    output wire [63:0]      dac1_word,
    output wire [63:0]      dac2_word,
    output wire [63:0]      dac3_word
);
    // Unpack the flat bus into a 16-entry array, then index it per DAC.  This is
    // the same 16:1 mux as a `sources[sel*64 +: 64]` part-select but uses plain
    // array indexing -- equally synthesizable and portable across simulators.
    wire [63:0] src [0:15];
    genvar gi;
    generate
        for (gi = 0; gi < 16; gi = gi + 1) begin : g_unpack
            assign src[gi] = sources[gi*64 +: 64];
        end
    endgenerate

    assign dac0_word = src[sel[ 3:0]];
    assign dac1_word = src[sel[ 7:4]];
    assign dac2_word = src[sel[11:8]];
    assign dac3_word = src[sel[15:12]];

endmodule
