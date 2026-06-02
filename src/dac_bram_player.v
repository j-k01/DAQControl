`timescale 1ns/1ps

module dac_bram_player #(
    parameter integer BRAM_DEPTH_WORDS = 262144
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        enable,
    input  wire        restart,
    input  wire [23:0] frame_count,

    output wire [31:0] bram_addr,
    output wire        bram_clk,
    output wire [63:0] bram_din,
    input  wire [63:0] bram_dout,
    output wire        bram_en,
    output wire        bram_rst,
    output wire [7:0]  bram_we,

    output reg  [63:0] program_word,
    output wire [31:0] status
);

    localparam integer FRAME_WORDS = BRAM_DEPTH_WORDS / 2;
    localparam integer ADDR_W = $clog2(FRAME_WORDS);
    localparam [ADDR_W-1:0] LAST_INDEX = FRAME_WORDS - 1;

    reg [ADDR_W-1:0] read_index = {ADDR_W{1'b0}};
    reg [ADDR_W-1:0] output_index = {ADDR_W{1'b0}};
    reg              valid = 1'b0;
    wire [ADDR_W-1:0] frame_count_w = frame_count[ADDR_W-1:0];
    wire [ADDR_W-1:0] runtime_last_index =
        (frame_count_w == {ADDR_W{1'b0}}) ? LAST_INDEX :
        (frame_count_w - {{(ADDR_W-1){1'b0}}, 1'b1});

    assign bram_clk = clk;
    assign bram_rst = 1'b0;
    assign bram_din = 64'd0;
    assign bram_we = 8'd0;
    assign bram_en = enable;
    assign bram_addr = {{(32-ADDR_W-3){1'b0}}, read_index, 3'b000};

    always @(posedge clk) begin
        if (rst) begin
            read_index <= {ADDR_W{1'b0}};
            output_index <= {ADDR_W{1'b0}};
            program_word <= 64'd0;
            valid <= 1'b0;
        end else if (!enable) begin
            read_index <= {ADDR_W{1'b0}};
            output_index <= {ADDR_W{1'b0}};
            program_word <= 64'd0;
            valid <= 1'b0;
        end else begin
            if (restart) begin
                read_index <= {ADDR_W{1'b0}};
                output_index <= {ADDR_W{1'b0}};
                valid <= 1'b0;
            end else begin
                if (read_index == runtime_last_index) begin
                    read_index <= {ADDR_W{1'b0}};
                end else begin
                    read_index <= read_index + 1'b1;
                end

                if (valid) begin
                    program_word <= bram_dout;
                    if (output_index == runtime_last_index) begin
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

    wire [16:0] status_index = output_index[16:0];
    assign status = {
        8'hD4,
        4'd0,
        enable,
        valid,
        restart,
        2'd0,
        status_index
    };

endmodule
