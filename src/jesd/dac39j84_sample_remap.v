`timescale 1ns/1ps

module dac39j84_sample_remap (
    input  wire [63:0] converter0_in,
    input  wire [63:0] converter1_in,
    input  wire [63:0] converter2_in,
    input  wire [63:0] converter3_in,
    output wire [63:0] converter0_out,
    output wire [63:0] converter1_out,
    output wire [63:0] converter2_out,
    output wire [63:0] converter3_out
);

    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : gen_sample
            wire [15:0] c0 = converter0_in[(16*i) +: 16];
            wire [15:0] c1 = converter1_in[(16*i) +: 16];
            wire [15:0] c2 = converter2_in[(16*i) +: 16];
            wire [15:0] c3 = converter3_in[(16*i) +: 16];

            assign converter0_out[(16*i) +: 16] = {c3[7:0],  c2[7:0]};
            assign converter1_out[(16*i) +: 16] = {c2[15:8], c3[15:8]};
            assign converter2_out[(16*i) +: 16] = {c0[7:0],  c0[15:8]};
            assign converter3_out[(16*i) +: 16] = {c1[7:0],  c1[15:8]};
        end
    endgenerate

endmodule
