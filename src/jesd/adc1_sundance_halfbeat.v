`timescale 1ns/1ps

module adc1_sundance_halfbeat #(
    parameter REVERSE_BYTES = 0,
    parameter SWAP_SAMPLE_BYTES = 0
) (
    input  wire [31:0] lane0,
    input  wire [31:0] lane1,
    input  wire [31:0] lane2,
    input  wire [31:0] lane3,

    output wire [63:0] adc1_ch1,
    output wire [63:0] adc1_ch2
);

    function [7:0] pick_byte;
        input [31:0] lane;
        input [1:0]  index;
        reg   [1:0]  physical_index;
        begin
            physical_index = REVERSE_BYTES ? (2'd3 - index) : index;
            case (physical_index)
            2'd0: pick_byte = lane[7:0];
            2'd1: pick_byte = lane[15:8];
            2'd2: pick_byte = lane[23:16];
            default: pick_byte = lane[31:24];
            endcase
        end
    endfunction

    function [15:0] make_sample;
        input [7:0] high_byte;
        input [7:0] low_byte;
        begin
            make_sample = SWAP_SAMPLE_BYTES ?
                {low_byte, high_byte} :
                {high_byte, low_byte};
        end
    endfunction

    genvar sample_index;
    generate
        for (sample_index = 0; sample_index < 4; sample_index = sample_index + 1) begin : gen_samples
            localparam [1:0] BYTE_INDEX = sample_index;

            assign adc1_ch1[(16*sample_index) +: 16] =
                make_sample(pick_byte(lane0, BYTE_INDEX), pick_byte(lane1, BYTE_INDEX));

            assign adc1_ch2[(16*sample_index) +: 16] =
                make_sample(pick_byte(lane2, BYTE_INDEX), pick_byte(lane3, BYTE_INDEX));
        end
    endgenerate

endmodule
