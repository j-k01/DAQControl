
`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/01/2025 12:38:46 AM
// Design Name: 
// Module Name: izh_neuron
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


module izh_neuron(
    input wire clk,
    input wire reset,
    input wire signed [31:0] a_param,
    input wire signed [31:0] b_param,
    input wire signed [31:0] c_param,
    input wire signed [31:0] d_param,
    input wire signed [31:0] I,
    input wire signed [31:0] v_timestep,
    input wire signed [31:0] I_constant,
    input wire step_enable,
    output reg SPIKE,
    output wire signed [31:0] v_out,
    output wire signed [31:0] u_out
    );


   


// Using Q16.16 fixed-point representation
// MSB is sign bit, 15 bits for integer, 16 bits for fraction.

// Model parameters
parameter signed [31:0] X_CONST = 32'h00000A3D; // 0.04 in Q16.16 (0.04 * 2^16)
parameter signed [31:0] Y_CONST = 32'h00050000; // 5 in Q16.16
parameter signed [31:0] W_CONST = 32'h008C0000; // 140 in Q16.16
parameter signed [31:0] Z_CONST = 32'h001E0000; // 30 in Q16.16

parameter signed [31:0] U_INIT  = 32'hFFF30000; // -13mV

reg signed [31:0] v;
reg signed [31:0] u;

assign v_out = v;
assign u_out = u;

reg signed [31:0] v_next_timestep;
reg signed [31:0] u_next_timestep;


// always @(posedge clk) begin
//     v <= v + (0.04 * v + 5) * v + 140 - u + I;
//     u <= u + a_param * (b_param * v - u);
// end

reg signed [63:0] v_intermediate_s1_mult;
reg signed [31:0] v_intermediate_s1;
reg signed [31:0] v_intermediate_s2_add;
reg signed [63:0] v_intermediate_s3_mult;
reg signed [31:0] v_intermediate_s3;

reg signed [31:0] v_intermediate_s1_branch1_sub;
reg signed [31:0] v_intermediate_s2_branch1_add;
reg signed [31:0] v_intermediate_s4_add;
reg signed [63:0] v_intermediate_s4_post_timestep_mult;
reg signed [31:0] v_intermediate_s4_post_timestep;

always @(*) begin
    v_intermediate_s1_mult = v * X_CONST;
    v_intermediate_s1 = v_intermediate_s1_mult[47:16];

    v_intermediate_s2_add = v_intermediate_s1 + Y_CONST;
    v_intermediate_s3_mult = v_intermediate_s2_add * v;
    v_intermediate_s3 = v_intermediate_s3_mult[47:16];

    v_intermediate_s1_branch1_sub = W_CONST - u;
    v_intermediate_s2_branch1_add = I + v_intermediate_s1_branch1_sub + I_constant;
    v_intermediate_s4_add = v_intermediate_s2_branch1_add + v_intermediate_s3;
    v_intermediate_s4_post_timestep_mult = v_intermediate_s4_add * v_timestep;
    v_intermediate_s4_post_timestep = v_intermediate_s4_post_timestep_mult[47:16];
    v_next_timestep = v_intermediate_s4_post_timestep + v;
end

always @(posedge clk) begin
    if (reset) begin
        v <= c_param;
    end else if (!step_enable) begin
        v <= v;
    end else if (v >= Z_CONST) begin
        v <= c_param;
    end else begin
        v <= v_next_timestep;
    end
end

reg signed [63:0] u_intermediate_s1_mult;
reg signed [31:0] u_intermediate_s1;
reg signed [31:0] u_intermediate_s2_sub;
reg signed [63:0] u_intermediate_s3_mult;
reg signed [31:0] u_intermediate_s3;
reg signed [63:0] u_intermediate_post_timestep_mult;
reg signed [31:0] u_intermediate_post_timestep;


always @(*) begin
    u_intermediate_s1_mult = v * b_param;
    u_intermediate_s1 = u_intermediate_s1_mult[47:16];

    u_intermediate_s2_sub = u_intermediate_s1 - u;
    u_intermediate_s3_mult = u_intermediate_s2_sub * a_param;
    u_intermediate_s3 = u_intermediate_s3_mult[47:16];

    u_intermediate_post_timestep_mult = u_intermediate_s3 * v_timestep;
    u_intermediate_post_timestep = u_intermediate_post_timestep_mult[47:16];
    u_next_timestep = u_intermediate_post_timestep + u;
end


always @(posedge clk) begin
    if (reset) begin
        u <= U_INIT;
    end else if (!step_enable) begin
        u <= u;
    end else if (v >= Z_CONST) begin
        u <= u + d_param;
    end else begin
        u <= u_next_timestep;
    end
end

always @(posedge clk) begin
    SPIKE <= 1'b0;
    if (step_enable && v >= Z_CONST) begin
        SPIKE <= 1'b1;
    end
end



endmodule
