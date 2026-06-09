`timescale 1ns/1ps

module adc_bram_capture #(
    parameter integer CAPTURE_FRAMES = 4096
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        start,
    input  wire        data_valid,
    input  wire [31:0] ch0_low,
    input  wire [31:0] ch0_high,
    input  wire [31:0] ch1_low,
    input  wire [31:0] ch1_high,
    input  wire [31:0] ch2_low,
    input  wire [31:0] ch2_high,
    input  wire [31:0] ch3_low,
    input  wire [31:0] ch3_high,

    output wire [31:0] bram_addr,
    output wire        bram_clk,
    output wire [255:0] bram_din,
    input  wire [255:0] bram_dout,
    output wire        bram_en,
    output wire        bram_rst,
    output wire [31:0] bram_we,

    output wire [31:0] status
);

    localparam integer ADDR_W = $clog2(CAPTURE_FRAMES);
    localparam [ADDR_W:0] CAPTURE_LIMIT = CAPTURE_FRAMES;

    localparam [1:0] S_IDLE      = 2'd0;
    localparam [1:0] S_CAPTURING = 2'd1;
    localparam [1:0] S_DONE      = 2'd2;

    reg [1:0]        state = S_IDLE;
    reg [ADDR_W-1:0] addr_count = {ADDR_W{1'b0}};
    reg [ADDR_W:0]   captured_count = {(ADDR_W+1){1'b0}};

    wire capture_write = (state == S_CAPTURING) & data_valid;
    wire capture_last = captured_count == (CAPTURE_LIMIT - 1'b1);

    assign bram_clk  = clk;
    assign bram_rst  = 1'b0;
    assign bram_addr = {{(27-ADDR_W){1'b0}}, addr_count, 5'b00000};
    assign bram_din  = {
        ch3_high,
        ch3_low,
        ch2_high,
        ch2_low,
        ch1_high,
        ch1_low,
        ch0_high,
        ch0_low
    };
    assign bram_en   = capture_write;
    assign bram_we   = {32{capture_write}};
    wire [15:0] status_count = captured_count;

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            addr_count <= {ADDR_W{1'b0}};
            captured_count <= {(ADDR_W+1){1'b0}};
        end else if (start) begin
            state <= S_CAPTURING;
            addr_count <= {ADDR_W{1'b0}};
            captured_count <= {(ADDR_W+1){1'b0}};
        end else begin
            case (state)
                S_IDLE: begin
                    addr_count <= {ADDR_W{1'b0}};
                    captured_count <= {(ADDR_W+1){1'b0}};
                end

                S_CAPTURING: begin
                    if (data_valid) begin
                        if (capture_last) begin
                            state <= S_DONE;
                            captured_count <= CAPTURE_LIMIT;
                        end else begin
                            addr_count <= addr_count + 1'b1;
                            captured_count <= captured_count + 1'b1;
                        end
                    end
                end

                default: begin
                    state <= S_DONE;
                end
            endcase
        end
    end

    assign status = {
        8'hC4,
        state,
        2'd0,
        state == S_DONE,
        state == S_CAPTURING,
        2'd0,
        status_count
    };

    wire unused = ^bram_dout;

endmodule
