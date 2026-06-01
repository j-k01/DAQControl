`timescale 1ns/1ps

module adc_bram_capture #(
    parameter integer BRAM_DEPTH_WORDS = 262144
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        start,
    input  wire [1:0]  source_select,
    input  wire        data_valid,
    input  wire [31:0] sample_a_low,
    input  wire [31:0] sample_a_high,
    input  wire [31:0] sample_b_low,
    input  wire [31:0] sample_b_high,

    output wire [31:0] bram_addr,
    output wire        bram_clk,
    output wire [31:0] bram_din,
    input  wire [31:0] bram_dout,
    output wire        bram_en,
    output wire        bram_rst,
    output wire [3:0]  bram_we,

    output wire [31:0] status
);

    localparam integer ADDR_W = $clog2(BRAM_DEPTH_WORDS);
    localparam [ADDR_W:0] CAPTURE_WORDS = BRAM_DEPTH_WORDS;

    localparam [1:0] S_IDLE      = 2'd0;
    localparam [1:0] S_CAPTURING = 2'd1;
    localparam [1:0] S_DONE      = 2'd2;

    reg [1:0]        state = S_IDLE;
    reg [1:0]        source_latched = 2'd0;
    reg [ADDR_W-1:0] addr_count = {ADDR_W{1'b0}};
    reg [ADDR_W:0]   captured_count = {(ADDR_W+1){1'b0}};

    reg [31:0] sample_word;
    always @* begin
        case (source_latched)
            2'd0: sample_word = sample_a_low;
            2'd1: sample_word = sample_a_high;
            2'd2: sample_word = sample_b_low;
            default: sample_word = sample_b_high;
        endcase
    end

    wire capture_write = (state == S_CAPTURING) & data_valid;
    wire capture_last = captured_count == (CAPTURE_WORDS - 1'b1);

    assign bram_clk  = clk;
    assign bram_rst  = 1'b0;
    assign bram_addr = {{(30-ADDR_W){1'b0}}, addr_count, 2'b00};
    assign bram_din  = sample_word;
    assign bram_en   = capture_write;
    assign bram_we   = {4{capture_write}};

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            source_latched <= 2'd0;
            addr_count <= {ADDR_W{1'b0}};
            captured_count <= {(ADDR_W+1){1'b0}};
        end else if (start) begin
            state <= S_CAPTURING;
            source_latched <= source_select;
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
                            captured_count <= CAPTURE_WORDS;
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
        source_latched,
        state == S_DONE,
        state == S_CAPTURING,
        captured_count[ADDR_W-1:0]
    };

    wire unused = ^bram_dout;

endmodule
