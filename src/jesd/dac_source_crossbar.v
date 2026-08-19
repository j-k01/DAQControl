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
    input  wire [3:0]       invert,    // post-route polarity, bit n -> DACn

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

    function [15:0] negate_sat16;
        input [15:0] sample;
        begin
            // Two's-complement -32768 has no positive s16 representation.
            negate_sat16 = (sample == 16'h8000) ?
                           16'h7FFF : (~sample + 16'd1);
        end
    endfunction

    function [63:0] apply_invert;
        input [63:0] word_in;
        input        invert_in;
        begin
            if (invert_in) begin
                apply_invert[15:0]  = negate_sat16(word_in[15:0]);
                apply_invert[31:16] = negate_sat16(word_in[31:16]);
                apply_invert[47:32] = negate_sat16(word_in[47:32]);
                apply_invert[63:48] = negate_sat16(word_in[63:48]);
            end else begin
                apply_invert = word_in;
            end
        end
    endfunction

    assign dac0_word = apply_invert(src[sel[ 3:0]], invert[0]);
    assign dac1_word = apply_invert(src[sel[ 7:4]], invert[1]);
    assign dac2_word = apply_invert(src[sel[11:8]], invert[2]);
    assign dac3_word = apply_invert(src[sel[15:12]], invert[3]);

endmodule
