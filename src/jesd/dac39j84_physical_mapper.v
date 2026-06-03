`timescale 1ns/1ps

module dac39j84_physical_mapper (
    input  wire [63:0] dac_out0,
    input  wire [63:0] dac_out1,
    input  wire [63:0] dac_out2,
    input  wire [63:0] dac_out3,
    output wire [63:0] converter0,
    output wire [63:0] converter1,
    output wire [63:0] converter2,
    output wire [63:0] converter3
);

    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : gen_sample
            wire [15:0] out0 = dac_out0[(16*i) +: 16];
            wire [15:0] out1 = dac_out1[(16*i) +: 16];
            wire [15:0] out2 = dac_out2[(16*i) +: 16];
            wire [15:0] out3 = dac_out3[(16*i) +: 16];

            // LiteJESD converter0 emits lane0=high byte and lane1=low byte.
            // Sundance's DAC adapter places physical outputs across byte lanes:
            // out0 on lanes 3/0, out1 on lanes 2/1, out2 on lanes 7/6,
            // and out3 on lanes 5/4.
            assign converter0[(16*i) +: 16] = {out0[7:0],  out1[7:0]};
            assign converter1[(16*i) +: 16] = {out1[15:8], out0[15:8]};
            assign converter2[(16*i) +: 16] = {out3[7:0],  out3[15:8]};
            assign converter3[(16*i) +: 16] = {out2[7:0],  out2[15:8]};
        end
    endgenerate

endmodule
