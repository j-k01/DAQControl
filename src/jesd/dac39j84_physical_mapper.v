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

    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : gen_sample
            wire [15:0] out0 = dac_out0[(16*i) +: 16];
            wire [15:0] out1 = dac_out1[(16*i) +: 16];
            wire [15:0] out2 = dac_out2[(16*i) +: 16];
            wire [15:0] out3 = dac_out3[(16*i) +: 16];

            reg [7:0] lane0;
            reg [7:0] lane1;
            reg [7:0] lane2;
            reg [7:0] lane3;
            reg [7:0] lane4;
            reg [7:0] lane5;
            reg [7:0] lane6;
            reg [7:0] lane7;

            always @(*) begin
                case (map_mode[1:0])
                2'd1: begin
                    // Swap physical output labels within the first DAC pair.
                    lane0 = out1[7:0];
                    lane1 = out0[7:0];
                    lane2 = out0[15:8];
                    lane3 = out1[15:8];
                end
                2'd2: begin
                    // Native LiteJESD pair ordering for comparison.
                    lane0 = out0[15:8];
                    lane1 = out0[7:0];
                    lane2 = out1[15:8];
                    lane3 = out1[7:0];
                end
                2'd3: begin
                    // Sundance lane placement with byte orientation flipped.
                    lane0 = out0[15:8];
                    lane1 = out1[15:8];
                    lane2 = out1[7:0];
                    lane3 = out0[7:0];
                end
                default: begin
                    // Sundance lane placement for physical outputs 0/1:
                    // out0 on lanes 3/0 and out1 on lanes 2/1.
                    lane0 = out0[7:0];
                    lane1 = out1[7:0];
                    lane2 = out1[15:8];
                    lane3 = out0[15:8];
                end
                endcase

                case (map_mode[3:2])
                2'd1: begin
                    // Swap physical output labels within the second DAC pair.
                    lane4 = out2[7:0];
                    lane5 = out2[15:8];
                    lane6 = out3[7:0];
                    lane7 = out3[15:8];
                end
                2'd2: begin
                    // Native LiteJESD pair ordering for comparison.
                    lane4 = out2[15:8];
                    lane5 = out2[7:0];
                    lane6 = out3[15:8];
                    lane7 = out3[7:0];
                end
                2'd3: begin
                    // Sundance lane placement with byte orientation flipped.
                    lane4 = out3[15:8];
                    lane5 = out3[7:0];
                    lane6 = out2[15:8];
                    lane7 = out2[7:0];
                end
                default: begin
                    // Sundance lane placement for physical outputs 2/3:
                    // out2 on lanes 7/6 and out3 on lanes 5/4.
                    lane4 = out3[7:0];
                    lane5 = out3[15:8];
                    lane6 = out2[7:0];
                    lane7 = out2[15:8];
                end
                endcase
            end

            // LiteJESD converter0 emits lane0=high byte and lane1=low byte.
            assign converter0[(16*i) +: 16] = {lane0, lane1};
            assign converter1[(16*i) +: 16] = {lane2, lane3};
            assign converter2[(16*i) +: 16] = {lane4, lane5};
            assign converter3[(16*i) +: 16] = {lane6, lane7};
        end
    endgenerate

endmodule
