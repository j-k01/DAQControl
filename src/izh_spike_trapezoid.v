`timescale 1ns/1ps

module izh_spike_trapezoid #(
    parameter [15:0] PULSE_AMPLITUDE = 16'h6000
) (
    input  wire        clk,
    input  wire        reset,
    input  wire        spike,
    output wire        active,
    output wire [15:0] dac_sample,
    output wire [63:0] dac_word
);

    localparam [3:0] PULSE_SAMPLES = 4'd7;

    reg       spike_d = 1'b0;
    reg       active_r = 1'b0;
    reg [3:0] sample_index = 4'd0;

    wire spike_edge = spike & ~spike_d;

    function [15:0] pulse_sample;
        input [3:0] index;
        begin
            case (index)
            4'd0: pulse_sample = PULSE_AMPLITUDE >> 2;
            4'd1: pulse_sample = PULSE_AMPLITUDE >> 1;
            4'd2: pulse_sample = PULSE_AMPLITUDE;
            4'd3: pulse_sample = PULSE_AMPLITUDE;
            4'd4: pulse_sample = PULSE_AMPLITUDE;
            4'd5: pulse_sample = PULSE_AMPLITUDE >> 1;
            4'd6: pulse_sample = PULSE_AMPLITUDE >> 2;
            default: pulse_sample = 16'd0;
            endcase
        end
    endfunction

    always @(posedge clk) begin
        if (reset) begin
            spike_d <= 1'b0;
            active_r <= 1'b0;
            sample_index <= 4'd0;
        end else begin
            spike_d <= spike;

            if (spike_edge) begin
                active_r <= 1'b1;
                sample_index <= 4'd0;
            end else if (active_r) begin
                if ((sample_index + 4'd4) >= PULSE_SAMPLES) begin
                    active_r <= 1'b0;
                    sample_index <= 4'd0;
                end else begin
                    sample_index <= sample_index + 4'd4;
                end
            end
        end
    end

    assign active = active_r;
    assign dac_sample = active_r ? pulse_sample(sample_index) : 16'd0;
    assign dac_word = active_r ? {
        pulse_sample(sample_index + 4'd3),
        pulse_sample(sample_index + 4'd2),
        pulse_sample(sample_index + 4'd1),
        pulse_sample(sample_index)
    } : 64'd0;

endmodule
