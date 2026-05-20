`timescale 1ns/1ps

module clk_wiz_0 (
    input  wire clk_in1_p,
    input  wire clk_in1_n,
    output reg  clk_out1,
    output reg  clk_out2,
    output reg  locked
);
    initial begin
        clk_out1 = 1'b0;
        clk_out2 = 1'b0;
        locked = 1'b0;
        #100 locked = 1'b1;
    end

    always #2.5 clk_out1 = ~clk_out1;
    always #5.0 clk_out2 = ~clk_out2;

    wire unused = clk_in1_p ^ clk_in1_n;
endmodule

module microblaze_bd_wrapper (
    input  wire        Clk,
    input  wire        reset,
    output wire        rs232_uart_txd,
    input  wire        rs232_uart_rxd,
    output reg  [31:0] RW_REG0_0,
    output reg  [31:0] RW_REG1_0,
    output reg  [31:0] RW_REG2_0,
    output reg  [31:0] RW_REG3_0,
    input  wire [31:0] RO_REG0_IN_0,
    input  wire        RO_REG0_WE_0,
    input  wire [31:0] RO_REG1_IN_0,
    input  wire        RO_REG1_WE_0,
    input  wire [31:0] RO_REG2_IN_0,
    input  wire        RO_REG2_WE_0,
    input  wire [31:0] RO_REG3_IN_0,
    input  wire        RO_REG3_WE_0,
    input  wire [31:0] BRAM_PORTB_0_addr,
    input  wire        BRAM_PORTB_0_clk,
    input  wire [31:0] BRAM_PORTB_0_din,
    output wire [31:0] BRAM_PORTB_0_dout,
    input  wire        BRAM_PORTB_0_en,
    input  wire        BRAM_PORTB_0_rst,
    input  wire [3:0]  BRAM_PORTB_0_we,
    output wire        RO_REG0_RDINT_0,
    output wire        RO_REG1_RDINT_0,
    output wire        RO_REG2_RDINT_0,
    output wire        RO_REG3_RDINT_0
);
    initial begin
        RW_REG0_0 = 32'h0009_0000;
        RW_REG1_0 = 32'd0;
        RW_REG2_0 = 32'd0;
        RW_REG3_0 = 32'd0;
        #600 RW_REG1_0 = 32'd1;
        #600 RW_REG1_0 = 32'd2;
        #600 RW_REG1_0 = 32'd3;
    end

    assign rs232_uart_txd = 1'b1;
    assign BRAM_PORTB_0_dout = 32'd0;
    assign RO_REG0_RDINT_0 = 1'b0;
    assign RO_REG1_RDINT_0 = 1'b0;
    assign RO_REG2_RDINT_0 = 1'b0;
    assign RO_REG3_RDINT_0 = 1'b0;

    wire unused = Clk ^ reset ^ rs232_uart_rxd ^ RO_REG0_WE_0 ^ RO_REG1_WE_0 ^
                  RO_REG2_WE_0 ^ RO_REG3_WE_0 ^ BRAM_PORTB_0_clk ^
                  BRAM_PORTB_0_en ^ BRAM_PORTB_0_rst ^ ^RO_REG0_IN_0 ^
                  ^RO_REG1_IN_0 ^ ^RO_REG2_IN_0 ^ ^RO_REG3_IN_0 ^
                  ^BRAM_PORTB_0_addr ^ ^BRAM_PORTB_0_din ^ ^BRAM_PORTB_0_we;
endmodule

module IBUFDS #(
    parameter DIFF_TERM = "FALSE",
    parameter IOSTANDARD = "DEFAULT"
) (
    input  wire I,
    input  wire IB,
    output wire O
);
    assign O = I;
    wire unused = IB;
endmodule

module BUFG (
    input  wire I,
    output wire O
);
    assign O = I;
endmodule

module IBUFDS_GTE4 (
    input  wire I,
    input  wire IB,
    input  wire CEB,
    output wire O,
    output wire ODIV2
);
    assign O = CEB ? 1'b0 : I;
    assign ODIV2 = CEB ? 1'b0 : I;
    wire unused = IB;
endmodule

module BUFG_GT (
    input  wire       I,
    input  wire       CE,
    input  wire       CEMASK,
    input  wire       CLR,
    input  wire       CLRMASK,
    input  wire [2:0] DIV,
    output wire       O
);
    assign O = (CE && !CLR) ? I : 1'b0;
    wire unused = CEMASK ^ CLRMASK ^ ^DIV;
endmodule
