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

    input  wire [31:0]  adc0_axi_addr,
    input  wire         adc0_axi_clk,
    input  wire [31:0]  adc0_axi_din,
    output wire [31:0]  adc0_axi_dout,
    input  wire         adc0_axi_en,
    input  wire         adc0_axi_rst,
    input  wire [3:0]   adc0_axi_we,
    input  wire [31:0]  adc1_axi_addr,
    input  wire         adc1_axi_clk,
    input  wire [31:0]  adc1_axi_din,
    output wire [31:0]  adc1_axi_dout,
    input  wire         adc1_axi_en,
    input  wire         adc1_axi_rst,
    input  wire [3:0]   adc1_axi_we,
    input  wire [31:0]  adc_fabric_addr,
    input  wire         adc_fabric_clk,
    input  wire [127:0] adc0_fabric_din,
    output wire [127:0] adc0_fabric_dout,
    input  wire [127:0] adc1_fabric_din,
    output wire [127:0] adc1_fabric_dout,
    input  wire         adc_fabric_en,
    input  wire         adc_fabric_rst,
    input  wire [15:0]  adc_fabric_we,

    // IZH neuron config bank: 64 x 32-bit dual-clock RAM.  Port A is the AXI
    // BRAM controller (clk_200), port B is a read-only port in the neuron
    // clk_50 domain that the bank reader walks each program pulse.
    input  wire [31:0]  neuron_cfg_axi_addr,
    input  wire         neuron_cfg_axi_clk,
    input  wire [31:0]  neuron_cfg_axi_din,
    output wire [31:0]  neuron_cfg_axi_dout,
    input  wire         neuron_cfg_axi_en,
    input  wire         neuron_cfg_axi_rst,
    input  wire [3:0]   neuron_cfg_axi_we,
    input  wire         neuron_cfg_fabric_clk,
    input  wire [5:0]   neuron_cfg_fabric_addr,
    output wire [31:0]  neuron_cfg_fabric_dout,

    // Current-source waveform: 1024 x 32-bit Q16.16 samples, dual-clock.  Port A
    // = AXI BRAM controller (clk_200, MicroBlaze loads the waveform); port B =
    // read-only in the neuron clk_50 domain (the current player reads it).
    input  wire [31:0]  cur_wave_axi_addr,
    input  wire         cur_wave_axi_clk,
    input  wire [31:0]  cur_wave_axi_din,
    output wire [31:0]  cur_wave_axi_dout,
    input  wire         cur_wave_axi_en,
    input  wire         cur_wave_axi_rst,
    input  wire [3:0]   cur_wave_axi_we,
    input  wire         cur_wave_fabric_clk,
    input  wire [9:0]   cur_wave_fabric_addr,
    output wire [31:0]  cur_wave_fabric_dout,

    // Spike pulse shape: AXI writes a shared table of signed s16 DAC samples
    // packed two per 32-bit word. Internally the table is replicated four
    // times so the four neuron-triggered shapers can read independent 64-bit
    // beat words ({s3,s2,s1,s0}) in the DAC/JESD clock domain.
    input  wire [31:0]  spike_shape_axi_addr,
    input  wire         spike_shape_axi_clk,
    input  wire [31:0]  spike_shape_axi_din,
    output wire [31:0]  spike_shape_axi_dout,
    input  wire         spike_shape_axi_en,
    input  wire         spike_shape_axi_rst,
    input  wire [3:0]   spike_shape_axi_we,
    input  wire         spike_shape_fabric_clk,
    input  wire [9:0]   spike_shape_fabric_addr0,
    output wire [63:0]  spike_shape_fabric_dout0,
    input  wire [9:0]   spike_shape_fabric_addr1,
    output wire [63:0]  spike_shape_fabric_dout1,
    input  wire [9:0]   spike_shape_fabric_addr2,
    output wire [63:0]  spike_shape_fabric_dout2,
    input  wire [9:0]   spike_shape_fabric_addr3,
    output wire [63:0]  spike_shape_fabric_dout3
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

    adc0_capture_bram u_adc0_capture_bram (
        .clka  (adc0_axi_clk),
        .ena   (adc0_axi_en),
        .wea   (adc0_axi_we),
        .addra (adc0_axi_addr[15:2]),
        .dina  (adc0_axi_din),
        .douta (adc0_axi_dout),
        .clkb  (adc_fabric_clk),
        .enb   (adc_fabric_en),
        .web   (adc_fabric_we),
        .addrb (adc_fabric_addr[15:4]),
        .dinb  (adc0_fabric_din),
        .doutb (adc0_fabric_dout)
    );

    adc1_capture_bram u_adc1_capture_bram (
        .clka  (adc1_axi_clk),
        .ena   (adc1_axi_en),
        .wea   (adc1_axi_we),
        .addra (adc1_axi_addr[15:2]),
        .dina  (adc1_axi_din),
        .douta (adc1_axi_dout),
        .clkb  (adc_fabric_clk),
        .enb   (adc_fabric_en),
        .web   (adc_fabric_we),
        .addrb (adc_fabric_addr[15:4]),
        .dinb  (adc1_fabric_din),
        .doutb (adc1_fabric_dout)
    );

    // IZH config bank.  XPM TDP RAM avoids generating/committing another BMG
    // .xci.  Port A: byte-addressed by the AXI BRAM controller -> word index =
    // addr[7:2] (64 words).  Port B: read-only word address from the bank
    // reader, 1-cycle read latency (matches the reader's S_WAIT->S_READ).
    xpm_memory_tdpram #(
        .ADDR_WIDTH_A        (6),
        .ADDR_WIDTH_B        (6),
        .BYTE_WRITE_WIDTH_A  (8),
        .BYTE_WRITE_WIDTH_B  (8),
        .CLOCKING_MODE       ("independent_clock"),
        .MEMORY_PRIMITIVE    ("block"),
        .MEMORY_SIZE         (64 * 32),
        .READ_DATA_WIDTH_A   (32),
        .READ_DATA_WIDTH_B   (32),
        .READ_LATENCY_A      (1),
        .READ_LATENCY_B      (1),
        .WRITE_DATA_WIDTH_A  (32),
        .WRITE_DATA_WIDTH_B  (32),
        .WRITE_MODE_A        ("read_first"),
        .WRITE_MODE_B        ("read_first")
    ) u_neuron_cfg_bram (
        .clka           (neuron_cfg_axi_clk),
        .ena            (neuron_cfg_axi_en),
        .wea            (neuron_cfg_axi_we),
        .addra          (neuron_cfg_axi_addr[7:2]),
        .dina           (neuron_cfg_axi_din),
        .douta          (neuron_cfg_axi_dout),
        .clkb           (neuron_cfg_fabric_clk),
        .enb            (1'b1),
        .web            (4'b0),
        .addrb          (neuron_cfg_fabric_addr),
        .dinb           (32'b0),
        .doutb          (neuron_cfg_fabric_dout),
        .rsta           (1'b0),
        .rstb           (1'b0),
        .regcea         (1'b1),
        .regceb         (1'b1),
        .sleep          (1'b0),
        .injectsbiterra (1'b0),
        .injectdbiterra (1'b0),
        .injectsbiterrb (1'b0),
        .injectdbiterrb (1'b0)
    );

    // Current-source waveform RAM (1024 x 32).  Port A byte-addressed by the AXI
    // BRAM controller -> word index = addr[11:2].  Port B read-only in the neuron
    // clk_50 domain (1-cycle latency, matches the player's read pipeline).
    xpm_memory_tdpram #(
        .ADDR_WIDTH_A        (10),
        .ADDR_WIDTH_B        (10),
        .BYTE_WRITE_WIDTH_A  (8),
        .BYTE_WRITE_WIDTH_B  (8),
        .CLOCKING_MODE       ("independent_clock"),
        .MEMORY_PRIMITIVE    ("block"),
        .MEMORY_SIZE         (1024 * 32),
        .READ_DATA_WIDTH_A   (32),
        .READ_DATA_WIDTH_B   (32),
        .READ_LATENCY_A      (1),
        .READ_LATENCY_B      (1),
        .WRITE_DATA_WIDTH_A  (32),
        .WRITE_DATA_WIDTH_B  (32),
        .WRITE_MODE_A        ("read_first"),
        .WRITE_MODE_B        ("read_first")
    ) u_cur_wave_bram (
        .clka           (cur_wave_axi_clk),
        .ena            (cur_wave_axi_en),
        .wea            (cur_wave_axi_we),
        .addra          (cur_wave_axi_addr[11:2]),
        .dina           (cur_wave_axi_din),
        .douta          (cur_wave_axi_dout),
        .clkb           (cur_wave_fabric_clk),
        .enb            (1'b1),
        .web            (4'b0),
        .addrb          (cur_wave_fabric_addr),
        .dinb           (32'b0),
        .doutb          (cur_wave_fabric_dout),
        .rsta           (1'b0),
        .rstb           (1'b0),
        .regcea         (1'b1),
        .regceb         (1'b1),
        .sleep          (1'b0),
        .injectsbiterra (1'b0),
        .injectdbiterra (1'b0),
        .injectsbiterrb (1'b0),
        .injectdbiterrb (1'b0)
    );

    spike_shape_bram_bank #(
        .ADDR_W (10)
    ) u_spike_shape_bram_bank (
        .axi_addr      (spike_shape_axi_addr),
        .axi_clk       (spike_shape_axi_clk),
        .axi_din       (spike_shape_axi_din),
        .axi_dout      (spike_shape_axi_dout),
        .axi_en        (spike_shape_axi_en),
        .axi_we        (spike_shape_axi_we),
        .fabric_clk    (spike_shape_fabric_clk),
        .fabric_addr0  (spike_shape_fabric_addr0),
        .fabric_dout0  (spike_shape_fabric_dout0),
        .fabric_addr1  (spike_shape_fabric_addr1),
        .fabric_dout1  (spike_shape_fabric_dout1),
        .fabric_addr2  (spike_shape_fabric_addr2),
        .fabric_dout2  (spike_shape_fabric_dout2),
        .fabric_addr3  (spike_shape_fabric_addr3),
        .fabric_dout3  (spike_shape_fabric_dout3)
    );

    wire unused = dac0_axi_rst ^ dac0_fabric_rst ^
                  dac1_axi_rst ^ dac1_fabric_rst ^
                  dac2_axi_rst ^ dac2_fabric_rst ^
                  dac3_axi_rst ^ dac3_fabric_rst ^
                  adc0_axi_rst ^ adc1_axi_rst ^ adc_fabric_rst ^
                  neuron_cfg_axi_rst ^ cur_wave_axi_rst ^
                  spike_shape_axi_rst;
endmodule
