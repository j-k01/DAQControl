`timescale 1ns/1ps

module dac_bram_player #(
    parameter integer BRAM_DEPTH_WORDS = 262144
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        enable,
    input  wire        restart,

    output wire [31:0] bram_addr,
    output wire        bram_clk,
    output wire [31:0] bram_din,
    input  wire [31:0] bram_dout,
    output wire        bram_en,
    output wire        bram_rst,
    output wire [3:0]  bram_we,

    output reg  [31:0] program_word,
    output wire [31:0] status
);

    localparam integer ADDR_W = $clog2(BRAM_DEPTH_WORDS);
    localparam [ADDR_W-1:0] LAST_INDEX = BRAM_DEPTH_WORDS - 1;

    reg [ADDR_W-1:0] read_index = {ADDR_W{1'b0}};
    reg [ADDR_W-1:0] output_index = {ADDR_W{1'b0}};
    reg              valid = 1'b0;

    assign bram_clk = clk;
    assign bram_rst = 1'b0;
    assign bram_din = 32'd0;
    assign bram_we = 4'd0;
    assign bram_en = enable;
    assign bram_addr = {{(32-ADDR_W-2){1'b0}}, read_index, 2'b00};

    always @(posedge clk) begin
        if (rst) begin
            read_index <= {ADDR_W{1'b0}};
            output_index <= {ADDR_W{1'b0}};
            program_word <= 32'h8000_8000;
            valid <= 1'b0;
        end else if (!enable) begin
            read_index <= {ADDR_W{1'b0}};
            output_index <= {ADDR_W{1'b0}};
            program_word <= 32'h8000_8000;
            valid <= 1'b0;
        end else begin
            if (restart) begin
                read_index <= {ADDR_W{1'b0}};
                output_index <= {ADDR_W{1'b0}};
                valid <= 1'b0;
            end else begin
                if (read_index == LAST_INDEX) begin
                    read_index <= {ADDR_W{1'b0}};
                end else begin
                    read_index <= read_index + 1'b1;
                end

                if (valid) begin
                    program_word <= bram_dout;
                    if (output_index == LAST_INDEX) begin
                        output_index <= {ADDR_W{1'b0}};
                    end else begin
                        output_index <= output_index + 1'b1;
                    end
                end else begin
                    valid <= 1'b1;
                end
            end
        end
    end

    assign status = {
        8'hD4,
        5'd0,
        enable,
        valid,
        restart,
        output_index
    };

endmodule
