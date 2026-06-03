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

            reg [15:0] sd_dac2_ch2;
            reg [15:0] sd_dac2_ch1;
            reg [15:0] sd_dac1_ch2;
            reg [15:0] sd_dac1_ch1;
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
                    // User-guide OUT_A/OUT_B/OUT_C/OUT_D order:
                    // OUT_A=Channel 1=dac1_ch1, OUT_B=dac1_ch2,
                    // OUT_C=dac2_ch1, OUT_D=dac2_ch2.
                    sd_dac1_ch1 = out0;
                    sd_dac1_ch2 = out1;
                    sd_dac2_ch1 = out2;
                    sd_dac2_ch2 = out3;
                end
                2'd2: begin
                    // Swap the first Sundance pair. This directly tests the
                    // observed case where physical OUT1 appeared to be driven
                    // by the source intended for OUT0.
                    sd_dac2_ch2 = out1;
                    sd_dac2_ch1 = out0;
                    sd_dac1_ch2 = out2;
                    sd_dac1_ch1 = out3;
                end
                2'd3: begin
                    // Swap the second Sundance pair.
                    sd_dac2_ch2 = out0;
                    sd_dac2_ch1 = out1;
                    sd_dac1_ch2 = out3;
                    sd_dac1_ch1 = out2;
                end
                default: begin
                    // Exact Sundance adapter source order. Sundance's
                    // dac_ena[0..3] feed dac2_ch2, dac2_ch1, dac1_ch2,
                    // dac1_ch1 respectively.
                    sd_dac2_ch2 = out0;
                    sd_dac2_ch1 = out1;
                    sd_dac1_ch2 = out2;
                    sd_dac1_ch1 = out3;
                end
                endcase

                lane0 = sd_dac2_ch2[7:0];
                lane1 = sd_dac2_ch1[7:0];
                lane2 = sd_dac2_ch1[15:8];
                lane3 = sd_dac2_ch2[15:8];

                case (map_mode[3:2])
                2'd3: begin
                    // Byte-orientation diagnostic: keep source ownership but
                    // invert the high/low byte placement on all DAC outputs.
                    lane0 = sd_dac2_ch2[15:8];
                    lane1 = sd_dac2_ch1[15:8];
                    lane2 = sd_dac2_ch1[7:0];
                    lane3 = sd_dac2_ch2[7:0];
                    lane4 = sd_dac1_ch1[15:8];
                    lane5 = sd_dac1_ch1[7:0];
                    lane6 = sd_dac1_ch2[15:8];
                    lane7 = sd_dac1_ch2[7:0];
                end
                default: begin
                    // Exact Sundance byte-lane placement.
                    lane4 = sd_dac1_ch1[7:0];
                    lane5 = sd_dac1_ch1[15:8];
                    lane6 = sd_dac1_ch2[7:0];
                    lane7 = sd_dac1_ch2[15:8];
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
