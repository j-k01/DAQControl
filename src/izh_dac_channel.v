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
    input  wire [23:0]        update_period,

    output wire               spike,
    output wire signed [31:0] v_out,
    output wire signed [31:0] u_out,
    output wire [15:0]        dac_sample,
    output wire [63:0]        dac_word
);

    wire signed [31:0] neuron_v;
    wire signed [31:0] neuron_u;
    wire               neuron_spike;
    wire [15:0]        spike_dac_sample;
    wire [63:0]        spike_dac_word;
    reg [23:0]         update_counter = 24'd0;

    wire [23:0] update_reload = (update_period <= 24'd1) ? 24'd0 :
                                 (update_period - 1'b1);
    wire step_enable = (update_counter == 24'd0);

    always @(posedge clk) begin
        if (reset) begin
            update_counter <= 24'd0;
        end else if (update_reload == 24'd0) begin
            update_counter <= 24'd0;
        end else if (update_counter == update_reload) begin
            update_counter <= 24'd0;
        end else begin
            update_counter <= update_counter + 1'b1;
        end
    end

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
        .step_enable(step_enable),
        .SPIKE      (neuron_spike),
        .v_out      (neuron_v),
        .u_out      (neuron_u)
    );

    izh_spike_trapezoid u_izh_spike_trapezoid (
        .clk        (clk),
        .reset      (reset),
        .spike      (neuron_spike),
        .active     (),
        .dac_sample (spike_dac_sample),
        .dac_word   (spike_dac_word)
    );

    assign spike = neuron_spike;
    assign v_out = neuron_v;
    assign u_out = neuron_u;
    assign dac_sample = spike_dac_sample;
    assign dac_word = spike_dac_word;

endmodule
