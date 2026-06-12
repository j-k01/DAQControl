`timescale 1ns/1ps

// 3-stage CIC decimator (N=3, M=1), serial: one input sample per in_valid,
// one output sample every DECIM input samples. Integrators run at the input
// rate; the comb section runs at the decimated rate. Output is normalized by
// OUT_SHIFT (= log2 of the total DC gain to be removed, including any upstream
// prefilter gain).
//
// ACC_W must be >= IN_W + N*ceil(log2(DECIM*M)) so the (wrapping) integrators
// and combs reconstruct the result by modular arithmetic. For N=3, DECIM=32,
// IN_W=18 that is 18 + 15 = 33; ACC_W=36 leaves margin.
module cic3_decimate #(
    parameter integer DECIM     = 32,
    parameter integer IN_W      = 18,
    parameter integer ACC_W     = 36,
    parameter integer OUT_SHIFT = 17
) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire                   in_valid,
    input  wire signed [IN_W-1:0] in_data,
    output reg                    out_valid,
    output reg  signed [15:0]     out_data
);
    localparam integer CW = (DECIM <= 1) ? 1 : $clog2(DECIM);

    reg  signed [ACC_W-1:0] i0, i1, i2;          // integrators
    reg  [CW-1:0]           cnt;
    wire                    samp = in_valid & (cnt == DECIM[CW-1:0] - 1'b1);

    wire signed [ACC_W-1:0] i0n = i0 + {{(ACC_W-IN_W){in_data[IN_W-1]}}, in_data};
    wire signed [ACC_W-1:0] i1n = i1 + i0n;
    wire signed [ACC_W-1:0] i2n = i2 + i1n;

    // Comb section at the decimated rate. cd* hold the one-sample delays.
    reg  signed [ACC_W-1:0] cd0, cd1, cd2;
    wire signed [ACC_W-1:0] s0 = i2n;            // integrator value at this decimation
    wire signed [ACC_W-1:0] y1 = s0 - cd0;
    wire signed [ACC_W-1:0] y2 = y1 - cd1;
    wire signed [ACC_W-1:0] y3 = y2 - cd2;

    always @(posedge clk) begin
        if (rst) begin
            i0 <= 0; i1 <= 0; i2 <= 0; cnt <= 0;
            cd0 <= 0; cd1 <= 0; cd2 <= 0;
            out_valid <= 1'b0; out_data <= 16'sd0;
        end else begin
            out_valid <= 1'b0;
            if (in_valid) begin
                i0 <= i0n; i1 <= i1n; i2 <= i2n;
                cnt <= samp ? {CW{1'b0}} : (cnt + 1'b1);
            end
            if (samp) begin
                cd0 <= s0; cd1 <= y1; cd2 <= y2;
                out_data  <= y3 >>> OUT_SHIFT;   // low 16 bits = normalized sample
                out_valid <= 1'b1;
            end
        end
    end
endmodule
