`timescale 1ns/1ps

module dac39j84_physical_mapper (
    input  wire [63:0] dac_out0,
    input  wire [63:0] dac_out1,
    input  wire [63:0] dac_out2,
    input  wire [63:0] dac_out3,
    input  wire [3:0]  map_mode,
    output wire [63:0] converter0,
    output wire [63:0] converter1,
    output wire [63:0] converter2,
    output wire [63:0] converter3
);

    // Public contract:
    //   dac_out0..3 are complete user-visible DAC streams.
    //   Each stream is four chronological 16-bit samples, sample0 in [15:0].
    //
    // This module is intentionally limited to whole-stream ordering. It must
    // never split a stream into bytes or halfwords: the generated LiteJESD
    // converter ports are sample streams, not byte-lane containers. Board lane
    // permutations belong after LiteJESD, where TXDATA and TXCHARISK are moved
    // together.
    //
    // map_mode[1:0] is retained for connector-label diagnostics only:
    //   0 = direct  out0,out1,out2,out3 -> converter0..3
    //   1 = reverse out3,out2,out1,out0 -> converter0..3
    //   2 = swap lower pair: out1,out0,out2,out3
    //   3 = swap upper pair: out0,out1,out3,out2
    // map_mode[3:2] is ignored by design so diagnostics cannot silently turn
    // this back into a byte-lane preimage.

    reg [63:0] converter0_r;
    reg [63:0] converter1_r;
    reg [63:0] converter2_r;
    reg [63:0] converter3_r;

    always @(*) begin
        case (map_mode[1:0])
        2'd1: begin
            converter0_r = dac_out3;
            converter1_r = dac_out2;
            converter2_r = dac_out1;
            converter3_r = dac_out0;
        end
        2'd2: begin
            converter0_r = dac_out1;
            converter1_r = dac_out0;
            converter2_r = dac_out2;
            converter3_r = dac_out3;
        end
        2'd3: begin
            converter0_r = dac_out0;
            converter1_r = dac_out1;
            converter2_r = dac_out3;
            converter3_r = dac_out2;
        end
        default: begin
            converter0_r = dac_out0;
            converter1_r = dac_out1;
            converter2_r = dac_out2;
            converter3_r = dac_out3;
        end
        endcase
    end

    assign converter0 = converter0_r;
    assign converter1 = converter1_r;
    assign converter2 = converter2_r;
    assign converter3 = converter3_r;

endmodule
