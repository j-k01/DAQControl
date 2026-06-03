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
            reg [7:0] p0;
            reg [7:0] p1;
            reg [7:0] p2;
            reg [7:0] p3;
            reg [7:0] p4;
            reg [7:0] p5;
            reg [7:0] p6;
            reg [7:0] p7;
            reg [7:0] l0;
            reg [7:0] l1;
            reg [7:0] l2;
            reg [7:0] l3;
            reg [7:0] l4;
            reg [7:0] l5;
            reg [7:0] l6;
            reg [7:0] l7;

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

                // Hypothesis B: Sundance's adapter builds core-lane bytes
                // first:
                //   p0..p3 are DAC1_DATA_o lanes 0..3 (DP4..DP7)
                //   p4..p7 are DAC2_DATA_o lanes 0..3 (DP0..DP3)
                //
                // These lane bytes are a Sundance-core-lane preimage to test,
                // not a statement that the DAC39J84 itself requires
                // non-adjacent logical byte pairs. The generated LiteJESD core
                // converts converterN[15:0] into logical lane bytes as
                // {L(2N), L(2N+1)}.
                p0 = sd_dac2_ch2[7:0];
                p1 = sd_dac2_ch1[7:0];
                p2 = sd_dac2_ch1[15:8];
                p3 = sd_dac2_ch2[15:8];

                case (map_mode[3:2])
                2'd3: begin
                    // Byte-orientation diagnostic: keep source ownership but
                    // invert the high/low byte placement on all DAC outputs.
                    p0 = sd_dac2_ch2[15:8];
                    p1 = sd_dac2_ch1[15:8];
                    p2 = sd_dac2_ch1[7:0];
                    p3 = sd_dac2_ch2[7:0];
                    p4 = sd_dac1_ch1[15:8];
                    p5 = sd_dac1_ch1[7:0];
                    p6 = sd_dac1_ch2[15:8];
                    p7 = sd_dac1_ch2[7:0];
                end
                default: begin
                    // Exact Sundance byte-lane placement.
                    p4 = sd_dac1_ch1[7:0];
                    p5 = sd_dac1_ch1[15:8];
                    p6 = sd_dac1_ch2[7:0];
                    p7 = sd_dac1_ch2[15:8];
                end
                endcase

                case (map_mode[3:2])
                2'd1: begin
                    // Physical-lane diagnostic. Use with tx_lane_mode=0 to
                    // reproduce Sundance's physical GTH lane byte placement.
                    l0 = p0;
                    l1 = p1;
                    l2 = p2;
                    l3 = p3;
                    l4 = p4;
                    l5 = p5;
                    l6 = p6;
                    l7 = p7;
                end
                2'd2: begin
                    // Old upper-half reverse diagnostic retained for A/B.
                    // This corresponds to tx_lane_mode=2's upper-lane order.
                    l0 = p1;
                    l1 = p3;
                    l2 = p2;
                    l3 = p0;
                    l4 = p7;
                    l5 = p6;
                    l6 = p5;
                    l7 = p4;
                end
                default: begin
                    // Sundance-core-lane preimage after the current DAC39J84
                    // config95/config96 value 0x3021/0x7654 and tx_lane_mode=3.
                    // The top-level lane mux sends physical lanes 0..7 <=
                    // logical lanes [3,0,2,1,4,5,6,7], so put each Sundance
                    // core-lane byte into the logical lane that will reach that
                    // physical/core lane.
                    l0 = p1;
                    l1 = p3;
                    l2 = p2;
                    l3 = p0;
                    l4 = p4;
                    l5 = p5;
                    l6 = p6;
                    l7 = p7;
                end
                endcase
            end

            // LiteJESD converter0 emits logical lane0=high byte and
            // logical lane1=low byte; converter1 emits lanes 2/3, etc.
            assign converter0[(16*i) +: 16] = {l0, l1};
            assign converter1[(16*i) +: 16] = {l2, l3};
            assign converter2[(16*i) +: 16] = {l4, l5};
            assign converter3[(16*i) +: 16] = {l6, l7};
        end
    endgenerate

endmodule
