`timescale 1ns/1ps

module dataplane_bram_ip (
    input  wire [31:0]  dac0_axi_addr,
    input  wire         dac0_axi_clk,
    input  wire [31:0]  dac0_axi_din,
    output wire [31:0]  dac0_axi_dout,
    input  wire         dac0_axi_en,
    input  wire         dac0_axi_rst,
    input  wire [3:0]   dac0_axi_we,
    input  wire [31:0]  dac0_fabric_addr,
    input  wire         dac0_fabric_clk,
    input  wire [63:0]  dac0_fabric_din,
    output wire [63:0]  dac0_fabric_dout,
    input  wire         dac0_fabric_en,
    input  wire         dac0_fabric_rst,
    input  wire [7:0]   dac0_fabric_we,

    input  wire [31:0]  dac1_axi_addr,
    input  wire         dac1_axi_clk,
    input  wire [31:0]  dac1_axi_din,
    output wire [31:0]  dac1_axi_dout,
    input  wire         dac1_axi_en,
    input  wire         dac1_axi_rst,
    input  wire [3:0]   dac1_axi_we,
    input  wire [31:0]  dac1_fabric_addr,
    input  wire         dac1_fabric_clk,
    input  wire [63:0]  dac1_fabric_din,
    output wire [63:0]  dac1_fabric_dout,
    input  wire         dac1_fabric_en,
    input  wire         dac1_fabric_rst,
    input  wire [7:0]   dac1_fabric_we,

    input  wire [31:0]  dac2_axi_addr,
    input  wire         dac2_axi_clk,
    input  wire [31:0]  dac2_axi_din,
    output wire [31:0]  dac2_axi_dout,
    input  wire         dac2_axi_en,
    input  wire         dac2_axi_rst,
    input  wire [3:0]   dac2_axi_we,
    input  wire [31:0]  dac2_fabric_addr,
    input  wire         dac2_fabric_clk,
    input  wire [63:0]  dac2_fabric_din,
    output wire [63:0]  dac2_fabric_dout,
    input  wire         dac2_fabric_en,
    input  wire         dac2_fabric_rst,
    input  wire [7:0]   dac2_fabric_we,

    input  wire [31:0]  dac3_axi_addr,
    input  wire         dac3_axi_clk,
    input  wire [31:0]  dac3_axi_din,
    output wire [31:0]  dac3_axi_dout,
    input  wire         dac3_axi_en,
    input  wire         dac3_axi_rst,
    input  wire [3:0]   dac3_axi_we,
    input  wire [31:0]  dac3_fabric_addr,
    input  wire         dac3_fabric_clk,
    input  wire [63:0]  dac3_fabric_din,
    output wire [63:0]  dac3_fabric_dout,
    input  wire         dac3_fabric_en,
    input  wire         dac3_fabric_rst,
    input  wire [7:0]   dac3_fabric_we,

    input  wire [31:0]  adc_axi_addr,
    input  wire         adc_axi_clk,
    input  wire [31:0]  adc_axi_din,
    output wire [31:0]  adc_axi_dout,
    input  wire         adc_axi_en,
    input  wire         adc_axi_rst,
    input  wire [3:0]   adc_axi_we,
    input  wire [31:0]  adc_fabric_addr,
    input  wire         adc_fabric_clk,
    input  wire [127:0] adc_fabric_din,
    output wire [127:0] adc_fabric_dout,
    input  wire         adc_fabric_en,
    input  wire         adc_fabric_rst,
    input  wire [15:0]  adc_fabric_we
);

    dac0_program_bram u_dac0_program_bram (
        .clka  (dac0_axi_clk),
        .ena   (dac0_axi_en),
        .wea   (dac0_axi_we),
        .addra (dac0_axi_addr[14:2]),
        .dina  (dac0_axi_din),
        .douta (dac0_axi_dout),
        .clkb  (dac0_fabric_clk),
        .enb   (dac0_fabric_en),
        .web   (dac0_fabric_we),
        .addrb (dac0_fabric_addr[14:3]),
        .dinb  (dac0_fabric_din),
        .doutb (dac0_fabric_dout)
    );

    dac1_program_bram u_dac1_program_bram (
        .clka  (dac1_axi_clk),
        .ena   (dac1_axi_en),
        .wea   (dac1_axi_we),
        .addra (dac1_axi_addr[14:2]),
        .dina  (dac1_axi_din),
        .douta (dac1_axi_dout),
        .clkb  (dac1_fabric_clk),
        .enb   (dac1_fabric_en),
        .web   (dac1_fabric_we),
        .addrb (dac1_fabric_addr[14:3]),
        .dinb  (dac1_fabric_din),
        .doutb (dac1_fabric_dout)
    );

    dac2_program_bram u_dac2_program_bram (
        .clka  (dac2_axi_clk),
        .ena   (dac2_axi_en),
        .wea   (dac2_axi_we),
        .addra (dac2_axi_addr[14:2]),
        .dina  (dac2_axi_din),
        .douta (dac2_axi_dout),
        .clkb  (dac2_fabric_clk),
        .enb   (dac2_fabric_en),
        .web   (dac2_fabric_we),
        .addrb (dac2_fabric_addr[14:3]),
        .dinb  (dac2_fabric_din),
        .doutb (dac2_fabric_dout)
    );

    dac3_program_bram u_dac3_program_bram (
        .clka  (dac3_axi_clk),
        .ena   (dac3_axi_en),
        .wea   (dac3_axi_we),
        .addra (dac3_axi_addr[14:2]),
        .dina  (dac3_axi_din),
        .douta (dac3_axi_dout),
        .clkb  (dac3_fabric_clk),
        .enb   (dac3_fabric_en),
        .web   (dac3_fabric_we),
        .addrb (dac3_fabric_addr[14:3]),
        .dinb  (dac3_fabric_din),
        .doutb (dac3_fabric_dout)
    );

    adc_capture_bram u_adc_capture_bram (
        .clka  (adc_axi_clk),
        .ena   (adc_axi_en),
        .wea   (adc_axi_we),
        .addra (adc_axi_addr[15:2]),
        .dina  (adc_axi_din),
        .douta (adc_axi_dout),
        .clkb  (adc_fabric_clk),
        .enb   (adc_fabric_en),
        .web   (adc_fabric_we),
        .addrb (adc_fabric_addr[15:4]),
        .dinb  (adc_fabric_din),
        .doutb (adc_fabric_dout)
    );

    wire unused = dac0_axi_rst ^ dac0_fabric_rst ^
                  dac1_axi_rst ^ dac1_fabric_rst ^
                  dac2_axi_rst ^ dac2_fabric_rst ^
                  dac3_axi_rst ^ dac3_fabric_rst ^
                  adc_axi_rst ^ adc_fabric_rst;
endmodule
