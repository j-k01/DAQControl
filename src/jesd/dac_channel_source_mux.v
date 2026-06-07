`timescale 1ns/1ps

module dac_channel_source_mux (
    input  wire [1:0]  source_mode,
    input  wire        program_enable,
    input  wire [63:0] dds_word,
    input  wire [63:0] bram_word,
    input  wire [63:0] pulse_word,
    output reg  [63:0] dac_word
);

    wire [1:0] effective_source_mode = (source_mode == 2'd0) ?
        (program_enable ? 2'd2 : 2'd1) : source_mode;

    always @(*) begin
        case (effective_source_mode)
        2'd2: begin
            dac_word = bram_word;
        end
        2'd3: begin
            dac_word = pulse_word;
        end
        default: begin
            dac_word = dds_word;
        end
        endcase
    end

endmodule
