`timescale 1ns/1ps

module izh_dac_channel (
    input  wire               clk,
    input  wire               reset,
    input  wire signed [31:0] a_param,
    input  wire signed [31:0] b_param,
    input  wire signed [31:0] c_param,
    input  wire signed [31:0] d_param,
    input  wire signed [31:0] i_param,
    input  wire signed [31:0] v_timestep,
    input  wire signed [31:0] i_constant,
    input  wire signed [31:0] v_offset,

    output wire               spike,
    output wire signed [31:0] v_out,
    output wire signed [31:0] u_out,
    output wire [15:0]        dac_sample,
    output wire [63:0]        dac_word
);

    localparam signed [31:0] V_MIN_Q16 = 32'shFFB0_0000; // -80 mV
    localparam signed [31:0] V_MAX_Q16 = 32'sh001E_0000; // +30 mV
    localparam [17:0] DAC_SCALE_NUM = 18'd152518;

    wire signed [31:0] neuron_v;
    wire signed [31:0] neuron_u;
    wire               neuron_spike;

    izh_neuron u_izh_neuron (
        .clk        (clk),
        .reset      (reset),
        .a_param    (a_param),
        .b_param    (b_param),
        .c_param    (c_param),
        .d_param    (d_param),
        .I          (i_param),
        .v_timestep (v_timestep),
        .I_constant (i_constant),
        .SPIKE      (neuron_spike),
        .v_out      (neuron_v),
        .u_out      (neuron_u)
    );

    function [15:0] q16_mv_to_dac;
        input signed [31:0] mv_q16;
        reg signed [31:0] clamped;
        reg [31:0] delta_q16;
        reg [48:0] scaled_full;
        reg [16:0] unsigned_counts;
        begin
            if (mv_q16 < V_MIN_Q16) begin
                clamped = V_MIN_Q16;
            end else if (mv_q16 > V_MAX_Q16) begin
                clamped = V_MAX_Q16;
            end else begin
                clamped = mv_q16;
            end

            delta_q16 = clamped - V_MIN_Q16;
            scaled_full = delta_q16 * DAC_SCALE_NUM;
            unsigned_counts = scaled_full[40:24];
            if (unsigned_counts > 17'h0FFFF) begin
                unsigned_counts = 17'h0FFFF;
            end

            q16_mv_to_dac = unsigned_counts[15:0] + 16'h8000;
        end
    endfunction

    wire signed [31:0] offset_v = neuron_spike ? V_MAX_Q16 : (neuron_v + v_offset);
    wire [15:0] scaled_sample = q16_mv_to_dac(offset_v);

    assign spike = neuron_spike;
    assign v_out = neuron_v;
    assign u_out = neuron_u;
    assign dac_sample = scaled_sample;
    assign dac_word = {4{scaled_sample}};

endmodule
