`timescale 1ns/1ps

module dac_source_to_converter_preimage (
    input  wire [63:0] source0,
    input  wire [63:0] source1,
    input  wire [63:0] source2,
    input  wire [63:0] source3,

    output wire [63:0] converter0,
    output wire [63:0] converter1,
    output wire [63:0] converter2,
    output wire [63:0] converter3
);

    // Source contract for every input stream:
    //   sourceN = {t3, t2, t1, t0}
    // where each t is a 16-bit DAC sample and t0 is first in time.
    //
    // Byte labels for one source word named HHGG_FFEE_DDCC_BBAA:
    //   A = source[ 7: 0], B = source[15: 8]  -> t0 = BBAA
    //   C = source[23:16], D = source[31:24]  -> t1 = DDCC
    //   E = source[39:32], F = source[47:40]  -> t2 = FFEE
    //   G = source[55:48], H = source[63:56]  -> t3 = HHGG
    //
    // The converter outputs are the byte preimage expected by the downstream
    // LiteJESD/DAC lane path. This module is intentionally just wiring, but it
    // gives the byte-lane transform a single named boundary.

    wire [7:0] s0_a = source0[ 7: 0];
    wire [7:0] s0_b = source0[15: 8];
    wire [7:0] s0_c = source0[23:16];
    wire [7:0] s0_d = source0[31:24];
    wire [7:0] s0_e = source0[39:32];
    wire [7:0] s0_f = source0[47:40];
    wire [7:0] s0_g = source0[55:48];
    wire [7:0] s0_h = source0[63:56];

    wire [7:0] s1_a = source1[ 7: 0];
    wire [7:0] s1_b = source1[15: 8];
    wire [7:0] s1_c = source1[23:16];
    wire [7:0] s1_d = source1[31:24];
    wire [7:0] s1_e = source1[39:32];
    wire [7:0] s1_f = source1[47:40];
    wire [7:0] s1_g = source1[55:48];
    wire [7:0] s1_h = source1[63:56];

    wire [7:0] s2_a = source2[ 7: 0];
    wire [7:0] s2_b = source2[15: 8];
    wire [7:0] s2_c = source2[23:16];
    wire [7:0] s2_d = source2[31:24];
    wire [7:0] s2_e = source2[39:32];
    wire [7:0] s2_f = source2[47:40];
    wire [7:0] s2_g = source2[55:48];
    wire [7:0] s2_h = source2[63:56];

    wire [7:0] s3_a = source3[ 7: 0];
    wire [7:0] s3_b = source3[15: 8];
    wire [7:0] s3_c = source3[23:16];
    wire [7:0] s3_d = source3[31:24];
    wire [7:0] s3_e = source3[39:32];
    wire [7:0] s3_f = source3[47:40];
    wire [7:0] s3_g = source3[55:48];
    wire [7:0] s3_h = source3[63:56];

    // CPU-visible 32-bit halves, for readability:
    //   converter0[31:0]  = {0D, 1C, 0B, 1A}
    //   converter0[63:32] = {0H, 1G, 0F, 1E}
    //   converter1[31:0]  = {0C, 1D, 0A, 1B}
    //   converter1[63:32] = {0G, 1H, 0E, 1F}
    //   converter2[31:0]  = {3C, 2C, 3A, 2A}
    //   converter2[63:32] = {3G, 2G, 3E, 2E}
    //   converter3[31:0]  = {2D, 3D, 2B, 3B}
    //   converter3[63:32] = {2H, 3H, 2F, 3F}

    assign converter0 = {
        s0_h, s1_g, s0_f, s1_e,
        s0_d, s1_c, s0_b, s1_a
    };

    assign converter1 = {
        s0_g, s1_h, s0_e, s1_f,
        s0_c, s1_d, s0_a, s1_b
    };

    assign converter2 = {
        s3_g, s2_g, s3_e, s2_e,
        s3_c, s2_c, s3_a, s2_a
    };

    assign converter3 = {
        s2_h, s3_h, s2_f, s3_f,
        s2_d, s3_d, s2_b, s3_b
    };

endmodule
