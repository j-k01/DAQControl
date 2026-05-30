`timescale 1ns/1ps

module clk_wiz_0 (
    input  wire clk_in1_p,
    input  wire clk_in1_n,
    output reg  clk_out1,
    output reg  clk_out2,
    output reg  clk_out3,
    output reg  locked
);
    initial begin
        clk_out1 = 1'b0;
        clk_out2 = 1'b0;
        clk_out3 = 1'b0;
        locked = 1'b0;
        #100 locked = 1'b1;
    end

    always #2.5 clk_out1 = ~clk_out1;
    always #5.0 clk_out2 = ~clk_out2;
    always #4.0 clk_out3 = ~clk_out3;

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
    assign RO_REG0_RDINT_0 = 1'b0;
    assign RO_REG1_RDINT_0 = 1'b0;
    assign RO_REG2_RDINT_0 = 1'b0;
    assign RO_REG3_RDINT_0 = 1'b0;

    wire unused = Clk ^ reset ^ rs232_uart_rxd ^ RO_REG0_WE_0 ^ RO_REG1_WE_0 ^
                  RO_REG2_WE_0 ^ RO_REG3_WE_0 ^ ^RO_REG0_IN_0 ^
                  ^RO_REG1_IN_0 ^ ^RO_REG2_IN_0 ^ ^RO_REG3_IN_0;
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

module FDPE #(
    parameter INIT = 1'b1
) (
    output reg  Q,
    input  wire C,
    input  wire CE,
    input  wire PRE,
    input  wire D
);
    initial Q = INIT;

    always @(posedge C or posedge PRE) begin
        if (PRE) begin
            Q <= 1'b1;
        end else if (CE) begin
            Q <= D;
        end
    end
endmodule

module gtwizard_ultrascale_0 (
    input  wire         gtwiz_userclk_tx_reset_in,
    output wire         gtwiz_userclk_tx_srcclk_out,
    output reg          gtwiz_userclk_tx_usrclk_out,
    output reg          gtwiz_userclk_tx_usrclk2_out,
    output wire         gtwiz_userclk_tx_active_out,
    input  wire         gtwiz_userclk_rx_reset_in,
    output wire         gtwiz_userclk_rx_srcclk_out,
    output reg          gtwiz_userclk_rx_usrclk_out,
    output reg          gtwiz_userclk_rx_usrclk2_out,
    output wire         gtwiz_userclk_rx_active_out,
    input  wire         gtwiz_reset_clk_freerun_in,
    input  wire         gtwiz_reset_all_in,
    input  wire         gtwiz_reset_tx_pll_and_datapath_in,
    input  wire         gtwiz_reset_tx_datapath_in,
    input  wire         gtwiz_reset_rx_pll_and_datapath_in,
    input  wire         gtwiz_reset_rx_datapath_in,
    output wire         gtwiz_reset_rx_cdr_stable_out,
    output wire         gtwiz_reset_tx_done_out,
    output wire         gtwiz_reset_rx_done_out,
    input  wire [255:0] gtwiz_userdata_tx_in,
    output wire [255:0] gtwiz_userdata_rx_out,
    input  wire [1:0]   gtrefclk00_in,
    output wire [1:0]   qpll0lock_out,
    output wire [1:0]   qpll0outclk_out,
    output wire [1:0]   qpll0outrefclk_out,
    input  wire [7:0]   gthrxn_in,
    input  wire [7:0]   gthrxp_in,
    input  wire [7:0]   rx8b10ben_in,
    input  wire [7:0]   rxcommadeten_in,
    input  wire [7:0]   rxmcommaalignen_in,
    input  wire [7:0]   rxpcommaalignen_in,
    input  wire [7:0]   rxpolarity_in,
    input  wire [7:0]   tx8b10ben_in,
    input  wire [127:0] txctrl0_in,
    input  wire [127:0] txctrl1_in,
    input  wire [63:0]  txctrl2_in,
    input  wire [7:0]   txpolarity_in,
    output wire [7:0]   gthtxn_out,
    output wire [7:0]   gthtxp_out,
    output wire [7:0]   gtpowergood_out,
    output wire [7:0]   rxcdrlock_out,
    output wire [7:0]   rxbyteisaligned_out,
    output wire [7:0]   rxbyterealign_out,
    output wire [7:0]   rxcommadet_out,
    output wire [127:0] rxctrl0_out,
    output wire [127:0] rxctrl1_out,
    output wire [63:0]  rxctrl2_out,
    output wire [63:0]  rxctrl3_out,
    output wire [7:0]   rxpmaresetdone_out,
    output wire [7:0]   txpmaresetdone_out
);
    initial begin
        gtwiz_userclk_tx_usrclk_out = 1'b0;
        gtwiz_userclk_tx_usrclk2_out = 1'b0;
        gtwiz_userclk_rx_usrclk_out = 1'b0;
        gtwiz_userclk_rx_usrclk2_out = 1'b0;
    end

    always #1.6 gtwiz_userclk_tx_usrclk_out = ~gtwiz_userclk_tx_usrclk_out;
    always #1.6 gtwiz_userclk_tx_usrclk2_out = ~gtwiz_userclk_tx_usrclk2_out;
    always #1.6 gtwiz_userclk_rx_usrclk_out = ~gtwiz_userclk_rx_usrclk_out;
    always #1.6 gtwiz_userclk_rx_usrclk2_out = ~gtwiz_userclk_rx_usrclk2_out;

    wire reset_any = gtwiz_reset_all_in | gtwiz_userclk_tx_reset_in |
                     gtwiz_userclk_rx_reset_in;

    assign gtwiz_userclk_tx_srcclk_out = gtwiz_userclk_tx_usrclk2_out;
    assign gtwiz_userclk_rx_srcclk_out = gtwiz_userclk_rx_usrclk2_out;
    assign gtwiz_userclk_tx_active_out = ~reset_any;
    assign gtwiz_userclk_rx_active_out = ~reset_any;
    assign gtwiz_reset_rx_cdr_stable_out = ~reset_any;
    assign gtwiz_reset_tx_done_out = ~reset_any;
    assign gtwiz_reset_rx_done_out = ~reset_any;
    assign gtwiz_userdata_rx_out = gtwiz_userdata_tx_in;
    assign qpll0lock_out = reset_any ? 2'b00 : 2'b11;
    assign qpll0outclk_out = gtrefclk00_in;
    assign qpll0outrefclk_out = gtrefclk00_in;
    assign gthtxn_out = 8'd0;
    assign gthtxp_out = 8'd0;
    assign gtpowergood_out = reset_any ? 8'd0 : 8'hff;
    assign rxcdrlock_out = reset_any ? 8'd0 : 8'hff;
    assign rxbyteisaligned_out = reset_any ? 8'd0 : 8'hff;
    assign rxbyterealign_out = 8'd0;
    assign rxcommadet_out = 8'd0;
    assign rxctrl0_out = 128'd0;
    assign rxctrl1_out = 128'd0;
    assign rxctrl2_out = 64'd0;
    assign rxctrl3_out = 64'd0;
    assign rxpmaresetdone_out = reset_any ? 8'd0 : 8'hff;
    assign txpmaresetdone_out = reset_any ? 8'd0 : 8'hff;

    wire unused = gtwiz_reset_clk_freerun_in ^ gtwiz_reset_tx_pll_and_datapath_in ^
                  gtwiz_reset_tx_datapath_in ^ gtwiz_reset_rx_pll_and_datapath_in ^
                  gtwiz_reset_rx_datapath_in ^ ^gthrxn_in ^ ^gthrxp_in ^
                  ^rx8b10ben_in ^ ^rxcommadeten_in ^ ^rxmcommaalignen_in ^
                  ^rxpcommaalignen_in ^ ^rxpolarity_in ^ ^tx8b10ben_in ^
                  ^txctrl0_in ^ ^txctrl1_in ^ ^txctrl2_in ^ ^txpolarity_in;
endmodule

module ila_fabric_debug (
    input  wire        clk,
    input  wire [31:0] probe0,
    input  wire [31:0] probe1,
    input  wire [31:0] probe2,
    input  wire [31:0] probe3,
    input  wire [31:0] probe4,
    input  wire [31:0] probe5,
    input  wire [31:0] probe6,
    input  wire [31:0] probe7,
    input  wire [31:0] probe8,
    input  wire [31:0] probe9,
    input  wire [31:0] probe10,
    input  wire [31:0] probe11,
    input  wire [31:0] probe12,
    input  wire [31:0] probe13,
    input  wire [31:0] probe14,
    input  wire [31:0] probe15,
    input  wire [31:0] probe16
);
    wire unused = clk ^ ^probe0 ^ ^probe1 ^ ^probe2 ^ ^probe3 ^ ^probe4 ^
                  ^probe5 ^ ^probe6 ^ ^probe7 ^ ^probe8 ^ ^probe9 ^
                  ^probe10 ^ ^probe11 ^ ^probe12 ^ ^probe13 ^ ^probe14 ^
                  ^probe15 ^ ^probe16;
endmodule

module ila_gth_tx_debug (
    input  wire        clk,
    input  wire [31:0] probe0,
    input  wire [31:0] probe1,
    input  wire [63:0] probe2,
    input  wire [31:0] probe3,
    input  wire [31:0] probe4,
    input  wire [31:0] probe5
);
    wire unused = clk ^ ^probe0 ^ ^probe1 ^ ^probe2 ^ ^probe3 ^
                  ^probe4 ^ ^probe5;
endmodule
