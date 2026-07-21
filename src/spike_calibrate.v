`timescale 1ns/1ps

// Per-neuron spike-pulse calibration: y = sat16(sample * gain + offset) on
// each of the four s16 samples in a 64-bit DAC beat, so each neuron's shaped
// pulse can be trimmed to a calibrated height and DC baseline.
//
//   cal[15:0]  = gain, unsigned Q1.15 (0x8000 = 1.000x, full-scale height
//                resolution ~1 count). 0 selects UNITY so an unprogrammed
//                register passes the shape through unchanged.
//   cal[31:16] = offset, signed s16 DAC counts, applied CONTINUOUSLY -- also
//                between pulses, when the shaper emits zeros -- so it trims
//                the DAC's resting baseline (the calibration DC offset).
//
// Two register stages (DSP multiply, then scale/add/saturate) so the logic
// cone closes at the DAC beat clock; the latency is identical for all four
// neuron instances, so relative spike timing is untouched.

module spike_calibrate (
    input  wire        clk,
    input  wire        rst,
    input  wire [31:0] cal,        // {offset_s16, gain_q1_15}
    input  wire [63:0] in_word,    // 4 x s16 shaped samples per beat
    output reg  [63:0] out_word
);
    wire [15:0] gain_q1_15 = (cal[15:0] == 16'd0) ? 16'h8000 : cal[15:0];
    wire signed [15:0] offset_s16 = cal[31:16];

    // stage 1: per-lane DSP multiply, s16 x unsigned Q1.15 (as 17-bit signed)
    reg signed [32:0] prod0 = 33'd0, prod1 = 33'd0,
                      prod2 = 33'd0, prod3 = 33'd0;
    reg signed [15:0] off_p = 16'd0;

    // stage 2: Q1.15 rescale + offset + symmetric s16 saturation
    function [15:0] sat_cal;
        input signed [32:0] prod;
        input signed [15:0] off;
        reg signed [19:0] sum;
        begin
            sum = (prod >>> 15) + off;
            if (sum > 20'sd32767)
                sat_cal = 16'h7FFF;
            else if (sum < -20'sd32768)
                sat_cal = 16'h8000;
            else
                sat_cal = sum[15:0];
        end
    endfunction

    always @(posedge clk) begin
        if (rst) begin
            prod0 <= 33'd0;
            prod1 <= 33'd0;
            prod2 <= 33'd0;
            prod3 <= 33'd0;
            off_p <= 16'd0;
            out_word <= 64'd0;
        end else begin
            prod0 <= $signed(in_word[15:0])  * $signed({1'b0, gain_q1_15});
            prod1 <= $signed(in_word[31:16]) * $signed({1'b0, gain_q1_15});
            prod2 <= $signed(in_word[47:32]) * $signed({1'b0, gain_q1_15});
            prod3 <= $signed(in_word[63:48]) * $signed({1'b0, gain_q1_15});
            off_p <= offset_s16;
            out_word[15:0]  <= sat_cal(prod0, off_p);
            out_word[31:16] <= sat_cal(prod1, off_p);
            out_word[47:32] <= sat_cal(prod2, off_p);
            out_word[63:48] <= sat_cal(prod3, off_p);
        end
    end

endmodule
