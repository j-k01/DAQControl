`timescale 1ns/1ps

module top #(
    parameter integer MONITOR_WINDOW_CYCLES = 200_000_000
) (
    input  wire        SYSCLK_P,
    input  wire        SYSCLK_N,
    input  wire        CPU_RESET,
    output wire [7:0]  GPIO_LED,

    input  wire        UART_RXD,
    output wire        UART_TXD,

    input  wire        FMC1_HPC_CLK0_M2C_P,
    input  wire        FMC1_HPC_CLK0_M2C_N,
    input  wire        FMC1_HPC_GBTCLK0_M2C_C_P,
    input  wire        FMC1_HPC_GBTCLK0_M2C_C_N,
    input  wire        FMC1_HPC_GBTCLK1_M2C_C_P,
    input  wire        FMC1_HPC_GBTCLK1_M2C_C_N,

    input  wire        DAQ_SYSREF_P,
    input  wire        DAQ_SYSREF_N,
    input  wire        DAC_SYNC_P,
    input  wire        DAC_SYNC_N,

    output wire        DAC_SCLK,
    output wire        DAC_SDIN,
    input  wire        DAC_SDOUT,
    output wire        DAC_CS_N,
    output wire        DAC_TXEN,
    output wire        DAC_RESET_N,
    input  wire        DAC_ALARM,

    output wire        ADC1_RESET,
    output wire        ADC2_RESET,
    output wire        ADC_SCLK,
    output wire        ADC_SDIN,
    input  wire        ADC1_SDOUT,
    input  wire        ADC2_SDOUT,
    output wire        ADC1_CS_N,
    output wire        ADC2_CS_N,
    output wire        ADC1_SYNC_N,
    output wire        ADC2_SYNC_N,
    output wire        CH1_ENDCC,
    output wire        CH2_ENDCC,
    output wire        CH3_ENDCC,
    output wire        CH4_ENDCC,

    output wire        HMC_CLK_RESET,
    output wire        HMC_CLK_CS_N,
    output wire        HMC_CLK_SCLK,
    inout  wire        HMC_CLK_SDIO
`ifdef DAQ_WITH_GTH
    ,
    input  wire [7:0]  DAQ_GTH_RX_P,
    input  wire [7:0]  DAQ_GTH_RX_N,
    output wire [7:0]  DAQ_GTH_TX_P,
    output wire [7:0]  DAQ_GTH_TX_N
`endif
);

    wire clk_200;
    wire clk_100;
    wire clk_125;
    wire clk_50;
    wire clk_300;       // ADC DMA drain clock: faster than the 250 MHz beat
                        // clock so the S2MM/HP path has bandwidth headroom to
                        // recover from DDR backpressure (lossless burst).
    wire mmcm_locked;

    clk_wiz_0 u_clk_wiz (
        .clk_in1_p (SYSCLK_P),
        .clk_in1_n (SYSCLK_N),
        .clk_out1  (clk_200),
        .clk_out2  (clk_100),
        .clk_out3  (clk_125),
        .clk_out4  (clk_50),
        .clk_out5  (clk_300),
        .locked    (mmcm_locked)
    );

    wire fabric_rst = CPU_RESET | ~mmcm_locked;
    wire microblaze_reset = 1'b0;

    wire [31:0] rw_reg0;
    wire [31:0] rw_reg1;
    wire [31:0] rw_reg2;
    wire [31:0] rw_reg3;
    wire [31:0] rw_reg4;
    wire [31:0] rw_reg5;
    wire [31:0] rw_reg6;
    wire [31:0] rw_reg7;

    wire ro_reg0_rdint;
    wire ro_reg1_rdint;
    wire ro_reg2_rdint;
    wire ro_reg3_rdint;
    wire ro_reg4_rdint;
    wire ro_reg5_rdint;
    wire ro_reg6_rdint;
    wire ro_reg7_rdint;

`ifdef DAQ_WITH_GTH
    wire        gth_tx_usrclk;
    wire        gth_tx_usrclk2;
    wire        gth_rx_usrclk;
    wire        gth_rx_usrclk2;
`endif

`ifdef DAQ_WITH_BRAM_DATAPLANE
    wire [31:0] dac0_axi_bram_addr;
    wire        dac0_axi_bram_clk;
    wire [31:0] dac0_axi_bram_din;
    wire [31:0] dac0_axi_bram_dout;
    wire        dac0_axi_bram_en;
    wire        dac0_axi_bram_rst;
    wire [3:0]  dac0_axi_bram_we;

    wire [31:0] dac1_axi_bram_addr;
    wire        dac1_axi_bram_clk;
    wire [31:0] dac1_axi_bram_din;
    wire [31:0] dac1_axi_bram_dout;
    wire        dac1_axi_bram_en;
    wire        dac1_axi_bram_rst;
    wire [3:0]  dac1_axi_bram_we;

    wire [31:0] dac2_axi_bram_addr;
    wire        dac2_axi_bram_clk;
    wire [31:0] dac2_axi_bram_din;
    wire [31:0] dac2_axi_bram_dout;
    wire        dac2_axi_bram_en;
    wire        dac2_axi_bram_rst;
    wire [3:0]  dac2_axi_bram_we;

    wire [31:0] dac3_axi_bram_addr;
    wire        dac3_axi_bram_clk;
    wire [31:0] dac3_axi_bram_din;
    wire [31:0] dac3_axi_bram_dout;
    wire        dac3_axi_bram_en;
    wire        dac3_axi_bram_rst;
    wire [3:0]  dac3_axi_bram_we;

    wire [31:0] adc0_axi_bram_addr;
    wire        adc0_axi_bram_clk;
    wire [31:0] adc0_axi_bram_din;
    wire [31:0] adc0_axi_bram_dout;
    wire        adc0_axi_bram_en;
    wire        adc0_axi_bram_rst;
    wire [3:0]  adc0_axi_bram_we;

    wire [31:0] adc1_axi_bram_addr;
    wire        adc1_axi_bram_clk;
    wire [31:0] adc1_axi_bram_din;
    wire [31:0] adc1_axi_bram_dout;
    wire        adc1_axi_bram_en;
    wire        adc1_axi_bram_rst;
    wire [3:0]  adc1_axi_bram_we;

    // IZH neuron config bank (port A: AXI BRAM controller in the BD, clk_200).
    wire [31:0] neuron_cfg_axi_bram_addr;
    wire        neuron_cfg_axi_bram_clk;
    wire [31:0] neuron_cfg_axi_bram_din;
    wire [31:0] neuron_cfg_axi_bram_dout;
    wire        neuron_cfg_axi_bram_en;
    wire        neuron_cfg_axi_bram_rst;
    wire [3:0]  neuron_cfg_axi_bram_we;

    wire [31:0] dac0_bram_addr;
    wire        dac0_bram_clk;
    wire [63:0] dac0_bram_din;
    wire [63:0] dac0_bram_dout;
    wire        dac0_bram_en;
    wire        dac0_bram_rst;
    wire [7:0]  dac0_bram_we;

    wire [31:0] dac1_bram_addr;
    wire        dac1_bram_clk;
    wire [63:0] dac1_bram_din;
    wire [63:0] dac1_bram_dout;
    wire        dac1_bram_en;
    wire        dac1_bram_rst;
    wire [7:0]  dac1_bram_we;

    wire [31:0] dac2_bram_addr;
    wire        dac2_bram_clk;
    wire [63:0] dac2_bram_din;
    wire [63:0] dac2_bram_dout;
    wire        dac2_bram_en;
    wire        dac2_bram_rst;
    wire [7:0]  dac2_bram_we;

    wire [31:0] dac3_bram_addr;
    wire        dac3_bram_clk;
    wire [63:0] dac3_bram_din;
    wire [63:0] dac3_bram_dout;
    wire        dac3_bram_en;
    wire        dac3_bram_rst;
    wire [7:0]  dac3_bram_we;

    wire [31:0] adc_bram_addr;
    wire        adc_bram_clk;
    wire [127:0] adc0_bram_din;
    wire [127:0] adc0_bram_dout;
    wire [127:0] adc1_bram_din;
    wire [127:0] adc1_bram_dout;
    wire        adc_bram_en;
    wire        adc_bram_rst;
    wire [15:0] adc_bram_we;
`endif

`ifdef DAQ_WITH_PS_DDR_DMA
    wire [127:0] adc0_dma_axis_tdata;
    wire [15:0]  adc0_dma_axis_tkeep;
    wire         adc0_dma_axis_tlast;
    wire         adc0_dma_axis_tvalid;
    wire         adc0_dma_axis_tready;
    wire [127:0] adc1_dma_axis_tdata;
    wire [15:0]  adc1_dma_axis_tkeep;
    wire         adc1_dma_axis_tlast;
    wire         adc1_dma_axis_tvalid;
    wire         adc1_dma_axis_tready;
    wire [31:0]  adc0_dma_status_async;
    wire [31:0]  adc1_dma_status_async;
`endif

    wire [31:0] status_reg;
    wire [31:0] clk_fmc_count;
    wire [31:0] sysref_count;
    reg  [31:0] selected_count;
    wire [31:0] adc_frontend_status_reg;
    reg  [31:0] adc0_selected_debug_reg;
    reg  [31:0] adc1_selected_debug_reg;
    reg  [31:0] adc_channel_selected_reg;

    // ZCU102 HPC0 routes FMC power/presence through fixed board logic and
    // I2C/system-controller paths, not simple PL GPIO pins as on VC709.
    wire fmc_present = 1'b1;
    wire fmc_pg_m2c = 1'b1;
    wire fmc_c2m_pg_status = 1'b1;

    microblaze_bd_wrapper u_microblaze (
        .Clk                  (clk_200),
        .reset                (microblaze_reset),
        .rs232_uart_txd       (UART_TXD),
        .rs232_uart_rxd       (UART_RXD),
        .RW_REG0_0            (rw_reg0),
        .RW_REG1_0            (rw_reg1),
        .RW_REG2_0            (rw_reg2),
        .RW_REG3_0            (rw_reg3),
        .RW_REG4_0            (rw_reg4),
        .RW_REG5_0            (rw_reg5),
        .RW_REG6_0            (rw_reg6),
        .RW_REG7_0            (rw_reg7),
        .RO_REG0_IN_0         (status_reg),
        .RO_REG0_WE_0         (1'b1),
        .RO_REG1_IN_0         (clk_fmc_count),
        .RO_REG1_WE_0         (1'b1),
        .RO_REG2_IN_0         (sysref_count),
        .RO_REG2_WE_0         (1'b1),
        .RO_REG3_IN_0         (selected_count),
        .RO_REG3_WE_0         (1'b1),
        .RO_REG4_IN_0         (adc_frontend_status_reg),
        .RO_REG4_WE_0         (1'b1),
        .RO_REG5_IN_0         (adc0_selected_debug_reg),
        .RO_REG5_WE_0         (1'b1),
        .RO_REG6_IN_0         (adc1_selected_debug_reg),
        .RO_REG6_WE_0         (1'b1),
        .RO_REG7_IN_0         (adc_channel_selected_reg),
        .RO_REG7_WE_0         (1'b1),
        .RO_REG0_RDINT_0      (ro_reg0_rdint),
        .RO_REG1_RDINT_0      (ro_reg1_rdint),
        .RO_REG2_RDINT_0      (ro_reg2_rdint),
        .RO_REG3_RDINT_0      (ro_reg3_rdint),
        .RO_REG4_RDINT_0      (ro_reg4_rdint),
        .RO_REG5_RDINT_0      (ro_reg5_rdint),
        .RO_REG6_RDINT_0      (ro_reg6_rdint),
        .RO_REG7_RDINT_0      (ro_reg7_rdint)
`ifdef DAQ_WITH_PS_DDR_DMA
        ,
        // DMA S2MM/SG + both HP ports run on clk_300 (drain headroom over the
        // 250 MHz ADC beat clock). The async capture FIFO crosses 250->300.
        .gt_rx_usrclk_2       (clk_300),
        .reset_rtl            (fabric_rst),
        .S_AXIS_S2MM_0_tdata  (adc0_dma_axis_tdata),
        .S_AXIS_S2MM_0_tkeep  (adc0_dma_axis_tkeep),
        .S_AXIS_S2MM_0_tlast  (adc0_dma_axis_tlast),
        .S_AXIS_S2MM_0_tready (adc0_dma_axis_tready),
        .S_AXIS_S2MM_0_tvalid (adc0_dma_axis_tvalid),
        .S_AXIS_S2MM_1_tdata  (adc1_dma_axis_tdata),
        .S_AXIS_S2MM_1_tkeep  (adc1_dma_axis_tkeep),
        .S_AXIS_S2MM_1_tlast  (adc1_dma_axis_tlast),
        .S_AXIS_S2MM_1_tready (adc1_dma_axis_tready),
        .S_AXIS_S2MM_1_tvalid (adc1_dma_axis_tvalid)
`endif
`ifdef DAQ_WITH_BRAM_DATAPLANE
        ,
        .DAC0_AXI_BRAM_PORTA_addr (dac0_axi_bram_addr),
        .DAC0_AXI_BRAM_PORTA_clk  (dac0_axi_bram_clk),
        .DAC0_AXI_BRAM_PORTA_din  (dac0_axi_bram_din),
        .DAC0_AXI_BRAM_PORTA_dout (dac0_axi_bram_dout),
        .DAC0_AXI_BRAM_PORTA_en   (dac0_axi_bram_en),
        .DAC0_AXI_BRAM_PORTA_rst  (dac0_axi_bram_rst),
        .DAC0_AXI_BRAM_PORTA_we   (dac0_axi_bram_we),
        .DAC1_AXI_BRAM_PORTA_addr (dac1_axi_bram_addr),
        .DAC1_AXI_BRAM_PORTA_clk  (dac1_axi_bram_clk),
        .DAC1_AXI_BRAM_PORTA_din  (dac1_axi_bram_din),
        .DAC1_AXI_BRAM_PORTA_dout (dac1_axi_bram_dout),
        .DAC1_AXI_BRAM_PORTA_en   (dac1_axi_bram_en),
        .DAC1_AXI_BRAM_PORTA_rst  (dac1_axi_bram_rst),
        .DAC1_AXI_BRAM_PORTA_we   (dac1_axi_bram_we),
        .DAC2_AXI_BRAM_PORTA_addr (dac2_axi_bram_addr),
        .DAC2_AXI_BRAM_PORTA_clk  (dac2_axi_bram_clk),
        .DAC2_AXI_BRAM_PORTA_din  (dac2_axi_bram_din),
        .DAC2_AXI_BRAM_PORTA_dout (dac2_axi_bram_dout),
        .DAC2_AXI_BRAM_PORTA_en   (dac2_axi_bram_en),
        .DAC2_AXI_BRAM_PORTA_rst  (dac2_axi_bram_rst),
        .DAC2_AXI_BRAM_PORTA_we   (dac2_axi_bram_we),
        .DAC3_AXI_BRAM_PORTA_addr (dac3_axi_bram_addr),
        .DAC3_AXI_BRAM_PORTA_clk  (dac3_axi_bram_clk),
        .DAC3_AXI_BRAM_PORTA_din  (dac3_axi_bram_din),
        .DAC3_AXI_BRAM_PORTA_dout (dac3_axi_bram_dout),
        .DAC3_AXI_BRAM_PORTA_en   (dac3_axi_bram_en),
        .DAC3_AXI_BRAM_PORTA_rst  (dac3_axi_bram_rst),
        .DAC3_AXI_BRAM_PORTA_we   (dac3_axi_bram_we),
        .ADC0_AXI_BRAM_PORTA_addr (adc0_axi_bram_addr),
        .ADC0_AXI_BRAM_PORTA_clk  (adc0_axi_bram_clk),
        .ADC0_AXI_BRAM_PORTA_din  (adc0_axi_bram_din),
        .ADC0_AXI_BRAM_PORTA_dout (adc0_axi_bram_dout),
        .ADC0_AXI_BRAM_PORTA_en   (adc0_axi_bram_en),
        .ADC0_AXI_BRAM_PORTA_rst  (adc0_axi_bram_rst),
        .ADC0_AXI_BRAM_PORTA_we   (adc0_axi_bram_we),
        .ADC1_AXI_BRAM_PORTA_addr (adc1_axi_bram_addr),
        .ADC1_AXI_BRAM_PORTA_clk  (adc1_axi_bram_clk),
        .ADC1_AXI_BRAM_PORTA_din  (adc1_axi_bram_din),
        .ADC1_AXI_BRAM_PORTA_dout (adc1_axi_bram_dout),
        .ADC1_AXI_BRAM_PORTA_en   (adc1_axi_bram_en),
        .ADC1_AXI_BRAM_PORTA_rst  (adc1_axi_bram_rst),
        .ADC1_AXI_BRAM_PORTA_we   (adc1_axi_bram_we),
        .NEURON_CFG_AXI_BRAM_PORTA_addr (neuron_cfg_axi_bram_addr),
        .NEURON_CFG_AXI_BRAM_PORTA_clk  (neuron_cfg_axi_bram_clk),
        .NEURON_CFG_AXI_BRAM_PORTA_din  (neuron_cfg_axi_bram_din),
        .NEURON_CFG_AXI_BRAM_PORTA_dout (neuron_cfg_axi_bram_dout),
        .NEURON_CFG_AXI_BRAM_PORTA_en   (neuron_cfg_axi_bram_en),
        .NEURON_CFG_AXI_BRAM_PORTA_rst  (neuron_cfg_axi_bram_rst),
        .NEURON_CFG_AXI_BRAM_PORTA_we   (neuron_cfg_axi_bram_we)
`endif
    );

`ifdef DAQ_WITH_BRAM_DATAPLANE
    dataplane_bram_ip u_dataplane_bram_ip (
        .dac0_axi_addr       (dac0_axi_bram_addr),
        .dac0_axi_clk        (dac0_axi_bram_clk),
        .dac0_axi_din        (dac0_axi_bram_din),
        .dac0_axi_dout       (dac0_axi_bram_dout),
        .dac0_axi_en         (dac0_axi_bram_en),
        .dac0_axi_rst        (dac0_axi_bram_rst),
        .dac0_axi_we         (dac0_axi_bram_we),
        .dac0_fabric_addr    (dac0_bram_addr),
        .dac0_fabric_clk     (dac0_bram_clk),
        .dac0_fabric_din     (dac0_bram_din),
        .dac0_fabric_dout    (dac0_bram_dout),
        .dac0_fabric_en      (dac0_bram_en),
        .dac0_fabric_rst     (dac0_bram_rst),
        .dac0_fabric_we      (dac0_bram_we),

        .dac1_axi_addr       (dac1_axi_bram_addr),
        .dac1_axi_clk        (dac1_axi_bram_clk),
        .dac1_axi_din        (dac1_axi_bram_din),
        .dac1_axi_dout       (dac1_axi_bram_dout),
        .dac1_axi_en         (dac1_axi_bram_en),
        .dac1_axi_rst        (dac1_axi_bram_rst),
        .dac1_axi_we         (dac1_axi_bram_we),
        .dac1_fabric_addr    (dac1_bram_addr),
        .dac1_fabric_clk     (dac1_bram_clk),
        .dac1_fabric_din     (dac1_bram_din),
        .dac1_fabric_dout    (dac1_bram_dout),
        .dac1_fabric_en      (dac1_bram_en),
        .dac1_fabric_rst     (dac1_bram_rst),
        .dac1_fabric_we      (dac1_bram_we),

        .dac2_axi_addr       (dac2_axi_bram_addr),
        .dac2_axi_clk        (dac2_axi_bram_clk),
        .dac2_axi_din        (dac2_axi_bram_din),
        .dac2_axi_dout       (dac2_axi_bram_dout),
        .dac2_axi_en         (dac2_axi_bram_en),
        .dac2_axi_rst        (dac2_axi_bram_rst),
        .dac2_axi_we         (dac2_axi_bram_we),
        .dac2_fabric_addr    (dac2_bram_addr),
        .dac2_fabric_clk     (dac2_bram_clk),
        .dac2_fabric_din     (dac2_bram_din),
        .dac2_fabric_dout    (dac2_bram_dout),
        .dac2_fabric_en      (dac2_bram_en),
        .dac2_fabric_rst     (dac2_bram_rst),
        .dac2_fabric_we      (dac2_bram_we),

        .dac3_axi_addr       (dac3_axi_bram_addr),
        .dac3_axi_clk        (dac3_axi_bram_clk),
        .dac3_axi_din        (dac3_axi_bram_din),
        .dac3_axi_dout       (dac3_axi_bram_dout),
        .dac3_axi_en         (dac3_axi_bram_en),
        .dac3_axi_rst        (dac3_axi_bram_rst),
        .dac3_axi_we         (dac3_axi_bram_we),
        .dac3_fabric_addr    (dac3_bram_addr),
        .dac3_fabric_clk     (dac3_bram_clk),
        .dac3_fabric_din     (dac3_bram_din),
        .dac3_fabric_dout    (dac3_bram_dout),
        .dac3_fabric_en      (dac3_bram_en),
        .dac3_fabric_rst     (dac3_bram_rst),
        .dac3_fabric_we      (dac3_bram_we),

        .adc0_axi_addr       (adc0_axi_bram_addr),
        .adc0_axi_clk        (adc0_axi_bram_clk),
        .adc0_axi_din        (adc0_axi_bram_din),
        .adc0_axi_dout       (adc0_axi_bram_dout),
        .adc0_axi_en         (adc0_axi_bram_en),
        .adc0_axi_rst        (adc0_axi_bram_rst),
        .adc0_axi_we         (adc0_axi_bram_we),
        .adc1_axi_addr       (adc1_axi_bram_addr),
        .adc1_axi_clk        (adc1_axi_bram_clk),
        .adc1_axi_din        (adc1_axi_bram_din),
        .adc1_axi_dout       (adc1_axi_bram_dout),
        .adc1_axi_en         (adc1_axi_bram_en),
        .adc1_axi_rst        (adc1_axi_bram_rst),
        .adc1_axi_we         (adc1_axi_bram_we),
        .adc_fabric_addr     (adc_bram_addr),
        .adc_fabric_clk      (adc_bram_clk),
        .adc0_fabric_din     (adc0_bram_din),
        .adc0_fabric_dout    (adc0_bram_dout),
        .adc1_fabric_din     (adc1_bram_din),
        .adc1_fabric_dout    (adc1_bram_dout),
        .adc_fabric_en       (adc_bram_en),
        .adc_fabric_rst      (adc_bram_rst),
        .adc_fabric_we       (adc_bram_we),

        .neuron_cfg_axi_addr (neuron_cfg_axi_bram_addr),
        .neuron_cfg_axi_clk  (neuron_cfg_axi_bram_clk),
        .neuron_cfg_axi_din  (neuron_cfg_axi_bram_din),
        .neuron_cfg_axi_dout (neuron_cfg_axi_bram_dout),
        .neuron_cfg_axi_en   (neuron_cfg_axi_bram_en),
        .neuron_cfg_axi_rst  (neuron_cfg_axi_bram_rst),
        .neuron_cfg_axi_we   (neuron_cfg_axi_bram_we),
        .neuron_cfg_fabric_clk  (clk_50),
        .neuron_cfg_fabric_addr (neuron_cfg_fabric_addr),
        .neuron_cfg_fabric_dout (neuron_cfg_fabric_dout)
    );
`endif

    (* ASYNC_REG = "TRUE" *) reg [2:0] uart_rxd_sync = 3'b111;
    (* ASYNC_REG = "TRUE" *) reg [2:0] uart_txd_sync = 3'b111;
    reg       uart_rx_prev = 1'b1;
    reg       uart_tx_prev = 1'b1;
    reg       uart_rx_low_seen = 1'b0;
    reg       uart_tx_low_seen = 1'b0;
    reg [31:0] uart_rx_edge_count = 32'd0;
    reg [31:0] uart_tx_edge_count = 32'd0;

    wire uart_rx_level = uart_rxd_sync[2];
    wire uart_tx_level = uart_txd_sync[2];

    always @(posedge clk_200) begin
        if (fabric_rst) begin
            uart_rxd_sync <= 3'b111;
            uart_txd_sync <= 3'b111;
            uart_rx_prev <= 1'b1;
            uart_tx_prev <= 1'b1;
            uart_rx_low_seen <= 1'b0;
            uart_tx_low_seen <= 1'b0;
            uart_rx_edge_count <= 32'd0;
            uart_tx_edge_count <= 32'd0;
        end else begin
            uart_rxd_sync <= {uart_rxd_sync[1:0], UART_RXD};
            uart_txd_sync <= {uart_txd_sync[1:0], UART_TXD};

            uart_rx_prev <= uart_rx_level;
            uart_tx_prev <= uart_tx_level;
            if (uart_rx_level != uart_rx_prev) begin
                uart_rx_edge_count <= uart_rx_edge_count + 1'b1;
            end
            if (uart_tx_level != uart_tx_prev) begin
                uart_tx_edge_count <= uart_tx_edge_count + 1'b1;
            end

            if (!uart_rx_level) begin
                uart_rx_low_seen <= 1'b1;
            end
            if (!uart_tx_level) begin
                uart_tx_low_seen <= 1'b1;
            end
        end
    end

    wire [31:0] uart_debug_reg = {
        8'hA5,
        uart_rx_edge_count[7:0],
        uart_tx_edge_count[7:0],
        uart_rx_low_seen,
        uart_tx_low_seen,
        ro_reg3_rdint,
        ro_reg0_rdint,
        UART_TXD,
        uart_tx_level,
        UART_RXD,
        uart_rx_level
    };

    wire [31:0] gth_status_async;
    wire [31:0] gth_status_reg;
    wire [31:0] gth_rx_status_async;
    wire [31:0] gth_rx_status_reg;
    wire        gth_qpll_locked_async;
    wire        gth_qpll_locked;
    wire        gth_tx_ready_async;
    wire        gth_tx_ready;
    wire        gth_rx_ready_async;
    wire        gth_rx_ready;
    wire        gth_unused_reduce;
    wire        litejesd_active_async;
    wire        litejesd_active;
    wire        litejesd_ready_async;
    wire        litejesd_ready;
    wire [31:0] litejesd_status_async;
    wire [31:0] litejesd_status_reg;
    wire [31:0] litejesd_triangle_async;
    wire [31:0] litejesd_triangle_word;
    wire [31:0] litejesd_sine_async;
    wire [31:0] litejesd_sine_word;
    wire [63:0] dac_program_word0_async;
    wire [63:0] dac_program_word1_async;
    wire [63:0] dac_program_word2_async;
    wire [63:0] dac_program_word3_async;
    wire [63:0] dac_program_word0_reg;
    wire [63:0] dac_program_word1_reg;
    wire [63:0] dac_program_word2_reg;
    wire [63:0] dac_program_word3_reg;
    wire [3:0]  izh_spike_flags_neuron;
    wire [3:0]  izh_spike_flags_tx;
    wire [7:0]  dac_source_modes_tx;
    wire [31:0] dac_neuron_debug_async;
    wire [31:0] dac_neuron_debug_reg;
    wire [31:0] dac_program_status_async;
    wire [31:0] dac_program_status_reg;
    wire [31:0] gth_tx_clk_count;
    wire [31:0] gth_rx_clk_count;
    wire [15:0] gth_tx_clk_count_short;
    wire [15:0] gth_rx_clk_count_short;
    wire [31:0] gth_txdata_lane0_async;
    wire [31:0] gth_txdata_lane0_debug;
    wire [7:0]  gth_txctrl2_lane0_async;
    wire [7:0]  gth_txctrl2_lane0_debug;
    wire [1:0]  gth_qpll0lock;
    wire [1:0]  gth_qpll0lock_sync;
    wire        adc1_sync_n_async;
    wire        adc1_litejesd_ready_async;
    wire        adc1_litejesd_ready;
    wire [31:0] adc1_rx_status_async;
    wire [31:0] adc1_rx_lane_status_async;
    wire [31:0] adc1_rx_event_counts_async;
    wire [31:0] adc1_rx_sample_a_low_async;
    wire [31:0] adc1_rx_sample_a_high_async;
    wire [31:0] adc1_rx_sample_b_low_async;
    wire [31:0] adc1_rx_sample_b_high_async;
    wire [31:0] adc1_rx_raw_lane_async;
    wire [31:0] adc1_rx_status_reg;
    wire [31:0] adc1_rx_lane_status_reg;
    wire [31:0] adc1_rx_event_counts_reg;
    wire [31:0] adc1_rx_sample_a_low_reg;
    wire [31:0] adc1_rx_sample_a_high_reg;
    wire [31:0] adc1_rx_sample_b_low_reg;
    wire [31:0] adc1_rx_sample_b_high_reg;
    wire [31:0] adc1_rx_raw_lane_reg;
    wire        adc2_sync_n_async;
    wire        adc2_litejesd_ready_async;
    wire        adc2_litejesd_ready;
    wire [31:0] adc2_rx_status_async;
    wire [31:0] adc2_rx_lane_status_async;
    wire [31:0] adc2_rx_event_counts_async;
    wire [31:0] adc2_rx_sample_a_low_async;
    wire [31:0] adc2_rx_sample_a_high_async;
    wire [31:0] adc2_rx_sample_b_low_async;
    wire [31:0] adc2_rx_sample_b_high_async;
    wire [31:0] adc2_rx_raw_lane_async;
    wire [31:0] adc2_rx_status_reg;
    wire [31:0] adc2_rx_lane_status_reg;
    wire [31:0] adc2_rx_event_counts_reg;
    wire [31:0] adc2_rx_sample_a_low_reg;
    wire [31:0] adc2_rx_sample_a_high_reg;
    wire [31:0] adc2_rx_sample_b_low_reg;
    wire [31:0] adc2_rx_sample_b_high_reg;
    wire [31:0] adc2_rx_raw_lane_reg;
    wire [63:0] adc_ch0_async;
    wire [63:0] adc_ch1_async;
    wire [63:0] adc_ch2_async;
    wire [63:0] adc_ch3_async;
    wire [63:0] adc_ch0_reg;
    wire [63:0] adc_ch1_reg;
    wire [63:0] adc_ch2_reg;
    wire [63:0] adc_ch3_reg;
    wire [31:0] adc_capture_status_async;
    wire [31:0] adc_capture_status_reg;

    wire manual_spi_enable = rw_reg0[30];
    wire dac_auto_busy;
    wire dac_auto_done;
    wire dac_auto_reset_n;
    wire dac_auto_cs_n;
    wire dac_auto_sclk;
    wire dac_auto_sdin;
    wire [5:0] dac_auto_step;
    wire [7:0] dac_auto_last_addr;
    wire [15:0] dac_auto_last_data;
    wire [31:0] dac_auto_status_reg;
    wire [31:0] dac_auto_last_write_reg;
    wire adc_auto_busy;
    wire adc_auto_done;
    wire adc_auto_reset1;
    wire adc_auto_reset2;
    wire adc_auto_cs1_n;
    wire adc_auto_cs2_n;
    wire adc_auto_sclk;
    wire adc_auto_sdin;
    wire adc_auto_chip;
    wire [6:0] adc_auto_step;
    wire [15:0] adc_auto_last_addr;
    wire [7:0] adc_auto_last_data;
    wire adc_readback_done1;
    wire adc_readback_done2;
    wire adc_readback_ok1;
    wire adc_readback_ok2;
    wire adc_sdout_stuck1;
    wire adc_sdout_stuck2;
    wire [23:0] adc1_analog_word;
    wire [23:0] adc1_jesd_digital_word;
    wire [15:0] adc1_jesd_analog_word;
    wire [23:0] adc2_analog_word;
    wire [23:0] adc2_jesd_digital_word;
    wire [15:0] adc2_jesd_analog_word;
    wire [15:0] adc_last_read_addr;
    wire [7:0] adc_last_read_data;
    wire adc_last_read_chip;
    wire [31:0] adc_auto_status_reg;
    wire [31:0] adc_auto_last_write_reg;
    wire [31:0] adc_auto_last_read_reg;
    wire hmc_auto_busy;
    wire hmc_auto_done;
    wire hmc_auto_reset;
    wire hmc_auto_cs_n;
    wire hmc_auto_sclk;
    wire hmc_auto_sdio_o;
    wire hmc_auto_sdio_oe;
    wire [7:0] hmc_auto_step;
    wire [11:0] hmc_auto_last_addr;
    wire [7:0] hmc_auto_last_data;
    wire hmc_sdio_in = HMC_CLK_SDIO;
    wire hmc_readback_done;
    wire hmc_readback_sdio_stuck;
    wire [3:0] hmc_readback_index;
    wire [11:0] hmc_readback_last_addr;
    wire [7:0] hmc_readback_last_data;
    wire [31:0] hmc_readback_scratch_word;
    wire [31:0] hmc_readback_id_word;
    wire [31:0] hmc_readback_alarm_word;
    wire [31:0] hmc_readback_pll1_word;
    wire [31:0] hmc_readback_pll2_word;
    wire hmc_auto_owns = ~manual_spi_enable;
    reg  hmc_restart_req_d = 1'b0;
    reg  dac_restart_req_d = 1'b0;
    reg  adc_restart_req_d = 1'b0;
    reg  adc_test_req_d = 1'b0;
    reg  adc_capture_req_d = 1'b0;
    reg  adc_capture_req_toggle = 1'b0;
    always @(posedge clk_200) begin
        if (fabric_rst) begin
            hmc_restart_req_d <= 1'b0;
            dac_restart_req_d <= 1'b0;
            adc_restart_req_d <= 1'b0;
            adc_test_req_d <= 1'b0;
            adc_capture_req_d <= 1'b0;
            adc_capture_req_toggle <= 1'b0;
        end else begin
            hmc_restart_req_d <= rw_reg3[0];
            dac_restart_req_d <= rw_reg3[1];
            adc_restart_req_d <= rw_reg3[2];
            adc_test_req_d <= rw_reg0[29];
            adc_capture_req_d <= rw_reg3[3];
            if (rw_reg3[3] & ~adc_capture_req_d) begin
                adc_capture_req_toggle <= ~adc_capture_req_toggle;
            end
        end
    end
    wire hmc_restart_pulse = ~fabric_rst & rw_reg3[0] & ~hmc_restart_req_d;
    wire dac_restart_pulse = ~fabric_rst & hmc_auto_done &
                              rw_reg3[1] & ~dac_restart_req_d;
    wire adc_restart_pulse = ~fabric_rst & hmc_auto_done &
                              rw_reg3[2] & ~adc_restart_req_d;
    wire adc_test_pulse = ~fabric_rst & hmc_auto_done & adc_auto_done &
                          rw_reg0[29] & ~adc_test_req_d;
    wire [2:0] adc_test_mode = rw_reg0[28:26];
    wire [1:0] adc_test_chip_mask = rw_reg5[25:24];

    hmc7044_init #(
        .CLK_HZ           (200_000_000),
        .SPI_HZ           (1_000_000),
        .RESET_ASSERT_US  (10_000),
        .RESET_RELEASE_US (10_000)
    ) u_hmc7044_init (
        .clk         (clk_200),
        .rst         (fabric_rst),
        .restart     (hmc_restart_pulse),
        .spi_sdio_i  (hmc_sdio_in),
        .busy        (hmc_auto_busy),
        .done        (hmc_auto_done),
        .reset_out   (hmc_auto_reset),
        .spi_cs_n    (hmc_auto_cs_n),
        .spi_sclk    (hmc_auto_sclk),
        .spi_sdio_o  (hmc_auto_sdio_o),
        .spi_sdio_oe (hmc_auto_sdio_oe),
        .step_index  (hmc_auto_step),
        .last_addr   (hmc_auto_last_addr),
        .last_data   (hmc_auto_last_data),
        .readback_done       (hmc_readback_done),
        .readback_sdio_stuck (hmc_readback_sdio_stuck),
        .readback_index      (hmc_readback_index),
        .readback_last_addr  (hmc_readback_last_addr),
        .readback_last_data  (hmc_readback_last_data),
        .readback_scratch_word (hmc_readback_scratch_word),
        .readback_id_word    (hmc_readback_id_word),
        .readback_alarm_word (hmc_readback_alarm_word),
        .readback_pll1_word  (hmc_readback_pll1_word),
        .readback_pll2_word  (hmc_readback_pll2_word)
    );

    dac39j84_init #(
        .CLK_HZ               (200_000_000),
        .SPI_HZ               (500_000),
        .RESET_ASSERT_US      (10_000),
        .RESET_RELEASE_US     (10_000),
        .WRITE_GAP_US         (10_000),
        .CLEAR_ALARM_DELAY_US (1_000_000)
    ) u_dac39j84_init (
        .clk        (clk_200),
        .rst        (fabric_rst | ~hmc_auto_done),
        .start      (1'b1),
        .restart    (dac_restart_pulse),
        .busy       (dac_auto_busy),
        .done       (dac_auto_done),
        .reset_n    (dac_auto_reset_n),
        .spi_cs_n   (dac_auto_cs_n),
        .spi_sclk   (dac_auto_sclk),
        .spi_sdin   (dac_auto_sdin),
        .step_index (dac_auto_step),
        .last_addr  (dac_auto_last_addr),
        .last_data  (dac_auto_last_data),
        .status     (dac_auto_status_reg),
        .last_write (dac_auto_last_write_reg)
    );

    ads54j60_init #(
        .CLK_HZ           (200_000_000),
        .SPI_HZ           (500_000),
        .RESET_ASSERT_US  (10_000),
        .RESET_RELEASE_US (10_000),
        .OP_GAP_US        (10_000)
    ) u_ads54j60_init (
        .clk                    (clk_200),
        .rst                    (fabric_rst | ~hmc_auto_done),
        .start                  (1'b1),
        .restart                (adc_restart_pulse),
        .test_restart           (adc_test_pulse),
        .test_mode              (adc_test_mode),
        .test_chip_mask         (adc_test_chip_mask),
        .adc1_sdout             (ADC1_SDOUT),
        .adc2_sdout             (ADC2_SDOUT),
        .busy                   (adc_auto_busy),
        .done                   (adc_auto_done),
        .adc1_reset             (adc_auto_reset1),
        .adc2_reset             (adc_auto_reset2),
        .spi_cs1_n              (adc_auto_cs1_n),
        .spi_cs2_n              (adc_auto_cs2_n),
        .spi_sclk               (adc_auto_sclk),
        .spi_sdin               (adc_auto_sdin),
        .chip_index             (adc_auto_chip),
        .op_index               (adc_auto_step),
        .last_addr              (adc_auto_last_addr),
        .last_data              (adc_auto_last_data),
        .readback_done1         (adc_readback_done1),
        .readback_done2         (adc_readback_done2),
        .readback_ok1           (adc_readback_ok1),
        .readback_ok2           (adc_readback_ok2),
        .sdout_stuck1           (adc_sdout_stuck1),
        .sdout_stuck2           (adc_sdout_stuck2),
        .adc1_analog_word       (adc1_analog_word),
        .adc1_jesd_digital_word (adc1_jesd_digital_word),
        .adc1_jesd_analog_word  (adc1_jesd_analog_word),
        .adc2_analog_word       (adc2_analog_word),
        .adc2_jesd_digital_word (adc2_jesd_digital_word),
        .adc2_jesd_analog_word  (adc2_jesd_analog_word),
        .last_read_addr         (adc_last_read_addr),
        .last_read_data         (adc_last_read_data),
        .last_read_chip         (adc_last_read_chip),
        .status                 (adc_auto_status_reg),
        .last_write             (adc_auto_last_write_reg),
        .last_read              (adc_auto_last_read_reg)
    );

    assign HMC_CLK_RESET = hmc_auto_owns ? hmc_auto_reset : rw_reg0[1];
`ifdef DAQ_WITH_LITEJESD
    assign DAC_RESET_N   = manual_spi_enable ? rw_reg0[2] :
                            dac_auto_reset_n;
    assign DAC_TXEN      = manual_spi_enable ? rw_reg0[3] :
                            (dac_auto_done & gth_tx_ready_async);
`else
    assign DAC_RESET_N   = rw_reg0[2];
    assign DAC_TXEN      = rw_reg0[3];
`endif
    assign ADC1_RESET    = manual_spi_enable ? rw_reg0[4] : adc_auto_reset1;
    assign ADC2_RESET    = manual_spi_enable ? rw_reg0[5] : adc_auto_reset2;
    assign ADC1_CS_N     = adc_auto_cs1_n;
    assign ADC2_CS_N     = adc_auto_cs2_n;
    assign ADC_SCLK      = adc_auto_sclk;
    assign ADC_SDIN      = adc_auto_sdin;
    assign ADC1_SYNC_N   = adc1_sync_n_async;
    assign ADC2_SYNC_N   = adc2_sync_n_async;
    assign CH1_ENDCC     = rw_reg0[6];
    assign CH2_ENDCC     = rw_reg0[7];
    assign CH3_ENDCC     = rw_reg0[8];
    assign CH4_ENDCC     = rw_reg0[9];

`ifdef DAQ_WITH_LITEJESD
    assign DAC_CS_N      = manual_spi_enable ? rw_reg0[16] : dac_auto_cs_n;
    assign DAC_SCLK      = manual_spi_enable ? rw_reg0[17] : dac_auto_sclk;
    assign DAC_SDIN      = manual_spi_enable ? rw_reg0[18] : dac_auto_sdin;
`else
    assign DAC_CS_N      = manual_spi_enable ? rw_reg0[16] : 1'b1;
    assign DAC_SCLK      = manual_spi_enable ? rw_reg0[17] : 1'b0;
    assign DAC_SDIN      = manual_spi_enable ? rw_reg0[18] : 1'b0;
`endif

    assign HMC_CLK_CS_N  = hmc_auto_owns ? hmc_auto_cs_n : rw_reg0[19];
    assign HMC_CLK_SCLK  = hmc_auto_owns ? hmc_auto_sclk : rw_reg0[20];
    assign HMC_CLK_SDIO  = hmc_auto_owns ?
                           (hmc_auto_sdio_oe ? hmc_auto_sdio_o : 1'bz) :
                           (rw_reg0[22] ? rw_reg0[21] : 1'bz);

    wire clk_fmc_ibuf;
    IBUFDS #(
        .DIFF_TERM  ("TRUE"),
        .IOSTANDARD ("LVDS")
    ) u_clk_fmc_ibuf (
        .I  (FMC1_HPC_CLK0_M2C_P),
        .IB (FMC1_HPC_CLK0_M2C_N),
        .O  (clk_fmc_ibuf)
    );
    wire sysref_ibuf;
    IBUFDS #(
        .DIFF_TERM  ("TRUE"),
        .IOSTANDARD ("LVDS")
    ) u_sysref_ibuf (
        .I  (DAQ_SYSREF_P),
        .IB (DAQ_SYSREF_N),
        .O  (sysref_ibuf)
    );
    wire dac_sync_raw;
    IBUFDS #(
        .DIFF_TERM  ("TRUE"),
        .IOSTANDARD ("LVDS")
    ) u_dac_sync_ibuf (
        .I  (DAC_SYNC_P),
        .IB (DAC_SYNC_N),
        .O  (dac_sync_raw)
    );

    wire gbt0_refclk;
    wire gbt1_refclk;

    IBUFDS_GTE4 u_gbtclk0_ibuf (
        .I     (FMC1_HPC_GBTCLK0_M2C_C_P),
        .IB    (FMC1_HPC_GBTCLK0_M2C_C_N),
        .CEB   (1'b0),
        .O     (gbt0_refclk),
        .ODIV2 ()
    );
    IBUFDS_GTE4 u_gbtclk1_ibuf (
        .I     (FMC1_HPC_GBTCLK1_M2C_C_P),
        .IB    (FMC1_HPC_GBTCLK1_M2C_C_N),
        .CEB   (1'b0),
        .O     (gbt1_refclk),
        .ODIV2 ()
    );
    wire [31:0] gbt0_count = 32'd0;
    wire [31:0] gbt1_count = 32'd0;
    wire        clk_fmc_seen;
    wire        sysref_seen;
    wire        gbt0_seen = 1'b0;
    wire        gbt1_seen = 1'b0;

    signal_activity_monitor #(
        .REF_WINDOW_CYCLES (MONITOR_WINDOW_CYCLES)
    ) u_clk_fmc_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_signal(clk_fmc_ibuf),
        .last_count (clk_fmc_count),
        .seen       (clk_fmc_seen)
    );

    signal_activity_monitor #(
        .REF_WINDOW_CYCLES (MONITOR_WINDOW_CYCLES)
    ) u_sysref_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_signal(sysref_ibuf),
        .last_count (sysref_count),
        .seen       (sysref_seen)
    );

    wire        adc_new_control_enable = rw_reg5[31];
    wire [1:0]  adc_capture_format_ctrl = adc_new_control_enable ? rw_reg5[1:0] : rw_reg2[29:28];
    wire [1:0]  adc_raw_lane_select_ctrl = adc_new_control_enable ? rw_reg5[3:2] : rw_reg2[29:28];
    wire        adc1_use_physical_dp_order_ctrl = adc_new_control_enable ? rw_reg5[4] : rw_reg2[26];
    wire        adc2_use_physical_dp_order_ctrl = adc_new_control_enable ? rw_reg5[5] : 1'b0;
    wire        adc_ilas_check_enable_ctrl = adc_new_control_enable ? ~rw_reg5[8] : ~rw_reg2[24];
    wire        adc_stpl_enable_ctrl = adc_new_control_enable ? rw_reg5[9] : rw_reg2[25];
    wire [7:0]  gth_rx_polarity_ctrl = adc_new_control_enable ? rw_reg5[23:16] : rw_reg2[23:16];

    // The IZH neuron bank runs in its own slow clock domain (clk_50) so the
    // vendor neuron's Q16.16 DSP multiply chains (~12 ns of logic) close
    // timing without touching vendor/izh_neuron.v.  All config now lives in a
    // dual-clock "config bank" BRAM: the MicroBlaze writes the profile image
    // over AXI (port A, clk_200), then toggles rw_reg4[0].  That toggle is the
    // ONLY config control that crosses through the register file; everything
    // else is read straight out of the BRAM on port B inside clk_50.  We
    // synchronize the toggle into clk_50 and edge-detect a one-cycle
    // prog_start pulse for the bank reader, which walks the whole BRAM and
    // (re)loads every neuron whose mask bit is set.
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] neuron_rst_sync = 2'b11;
    always @(posedge clk_50) begin
        neuron_rst_sync <= {neuron_rst_sync[0], fabric_rst};
    end
    wire neuron_rst = neuron_rst_sync[1];

    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [2:0] izh_prog_toggle_sync = 3'b000;
    always @(posedge clk_50) begin
        if (neuron_rst)
            izh_prog_toggle_sync <= 3'b000;
        else
            izh_prog_toggle_sync <= {izh_prog_toggle_sync[1:0], rw_reg4[0]};
    end
    // Either edge of the firmware-toggled bit fires one prog_start pulse.
    wire izh_prog_start = izh_prog_toggle_sync[2] ^ izh_prog_toggle_sync[1];

    // Config BRAM port B (read-only, clk_50 neuron domain).
    wire [5:0]  neuron_cfg_fabric_addr;
    wire [31:0] neuron_cfg_fabric_dout;

    izh_dac_bank #(
        .ADDR_W (6)
    ) u_izh_dac_bank (
        .clk         (clk_50),
        .reset       (neuron_rst),
        .prog_start  (izh_prog_start),
        .cfg_addr    (neuron_cfg_fabric_addr),
        .cfg_data    (neuron_cfg_fabric_dout),
        .spike_flags (izh_spike_flags_neuron),
        .debug_word  (dac_neuron_debug_async)
    );

`ifdef DAQ_WITH_GTH
    wire        gth_reset_all = fabric_rst | rw_reg2[0] | ~hmc_auto_done;
    wire        gth_tx_userclk_active;
    wire        gth_rx_userclk_active;
    wire        gth_reset_rx_cdr_stable;
    wire        gth_reset_tx_done;
    wire        gth_reset_rx_done;
    wire [1:0]  gth_qpll0outclk;
    wire [1:0]  gth_qpll0outrefclk;
    wire [7:0]  gth_gtpowergood;
    wire [7:0]  gth_txpmaresetdone;
    wire [7:0]  gth_rxpmaresetdone;
    wire [7:0]  gth_rxcdrlock;
    wire [7:0]  gth_rxbyteisaligned;
    wire [7:0]  gth_rxbyterealign;
    wire [7:0]  gth_rxcommadet;
    wire [255:0] gth_userdata_rx;
    wire [127:0] gth_rxctrl0;
    wire [127:0] gth_rxctrl1;
    wire [63:0]  gth_rxctrl2;
    wire [63:0]  gth_rxctrl3;
    wire [255:0] gth_userdata_tx;
    wire [63:0]  gth_txctrl2;

`ifdef DAQ_WITH_LITEJESD
    wire [255:0] litejesd_txdata;
    wire [31:0]  litejesd_txcharisk;
    wire [255:0] dac_debug_bram_words;
    wire [255:0] dac_debug_source_words;
    wire [255:0] dac_debug_native_words;
    wire [255:0] dac_debug_preimage_words;
    wire [255:0] dac_debug_physical_words;
    wire [255:0] dac_debug_remap_in_words;
    wire [255:0] dac_debug_remap_out_words;
    wire [255:0] dac_debug_jesd_converter_words;
    wire         litejesd_reset = gth_reset_all | ~gth_reset_tx_done |
                                  ~gth_tx_userclk_active;
    wire [7:0]   litejesd_phy_tx_rst = {8{litejesd_reset}};

    (* ASYNC_REG = "TRUE" *) reg [2:0] litejesd_sync_pipe = 3'b111;
    (* ASYNC_REG = "TRUE" *) reg [2:0] litejesd_sysref_pipe = 3'b000;

    always @(posedge gth_tx_usrclk2) begin
        if (litejesd_reset) begin
            litejesd_sync_pipe <= 3'b111;
            litejesd_sysref_pipe <= 3'b000;
        end else begin
            litejesd_sync_pipe <= {litejesd_sync_pipe[1:0], dac_sync_raw};
            litejesd_sysref_pipe <= {litejesd_sysref_pipe[1:0], sysref_ibuf};
        end
    end

    assign litejesd_active_async = ~litejesd_reset;

    wire [23:0] dac_sine_phase_inc_tx;
    cdc_vector_sync #(
        .WIDTH (24)
    ) u_dac_sine_phase_inc_sync (
        .dest_clk (gth_tx_usrclk2),
        .dest_rst (litejesd_reset),
        .src      (rw_reg3[31:8]),
        .dest     (dac_sine_phase_inc_tx)
    );

    // Keep DAC control out of RW2[15:8]. Those bits drive GTH TX polarity.
    // DAC-side runtime controls are limited to RW2[7:1]:
    //   [2:1] ILA-only sample-map tag
    //   [4:3] post-LiteJESD TX lane mode
    //   [7:5] debug/source probe select
    wire [6:0] dac_tx_control_tx;
    cdc_vector_sync #(
        .WIDTH (7)
    ) u_dac_tx_control_sync (
        .dest_clk (gth_tx_usrclk2),
        .dest_rst (litejesd_reset),
        .src      (rw_reg2[7:1]),
        .dest     (dac_tx_control_tx)
    );

    wire [1:0] dac_sample_map_mode_tx = dac_tx_control_tx[1:0];
    wire [1:0] dac_tx_lane_mode_tx = dac_tx_control_tx[3:2];
    wire [2:0] dac_active_converter_tx = dac_tx_control_tx[6:4];
    wire [3:0] dac_physical_map_mode_tx = 4'd0;
    wire       dac_tag_source_enable_tx = 1'b0;

    // Source select lives wholly in the GT (DAC) clock domain: a simple
    // per-DAC mux choosing among the viable outputs (DDS / BRAM / neuron pulse
    // shaper).  Firmware sets rw_reg4[15:8] (2 bits per DAC) and we sync it
    // across; it is independent of the neuron bank and its reprogramming.
    cdc_vector_sync #(
        .WIDTH (8)
    ) u_dac_source_modes_sync (
        .dest_clk (gth_tx_usrclk2),
        .dest_rst (litejesd_reset),
        .src      (rw_reg4[15:8]),
        .dest     (dac_source_modes_tx)
    );

    // The neurons live in the slow clk_50 neuron domain.  Only their one-bit
    // spike events cross into the JESD domain, where the DAC-rate pulse
    // shapers live; the shaper edge-detects, and a one-clk_50-cycle spike
    // pulse only widens through the 2FF sync, so no spikes are lost.
    cdc_vector_sync #(
        .WIDTH (4)
    ) u_izh_spike_flags_sync (
        .dest_clk (gth_tx_usrclk2),
        .dest_rst (litejesd_reset),
        .src      (izh_spike_flags_neuron),
        .dest     (izh_spike_flags_tx)
    );

`ifdef DAQ_WITH_BRAM_DATAPLANE
    (* ASYNC_REG = "TRUE" *) reg [2:0] dac_program_req_sync = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [1:0] dac_program_enable_sync = 2'b00;

    always @(posedge gth_tx_usrclk2) begin
        if (litejesd_reset) begin
            dac_program_req_sync <= 3'b000;
            dac_program_enable_sync <= 2'b00;
        end else begin
            dac_program_req_sync <= {dac_program_req_sync[1:0], adc_capture_req_toggle};
            dac_program_enable_sync <= {dac_program_enable_sync[0], rw_reg3[6]};
        end
    end

    wire dac_program_restart = dac_program_req_sync[2] ^ dac_program_req_sync[1];
    wire dac_program_enable = dac_program_enable_sync[1];
    wire [23:0] dac_bram_frame_count_tx = dac_sine_phase_inc_tx;
    wire [23:0] dac_dds_phase_inc_tx = dac_program_enable ? 24'd0 : dac_sine_phase_inc_tx;

    wire [31:0] dac_program_status0_async;
    wire [31:0] dac_program_status1_async;
    wire [31:0] dac_program_status2_async;
    wire [31:0] dac_program_status3_async;

    dac_bram_player #(
        .BRAM_DEPTH_WORDS (8192)
    ) u_dac0_bram_player (
        .clk          (gth_tx_usrclk2),
        .rst          (litejesd_reset),
        .enable       (litejesd_active_async & dac_program_enable),
        .restart      (dac_program_restart),
        .frame_count  (dac_bram_frame_count_tx),
        .bram_addr    (dac0_bram_addr),
        .bram_clk     (dac0_bram_clk),
        .bram_din     (dac0_bram_din),
        .bram_dout    (dac0_bram_dout),
        .bram_en      (dac0_bram_en),
        .bram_rst     (dac0_bram_rst),
        .bram_we      (dac0_bram_we),
        .program_word (dac_program_word0_async),
        .status       (dac_program_status0_async)
    );

    dac_bram_player #(
        .BRAM_DEPTH_WORDS (8192)
    ) u_dac1_bram_player (
        .clk          (gth_tx_usrclk2),
        .rst          (litejesd_reset),
        .enable       (litejesd_active_async & dac_program_enable),
        .restart      (dac_program_restart),
        .frame_count  (dac_bram_frame_count_tx),
        .bram_addr    (dac1_bram_addr),
        .bram_clk     (dac1_bram_clk),
        .bram_din     (dac1_bram_din),
        .bram_dout    (dac1_bram_dout),
        .bram_en      (dac1_bram_en),
        .bram_rst     (dac1_bram_rst),
        .bram_we      (dac1_bram_we),
        .program_word (dac_program_word1_async),
        .status       (dac_program_status1_async)
    );

    dac_bram_player #(
        .BRAM_DEPTH_WORDS (8192)
    ) u_dac2_bram_player (
        .clk          (gth_tx_usrclk2),
        .rst          (litejesd_reset),
        .enable       (litejesd_active_async & dac_program_enable),
        .restart      (dac_program_restart),
        .frame_count  (dac_bram_frame_count_tx),
        .bram_addr    (dac2_bram_addr),
        .bram_clk     (dac2_bram_clk),
        .bram_din     (dac2_bram_din),
        .bram_dout    (dac2_bram_dout),
        .bram_en      (dac2_bram_en),
        .bram_rst     (dac2_bram_rst),
        .bram_we      (dac2_bram_we),
        .program_word (dac_program_word2_async),
        .status       (dac_program_status2_async)
    );

    dac_bram_player #(
        .BRAM_DEPTH_WORDS (8192)
    ) u_dac3_bram_player (
        .clk          (gth_tx_usrclk2),
        .rst          (litejesd_reset),
        .enable       (litejesd_active_async & dac_program_enable),
        .restart      (dac_program_restart),
        .frame_count  (dac_bram_frame_count_tx),
        .bram_addr    (dac3_bram_addr),
        .bram_clk     (dac3_bram_clk),
        .bram_din     (dac3_bram_din),
        .bram_dout    (dac3_bram_dout),
        .bram_en      (dac3_bram_en),
        .bram_rst     (dac3_bram_rst),
        .bram_we      (dac3_bram_we),
        .program_word (dac_program_word3_async),
        .status       (dac_program_status3_async)
    );

    assign dac_program_status_async = {
        8'hD5,
        dac_program_enable,
        dac_program_restart,
        dac_program_status3_async[22],
        dac_program_status2_async[22],
        dac_program_status1_async[22],
        dac_program_status0_async[22],
        2'd0,
        dac_program_status0_async[15:0]
    };
`else
    wire dac_program_enable = 1'b0;
    wire dac_program_restart = 1'b0;
    wire [23:0] dac_dds_phase_inc_tx = dac_sine_phase_inc_tx;
    assign dac_program_word0_async = 64'd0;
    assign dac_program_word1_async = 64'd0;
    assign dac_program_word2_async = 64'd0;
    assign dac_program_word3_async = 64'd0;
    assign dac_program_status_async = 32'd0;
`endif

    daq_litejesd_dac_tx_path u_litejesd_dac_tx_path (
        .jesd_clk         (gth_tx_usrclk2),
        .jesd_rst         (litejesd_reset),
        .phy_tx_clk       (gth_tx_usrclk2),
        .phy_tx_rst       (litejesd_phy_tx_rst),
        .enable           (litejesd_active_async),
        .stpl_enable      (1'b0),
        .sysref           (litejesd_sysref_pipe[2]),
        .sync_n           (litejesd_sync_pipe[2]),
        .active_converter (dac_active_converter_tx),
        .sample_map_mode  (dac_sample_map_mode_tx),
        .physical_map_mode(dac_physical_map_mode_tx),
        .triangle_step    (16'd256),
        .sine_phase_inc   (dac_dds_phase_inc_tx),
        .source_modes     (dac_source_modes_tx),
        .tag_source_enable(dac_tag_source_enable_tx),
        .program_enable   (dac_program_enable),
        .program_word0    (dac_program_word0_async),
        .program_word1    (dac_program_word1_async),
        .program_word2    (dac_program_word2_async),
        .program_word3    (dac_program_word3_async),
        .neuron_spikes    (izh_spike_flags_tx),
        .litejesd_ready   (litejesd_ready_async),
        .status           (litejesd_status_async),
        .triangle_word    (litejesd_triangle_async),
        .sine_word        (litejesd_sine_async),
        .gth_txdata       (litejesd_txdata),
        .gth_txcharisk    (litejesd_txcharisk),
        .debug_bram_words (dac_debug_bram_words),
        .debug_source_words(dac_debug_source_words),
        .debug_native_words(dac_debug_native_words),
        .debug_preimage_words(dac_debug_preimage_words),
        .debug_physical_words(dac_debug_physical_words),
        .debug_remap_in_words(dac_debug_remap_in_words),
        .debug_remap_out_words(dac_debug_remap_out_words),
        .debug_jesd_converter_words(dac_debug_jesd_converter_words)
    );

    function [2:0] tx_src_lane;
        input [1:0] mode;
        input [2:0] phys;
        begin
            case (mode)
            2'd1: begin
                case (phys)
                3'd0: tx_src_lane = 3'd1;
                3'd1: tx_src_lane = 3'd3;
                3'd2: tx_src_lane = 3'd2;
                3'd3: tx_src_lane = 3'd0;
                3'd4: tx_src_lane = 3'd7;
                3'd5: tx_src_lane = 3'd6;
                3'd6: tx_src_lane = 3'd5;
                default: tx_src_lane = 3'd4;
                endcase
            end
            2'd2: begin
                case (phys)
                3'd0: tx_src_lane = 3'd3;
                3'd1: tx_src_lane = 3'd0;
                3'd2: tx_src_lane = 3'd2;
                3'd3: tx_src_lane = 3'd1;
                3'd4: tx_src_lane = 3'd7;
                3'd5: tx_src_lane = 3'd6;
                3'd6: tx_src_lane = 3'd5;
                default: tx_src_lane = 3'd4;
                endcase
            end
            2'd3: begin
                // DAC39J84 config95/config96 diagnostic for the current
                // 0x3021/0x7654 DAC-side octetpath crossbar. If
                // octetpath_sel(n) selects the SerDes RX lane feeding JESD
                // lane n, this is the inverse map needed for the ZCU/FMC
                // physical order without reversing the upper four lanes.
                case (phys)
                3'd0: tx_src_lane = 3'd3;
                3'd1: tx_src_lane = 3'd0;
                3'd2: tx_src_lane = 3'd2;
                3'd3: tx_src_lane = 3'd1;
                3'd4: tx_src_lane = 3'd4;
                3'd5: tx_src_lane = 3'd5;
                3'd6: tx_src_lane = 3'd6;
                default: tx_src_lane = 3'd7;
                endcase
            end
            default: begin
                tx_src_lane = phys;
            end
            endcase
        end
    endfunction

    wire [255:0] litejesd_txdata_muxed;
    wire [31:0] litejesd_txcharisk_muxed;

    genvar tx_lane_index;
    generate
        for (tx_lane_index = 0; tx_lane_index < 8; tx_lane_index = tx_lane_index + 1) begin : gen_tx_lane_mux
            wire [2:0] src_lane = tx_src_lane(dac_tx_lane_mode_tx, tx_lane_index);
            assign litejesd_txdata_muxed[(32*tx_lane_index) +: 32] =
                litejesd_txdata[(32*src_lane) +: 32];
            assign litejesd_txcharisk_muxed[(4*tx_lane_index) +: 4] =
                litejesd_txcharisk[(4*src_lane) +: 4];
        end
    endgenerate

    assign gth_userdata_tx = litejesd_txdata_muxed;
    assign gth_txctrl2 = {
        4'd0, litejesd_txcharisk_muxed[31:28],
        4'd0, litejesd_txcharisk_muxed[27:24],
        4'd0, litejesd_txcharisk_muxed[23:20],
        4'd0, litejesd_txcharisk_muxed[19:16],
        4'd0, litejesd_txcharisk_muxed[15:12],
        4'd0, litejesd_txcharisk_muxed[11:8],
        4'd0, litejesd_txcharisk_muxed[7:4],
        4'd0, litejesd_txcharisk_muxed[3:0]
    };

`ifdef DAQ_WITH_GTH_TX_ILA
    wire [31:0] dac_tx_lane_map_debug = {
        tx_src_lane(dac_tx_lane_mode_tx, 3'd7),
        tx_src_lane(dac_tx_lane_mode_tx, 3'd6),
        tx_src_lane(dac_tx_lane_mode_tx, 3'd5),
        tx_src_lane(dac_tx_lane_mode_tx, 3'd4),
        tx_src_lane(dac_tx_lane_mode_tx, 3'd3),
        tx_src_lane(dac_tx_lane_mode_tx, 3'd2),
        tx_src_lane(dac_tx_lane_mode_tx, 3'd1),
        tx_src_lane(dac_tx_lane_mode_tx, 3'd0),
        8'h00
    };
    wire [63:0] dac_tx_control_debug = {
        4'hD,
        2'b00,
        dac_tag_source_enable_tx,
        dac_program_enable,
        dac_program_restart,
        litejesd_sync_pipe[2],
        litejesd_sysref_pipe[2],
        litejesd_ready_async,
        litejesd_active_async,
        dac_source_modes_tx,
        dac_physical_map_mode_tx,
        dac_active_converter_tx,
        dac_tx_lane_mode_tx,
        dac_sample_map_mode_tx,
        litejesd_status_async
    };

    ila_gth_tx_debug u_ila_gth_tx_debug (
        .clk    (gth_tx_usrclk2),
        .probe0  (dac_tx_control_debug),
        .probe1  (dac_debug_bram_words),
        .probe2  (dac_debug_source_words),
        .probe3  (dac_debug_native_words),
        .probe4  (dac_debug_preimage_words),
        .probe5  (dac_debug_physical_words),
        .probe6  (dac_debug_remap_in_words),
        .probe7  (dac_debug_remap_out_words),
        .probe8  (dac_debug_jesd_converter_words),
        .probe9  (litejesd_txdata),
        .probe10 (litejesd_txdata_muxed),
        .probe11 (litejesd_txcharisk),
        .probe12 (litejesd_txcharisk_muxed),
        .probe13 (dac_tx_lane_map_debug),
        .probe14 (litejesd_sine_async),
        .probe15 (litejesd_triangle_async)
    );
`endif
`else
    assign litejesd_active_async = 1'b0;
    assign litejesd_ready_async = 1'b0;
    assign litejesd_status_async = 32'd0;
    assign litejesd_triangle_async = 32'd0;
    assign litejesd_sine_async = 32'd0;
    assign dac_program_word0_async = 64'd0;
    assign dac_program_word1_async = 64'd0;
    assign dac_program_word2_async = 64'd0;
    assign dac_program_word3_async = 64'd0;
    assign dac_source_modes_tx = 8'd0;
    assign dac_program_status_async = 32'd0;
`ifdef DAQ_WITH_BRAM_DATAPLANE
    assign dac0_bram_addr = 32'd0;
    assign dac0_bram_clk = clk_200;
    assign dac0_bram_din = 64'd0;
    assign dac0_bram_en = 1'b0;
    assign dac0_bram_rst = 1'b0;
    assign dac0_bram_we = 8'd0;
    assign dac1_bram_addr = 32'd0;
    assign dac1_bram_clk = clk_200;
    assign dac1_bram_din = 64'd0;
    assign dac1_bram_en = 1'b0;
    assign dac1_bram_rst = 1'b0;
    assign dac1_bram_we = 8'd0;
    assign dac2_bram_addr = 32'd0;
    assign dac2_bram_clk = clk_200;
    assign dac2_bram_din = 64'd0;
    assign dac2_bram_en = 1'b0;
    assign dac2_bram_rst = 1'b0;
    assign dac2_bram_we = 8'd0;
    assign dac3_bram_addr = 32'd0;
    assign dac3_bram_clk = clk_200;
    assign dac3_bram_din = 64'd0;
    assign dac3_bram_en = 1'b0;
    assign dac3_bram_rst = 1'b0;
    assign dac3_bram_we = 8'd0;
`endif

    assign gth_userdata_tx = {8{32'hbcbc_bcbc}};
    assign gth_txctrl2 = {
        8'h0f, 8'h0f, 8'h0f, 8'h0f,
        8'h0f, 8'h0f, 8'h0f, 8'h0f
    };
`endif

    assign gth_txdata_lane0_async = gth_userdata_tx[31:0];
    assign gth_txctrl2_lane0_async = gth_txctrl2[7:0];

    gtwizard_ultrascale_0 u_gth (
        .gtwiz_userclk_tx_reset_in              (gth_reset_all),
        .gtwiz_userclk_tx_srcclk_out            (),
        .gtwiz_userclk_tx_usrclk_out            (gth_tx_usrclk),
        .gtwiz_userclk_tx_usrclk2_out           (gth_tx_usrclk2),
        .gtwiz_userclk_tx_active_out            (gth_tx_userclk_active),
        .gtwiz_userclk_rx_reset_in              (gth_reset_all),
        .gtwiz_userclk_rx_srcclk_out            (),
        .gtwiz_userclk_rx_usrclk_out            (gth_rx_usrclk),
        .gtwiz_userclk_rx_usrclk2_out           (gth_rx_usrclk2),
        .gtwiz_userclk_rx_active_out            (gth_rx_userclk_active),
        .gtwiz_reset_clk_freerun_in             (clk_125),
        .gtwiz_reset_all_in                     (gth_reset_all),
        .gtwiz_reset_tx_pll_and_datapath_in     (1'b0),
        .gtwiz_reset_tx_datapath_in             (1'b0),
        .gtwiz_reset_rx_pll_and_datapath_in     (1'b0),
        .gtwiz_reset_rx_datapath_in             (1'b0),
        .gtwiz_reset_rx_cdr_stable_out          (gth_reset_rx_cdr_stable),
        .gtwiz_reset_tx_done_out                (gth_reset_tx_done),
        .gtwiz_reset_rx_done_out                (gth_reset_rx_done),
        .gtwiz_userdata_tx_in                   (gth_userdata_tx),
        .gtwiz_userdata_rx_out                  (gth_userdata_rx),
        // ZCU102 HPC0: GBTCLK1 feeds quad 228/X1Y1/DP[4:7],
        // GBTCLK0 feeds quad 229/X1Y2/DP[0:3].  The GTH Wizard
        // vector index follows the common order X1Y1 then X1Y2.
        .gtrefclk00_in                          ({gbt0_refclk, gbt1_refclk}),
        .qpll0lock_out                          (gth_qpll0lock),
        .qpll0outclk_out                        (gth_qpll0outclk),
        .qpll0outrefclk_out                     (gth_qpll0outrefclk),
        .gthrxn_in                              (DAQ_GTH_RX_N),
        .gthrxp_in                              (DAQ_GTH_RX_P),
        .rx8b10ben_in                           (8'hff),
        .rxcommadeten_in                        (8'hff),
        .rxmcommaalignen_in                     (8'hff),
        .rxpcommaalignen_in                     (8'hff),
        .rxpolarity_in                          (gth_rx_polarity_ctrl),
        .tx8b10ben_in                           (8'hff),
        .txctrl0_in                             (128'd0),
        .txctrl1_in                             (128'd0),
        .txctrl2_in                             (gth_txctrl2),
        .txpolarity_in                          (rw_reg2[15:8]),
        .gthtxn_out                             (DAQ_GTH_TX_N),
        .gthtxp_out                             (DAQ_GTH_TX_P),
        .gtpowergood_out                        (gth_gtpowergood),
        .rxcdrlock_out                          (gth_rxcdrlock),
        .rxbyteisaligned_out                    (gth_rxbyteisaligned),
        .rxbyterealign_out                      (gth_rxbyterealign),
        .rxcommadet_out                         (gth_rxcommadet),
        .rxctrl0_out                            (gth_rxctrl0),
        .rxctrl1_out                            (gth_rxctrl1),
        .rxctrl2_out                            (gth_rxctrl2),
        .rxctrl3_out                            (gth_rxctrl3),
        .rxpmaresetdone_out                     (gth_rxpmaresetdone),
        .txpmaresetdone_out                     (gth_txpmaresetdone)
    );

    wire        gth_tx_clk_seen;
    wire        gth_rx_clk_seen;

    clock_activity_monitor #(
        .COUNTER_WIDTH     (16),
        .REF_WINDOW_CYCLES (MONITOR_WINDOW_CYCLES)
    ) u_gth_tx_userclk_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_clk   (gth_tx_usrclk2),
        .last_count (gth_tx_clk_count_short),
        .seen       (gth_tx_clk_seen)
    );

    clock_activity_monitor #(
        .COUNTER_WIDTH     (16),
        .REF_WINDOW_CYCLES (MONITOR_WINDOW_CYCLES)
    ) u_gth_rx_userclk_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_clk   (gth_rx_usrclk2),
        .last_count (gth_rx_clk_count_short),
        .seen       (gth_rx_clk_seen)
    );

    assign gth_tx_clk_count = {16'd0, gth_tx_clk_count_short};
    assign gth_rx_clk_count = {16'd0, gth_rx_clk_count_short};

    assign gth_qpll_locked_async = &gth_qpll0lock;
    assign gth_tx_ready_async = gth_reset_tx_done & gth_tx_userclk_active & gth_tx_clk_seen &
                                gth_qpll_locked_async & (&gth_gtpowergood) & (&gth_txpmaresetdone);
    assign gth_rx_ready_async = gth_reset_rx_done & gth_rx_userclk_active & gth_rx_clk_seen &
                                gth_qpll_locked_async & (&gth_rxpmaresetdone);

    assign gth_status_async = {
        14'd0,
        gth_reset_rx_cdr_stable,
        |gth_rxcommadet,
        |gth_rxbyterealign,
        &gth_rxbyteisaligned,
        gth_rx_clk_seen,
        gth_tx_clk_seen,
        &gth_rxcdrlock,
        &gth_rxpmaresetdone,
        &gth_txpmaresetdone,
        &gth_gtpowergood,
        gth_qpll_locked_async,
        gth_qpll0lock,
        gth_rx_userclk_active,
        gth_tx_userclk_active,
        gth_reset_rx_done,
        gth_reset_tx_done,
        gth_reset_all
    };

    assign gth_rx_status_async = {
        gth_rxbyteisaligned,
        gth_rxbyterealign,
        gth_rxcommadet,
        gth_rxcdrlock
    };

`ifdef DAQ_WITH_LITEJESD
    wire adc_rx_reset = gth_reset_all | ~gth_reset_rx_done |
                        ~gth_rx_userclk_active | ~gth_rx_clk_seen |
                        ~adc_auto_done;
    wire adc_rx_enable = ~adc_rx_reset;

    (* ASYNC_REG = "TRUE" *) reg [2:0] adc_rx_sysref_pipe = 3'b000;
    always @(posedge gth_rx_usrclk2) begin
        if (adc_rx_reset) begin
            adc_rx_sysref_pipe <= 3'b000;
        end else begin
            adc_rx_sysref_pipe <= {adc_rx_sysref_pipe[1:0], sysref_ibuf};
        end
    end

    adc_frontend u_adc_frontend (
        .jesd_clk                   (gth_rx_usrclk2),
        .jesd_rst                   (adc_rx_reset),
        .enable                     (adc_rx_enable),
        .sysref                     (adc_rx_sysref_pipe[2]),
        .ilas_check_enable          (adc_ilas_check_enable_ctrl),
        .stpl_enable                (adc_stpl_enable_ctrl),
        .raw_lane_select            (adc_raw_lane_select_ctrl),
        .capture_format             (adc_capture_format_ctrl),
        .adc0_use_physical_dp_order (adc1_use_physical_dp_order_ctrl),
        .adc1_use_physical_dp_order (adc2_use_physical_dp_order_ctrl),
        .gth_userdata_rx            (gth_userdata_rx),
        .gth_rxctrl0                (gth_rxctrl0),
        .gth_rxctrl1                (gth_rxctrl1),
        .gth_rxctrl3                (gth_rxctrl3),
        .gth_rxbyteisaligned        (gth_rxbyteisaligned),
        .gth_rxcdrlock              (gth_rxcdrlock),
        .gth_rxpmaresetdone         (gth_rxpmaresetdone),
        .adc0_sync_n                (adc1_sync_n_async),
        .adc1_sync_n                (adc2_sync_n_async),
        .adc0_ready                 (adc1_litejesd_ready_async),
        .adc1_ready                 (adc2_litejesd_ready_async),
        .adc0_status                (adc1_rx_status_async),
        .adc0_lane_status           (adc1_rx_lane_status_async),
        .adc0_event_counts          (adc1_rx_event_counts_async),
        .adc0_ch_a_low              (adc1_rx_sample_a_low_async),
        .adc0_ch_a_high             (adc1_rx_sample_a_high_async),
        .adc0_ch_b_low              (adc1_rx_sample_b_low_async),
        .adc0_ch_b_high             (adc1_rx_sample_b_high_async),
        .adc0_raw_lane              (adc1_rx_raw_lane_async),
        .adc1_status                (adc2_rx_status_async),
        .adc1_lane_status           (adc2_rx_lane_status_async),
        .adc1_event_counts          (adc2_rx_event_counts_async),
        .adc1_ch_a_low              (adc2_rx_sample_a_low_async),
        .adc1_ch_a_high             (adc2_rx_sample_a_high_async),
        .adc1_ch_b_low              (adc2_rx_sample_b_low_async),
        .adc1_ch_b_high             (adc2_rx_sample_b_high_async),
        .adc1_raw_lane              (adc2_rx_raw_lane_async),
        .adc_ch0                    (adc_ch0_async),
        .adc_ch1                    (adc_ch1_async),
        .adc_ch2                    (adc_ch2_async),
        .adc_ch3                    (adc_ch3_async)
    );

`ifdef DAQ_WITH_BRAM_DATAPLANE
    (* ASYNC_REG = "TRUE" *) reg [2:0] adc_capture_req_sync = 3'b000;

    always @(posedge gth_rx_usrclk2) begin
        if (adc_rx_reset) begin
            adc_capture_req_sync <= 3'b000;
        end else begin
            adc_capture_req_sync <= {adc_capture_req_sync[1:0], adc_capture_req_toggle};
        end
    end

    wire adc_capture_start = adc_capture_req_sync[2] ^ adc_capture_req_sync[1];
    wire [63:0] adc_ch0_capture = adc1_litejesd_ready_async ? adc_ch0_async : 64'd0;
    wire [63:0] adc_ch1_capture = adc1_litejesd_ready_async ? adc_ch1_async : 64'd0;
    wire [63:0] adc_ch2_capture = adc2_litejesd_ready_async ? adc_ch2_async : 64'd0;
    wire [63:0] adc_ch3_capture = adc2_litejesd_ready_async ? adc_ch3_async : 64'd0;

    adc_bram_capture u_adc_bram_capture (
        .clk           (gth_rx_usrclk2),
        .rst           (adc_rx_reset),
        .start         (adc_capture_start),
        .data_valid    (adc1_litejesd_ready_async | adc2_litejesd_ready_async),
        .ch0_low       (adc_ch0_capture[31:0]),
        .ch0_high      (adc_ch0_capture[63:32]),
        .ch1_low       (adc_ch1_capture[31:0]),
        .ch1_high      (adc_ch1_capture[63:32]),
        .ch2_low       (adc_ch2_capture[31:0]),
        .ch2_high      (adc_ch2_capture[63:32]),
        .ch3_low       (adc_ch3_capture[31:0]),
        .ch3_high      (adc_ch3_capture[63:32]),
        .bram_addr     (adc_bram_addr),
        .bram_clk      (adc_bram_clk),
        .adc0_bram_din (adc0_bram_din),
        .adc0_bram_dout(adc0_bram_dout),
        .adc1_bram_din (adc1_bram_din),
        .adc1_bram_dout(adc1_bram_dout),
        .bram_en       (adc_bram_en),
        .bram_rst      (adc_bram_rst),
        .bram_we       (adc_bram_we),
        .status        (adc_capture_status_async)
    );

`ifdef DAQ_WITH_PS_DDR_DMA
    // RW6 = number of 128-bit beats to capture per chip in ONE full-rate,
    // un-decimated burst (16 B/beat = 4 ns @ 1 GS/s; 512 MB = 0x0200_0000
    // beats). One adc_capture_start fires both chips together so the two ADC
    // captures line up 1-to-1. No decimation, no cyclic ring.
    wire [31:0] adc_capture_beats_rx;
    cdc_vector_sync #(
        .WIDTH (32)
    ) u_adc_capture_beats_sync (
        .dest_clk (gth_rx_usrclk2),
        .dest_rst (adc_rx_reset),
        .src      (rw_reg6),
        .dest     (adc_capture_beats_rx)
    );

    adc_burst_capture u_adc0_burst_capture (
        .clk           (gth_rx_usrclk2),
        .rd_clk        (clk_300),
        .rst           (adc_rx_reset),
        .start         (adc_capture_start),
        .capture_beats (adc_capture_beats_rx),
        .data_valid    (adc1_litejesd_ready_async),
        .frame_data    ({adc_ch1_capture, adc_ch0_capture}),
        .m_axis_tdata  (adc0_dma_axis_tdata),
        .m_axis_tkeep  (adc0_dma_axis_tkeep),
        .m_axis_tlast  (adc0_dma_axis_tlast),
        .m_axis_tvalid (adc0_dma_axis_tvalid),
        .m_axis_tready (adc0_dma_axis_tready),
        .status        (adc0_dma_status_async)
    );

    adc_burst_capture u_adc1_burst_capture (
        .clk           (gth_rx_usrclk2),
        .rd_clk        (clk_300),
        .rst           (adc_rx_reset),
        .start         (adc_capture_start),
        .capture_beats (adc_capture_beats_rx),
        .data_valid    (adc2_litejesd_ready_async),
        .frame_data    ({adc_ch3_capture, adc_ch2_capture}),
        .m_axis_tdata  (adc1_dma_axis_tdata),
        .m_axis_tkeep  (adc1_dma_axis_tkeep),
        .m_axis_tlast  (adc1_dma_axis_tlast),
        .m_axis_tvalid (adc1_dma_axis_tvalid),
        .m_axis_tready (adc1_dma_axis_tready),
        .status        (adc1_dma_status_async)
    );
`endif
`else
    assign adc_capture_status_async = 32'd0;
`ifdef DAQ_WITH_PS_DDR_DMA
    assign adc0_dma_axis_tdata = 128'd0;
    assign adc0_dma_axis_tkeep = 16'd0;
    assign adc0_dma_axis_tlast = 1'b0;
    assign adc0_dma_axis_tvalid = 1'b0;
    assign adc1_dma_axis_tdata = 128'd0;
    assign adc1_dma_axis_tkeep = 16'd0;
    assign adc1_dma_axis_tlast = 1'b0;
    assign adc1_dma_axis_tvalid = 1'b0;
    assign adc0_dma_status_async = 32'd0;
    assign adc1_dma_status_async = 32'd0;
`endif
`endif
`else
    assign adc1_sync_n_async = 1'b0;
    assign adc1_litejesd_ready_async = 1'b0;
    assign adc1_rx_status_async = 32'd0;
    assign adc1_rx_lane_status_async = 32'd0;
    assign adc1_rx_event_counts_async = 32'd0;
    assign adc1_rx_sample_a_low_async = 32'd0;
    assign adc1_rx_sample_a_high_async = 32'd0;
    assign adc1_rx_sample_b_low_async = 32'd0;
    assign adc1_rx_sample_b_high_async = 32'd0;
    assign adc1_rx_raw_lane_async = 32'd0;
    assign adc2_sync_n_async = 1'b0;
    assign adc2_litejesd_ready_async = 1'b0;
    assign adc2_rx_status_async = 32'd0;
    assign adc2_rx_lane_status_async = 32'd0;
    assign adc2_rx_event_counts_async = 32'd0;
    assign adc2_rx_sample_a_low_async = 32'd0;
    assign adc2_rx_sample_a_high_async = 32'd0;
    assign adc2_rx_sample_b_low_async = 32'd0;
    assign adc2_rx_sample_b_high_async = 32'd0;
    assign adc2_rx_raw_lane_async = 32'd0;
    assign adc_ch0_async = 64'd0;
    assign adc_ch1_async = 64'd0;
    assign adc_ch2_async = 64'd0;
    assign adc_ch3_async = 64'd0;
    assign adc_capture_status_async = 32'd0;
`ifdef DAQ_WITH_PS_DDR_DMA
    assign adc0_dma_axis_tdata = 128'd0;
    assign adc0_dma_axis_tkeep = 16'd0;
    assign adc0_dma_axis_tlast = 1'b0;
    assign adc0_dma_axis_tvalid = 1'b0;
    assign adc1_dma_axis_tdata = 128'd0;
    assign adc1_dma_axis_tkeep = 16'd0;
    assign adc1_dma_axis_tlast = 1'b0;
    assign adc1_dma_axis_tvalid = 1'b0;
    assign adc0_dma_status_async = 32'd0;
    assign adc1_dma_status_async = 32'd0;
`endif
`ifdef DAQ_WITH_BRAM_DATAPLANE
    assign adc_bram_addr = 32'd0;
    assign adc_bram_clk = clk_200;
    assign adc0_bram_din = 128'd0;
    assign adc1_bram_din = 128'd0;
    assign adc_bram_en = 1'b0;
    assign adc_bram_rst = 1'b0;
    assign adc_bram_we = 16'd0;
`endif
`endif

    assign gth_unused_reduce = ^gth_userdata_rx ^ ^gth_rxctrl0 ^ ^gth_rxctrl1 ^
                               ^gth_rxctrl2 ^ ^gth_rxctrl3 ^ ^gth_qpll0outclk ^
                               ^gth_qpll0outrefclk ^ ^gth_tx_clk_count ^
                               ^gth_rx_clk_count ^ gth_tx_usrclk ^ gth_rx_usrclk;
`else
    assign gth_status_async = 32'd0;
    assign gth_rx_status_async = 32'd0;
    assign gth_qpll_locked_async = 1'b0;
    assign gth_tx_ready_async = 1'b0;
    assign gth_rx_ready_async = 1'b0;
    assign gth_qpll0lock = 2'd0;
    assign gth_unused_reduce = 1'b0;
    assign litejesd_active_async = 1'b0;
    assign litejesd_ready_async = 1'b0;
    assign litejesd_status_async = 32'd0;
    assign litejesd_triangle_async = 32'd0;
    assign litejesd_sine_async = 32'd0;
    assign dac_program_word0_async = 64'd0;
    assign dac_program_word1_async = 64'd0;
    assign dac_program_word2_async = 64'd0;
    assign dac_program_word3_async = 64'd0;
    assign dac_program_status_async = 32'd0;
    assign gth_tx_clk_count = 32'd0;
    assign gth_rx_clk_count = 32'd0;
    assign gth_tx_clk_count_short = 16'd0;
    assign gth_rx_clk_count_short = 16'd0;
    assign gth_txdata_lane0_async = 32'd0;
    assign gth_txctrl2_lane0_async = 8'd0;
    assign adc1_sync_n_async = 1'b0;
    assign adc1_litejesd_ready_async = 1'b0;
    assign adc1_rx_status_async = 32'd0;
    assign adc1_rx_lane_status_async = 32'd0;
    assign adc1_rx_event_counts_async = 32'd0;
    assign adc1_rx_sample_a_low_async = 32'd0;
    assign adc1_rx_sample_a_high_async = 32'd0;
    assign adc1_rx_sample_b_low_async = 32'd0;
    assign adc1_rx_sample_b_high_async = 32'd0;
    assign adc1_rx_raw_lane_async = 32'd0;
    assign adc2_sync_n_async = 1'b0;
    assign adc2_litejesd_ready_async = 1'b0;
    assign adc2_rx_status_async = 32'd0;
    assign adc2_rx_lane_status_async = 32'd0;
    assign adc2_rx_event_counts_async = 32'd0;
    assign adc2_rx_sample_a_low_async = 32'd0;
    assign adc2_rx_sample_a_high_async = 32'd0;
    assign adc2_rx_sample_b_low_async = 32'd0;
    assign adc2_rx_sample_b_high_async = 32'd0;
    assign adc2_rx_raw_lane_async = 32'd0;
    assign adc_ch0_async = 64'd0;
    assign adc_ch1_async = 64'd0;
    assign adc_ch2_async = 64'd0;
    assign adc_ch3_async = 64'd0;
    assign adc_capture_status_async = 32'd0;
`ifdef DAQ_WITH_BRAM_DATAPLANE
    assign dac0_bram_addr = 32'd0;
    assign dac0_bram_clk = clk_200;
    assign dac0_bram_din = 64'd0;
    assign dac0_bram_en = 1'b0;
    assign dac0_bram_rst = 1'b0;
    assign dac0_bram_we = 8'd0;
    assign dac1_bram_addr = 32'd0;
    assign dac1_bram_clk = clk_200;
    assign dac1_bram_din = 64'd0;
    assign dac1_bram_en = 1'b0;
    assign dac1_bram_rst = 1'b0;
    assign dac1_bram_we = 8'd0;
    assign dac2_bram_addr = 32'd0;
    assign dac2_bram_clk = clk_200;
    assign dac2_bram_din = 64'd0;
    assign dac2_bram_en = 1'b0;
    assign dac2_bram_rst = 1'b0;
    assign dac2_bram_we = 8'd0;
    assign dac3_bram_addr = 32'd0;
    assign dac3_bram_clk = clk_200;
    assign dac3_bram_din = 64'd0;
    assign dac3_bram_en = 1'b0;
    assign dac3_bram_rst = 1'b0;
    assign dac3_bram_we = 8'd0;
    assign adc_bram_addr = 32'd0;
    assign adc_bram_clk = clk_200;
    assign adc0_bram_din = 128'd0;
    assign adc1_bram_din = 128'd0;
    assign adc_bram_en = 1'b0;
    assign adc_bram_rst = 1'b0;
    assign adc_bram_we = 16'd0;
`endif
`endif

    localparam integer FABRIC_DEBUG_SYNC_WIDTH = 527;
    wire [FABRIC_DEBUG_SYNC_WIDTH-1:0] fabric_debug_sync;
    cdc_vector_sync #(
        .WIDTH (FABRIC_DEBUG_SYNC_WIDTH)
    ) u_fabric_debug_sync (
        .dest_clk (clk_200),
        .dest_rst (fabric_rst),
        .src      ({
            gth_qpll0lock,
            gth_rx_ready_async,
            gth_tx_ready_async,
            gth_qpll_locked_async,
            litejesd_ready_async,
            litejesd_active_async,
            gth_txctrl2_lane0_async,
            gth_txdata_lane0_async,
            dac_neuron_debug_async,
            dac_program_status_async,
            dac_program_word3_async,
            dac_program_word2_async,
            dac_program_word1_async,
            dac_program_word0_async,
            litejesd_sine_async,
            litejesd_triangle_async,
            litejesd_status_async,
            gth_rx_status_async,
            gth_status_async
        }),
        .dest     (fabric_debug_sync)
    );

    assign {
        gth_qpll0lock_sync,
        gth_rx_ready,
        gth_tx_ready,
        gth_qpll_locked,
        litejesd_ready,
        litejesd_active,
        gth_txctrl2_lane0_debug,
        gth_txdata_lane0_debug,
        dac_neuron_debug_reg,
        dac_program_status_reg,
        dac_program_word3_reg,
        dac_program_word2_reg,
        dac_program_word1_reg,
        dac_program_word0_reg,
        litejesd_sine_word,
        litejesd_triangle_word,
        litejesd_status_reg,
        gth_rx_status_reg,
        gth_status_reg
    } = fabric_debug_sync;

    localparam integer ADC_RX_DEBUG_SYNC_WIDTH = 802;
    wire [ADC_RX_DEBUG_SYNC_WIDTH-1:0] adc_rx_debug_sync;
    cdc_vector_sync #(
        .WIDTH (ADC_RX_DEBUG_SYNC_WIDTH)
    ) u_adc_rx_debug_sync (
        .dest_clk (clk_200),
        .dest_rst (fabric_rst),
        .src      ({
            adc_capture_status_async,
            adc1_litejesd_ready_async,
            adc2_litejesd_ready_async,
            adc_ch3_async,
            adc_ch2_async,
            adc_ch1_async,
            adc_ch0_async,
            adc2_rx_raw_lane_async,
            adc2_rx_sample_b_high_async,
            adc2_rx_sample_b_low_async,
            adc2_rx_sample_a_high_async,
            adc2_rx_sample_a_low_async,
            adc2_rx_event_counts_async,
            adc2_rx_lane_status_async,
            adc2_rx_status_async,
            adc1_rx_raw_lane_async,
            adc1_rx_sample_b_high_async,
            adc1_rx_sample_b_low_async,
            adc1_rx_sample_a_high_async,
            adc1_rx_sample_a_low_async,
            adc1_rx_event_counts_async,
            adc1_rx_lane_status_async,
            adc1_rx_status_async
        }),
        .dest     (adc_rx_debug_sync)
    );

    assign {
        adc_capture_status_reg,
        adc1_litejesd_ready,
        adc2_litejesd_ready,
        adc_ch3_reg,
        adc_ch2_reg,
        adc_ch1_reg,
        adc_ch0_reg,
        adc2_rx_raw_lane_reg,
        adc2_rx_sample_b_high_reg,
        adc2_rx_sample_b_low_reg,
        adc2_rx_sample_a_high_reg,
        adc2_rx_sample_a_low_reg,
        adc2_rx_event_counts_reg,
        adc2_rx_lane_status_reg,
        adc2_rx_status_reg,
        adc1_rx_raw_lane_reg,
        adc1_rx_sample_b_high_reg,
        adc1_rx_sample_b_low_reg,
        adc1_rx_sample_a_high_reg,
        adc1_rx_sample_a_low_reg,
        adc1_rx_event_counts_reg,
        adc1_rx_lane_status_reg,
        adc1_rx_status_reg
    } = adc_rx_debug_sync;

    assign adc_frontend_status_reg = {
        8'hAD,
        adc_new_control_enable,
        adc_capture_format_ctrl,
        adc_raw_lane_select_ctrl,
        adc2_use_physical_dp_order_ctrl,
        adc1_use_physical_dp_order_ctrl,
        adc_stpl_enable_ctrl,
        adc_ilas_check_enable_ctrl,
        adc2_litejesd_ready,
        adc1_litejesd_ready,
        9'd0,
        rw_reg7[3:0]
    };

    always @* begin
        case (rw_reg7[4:0])
            5'd0: adc0_selected_debug_reg = adc1_rx_status_reg;
            5'd1: adc0_selected_debug_reg = adc1_rx_lane_status_reg;
            5'd2: adc0_selected_debug_reg = adc1_rx_event_counts_reg;
            5'd3: adc0_selected_debug_reg = adc1_rx_sample_a_low_reg;
            5'd4: adc0_selected_debug_reg = adc1_rx_sample_a_high_reg;
            5'd5: adc0_selected_debug_reg = adc1_rx_sample_b_low_reg;
            5'd6: adc0_selected_debug_reg = adc1_rx_sample_b_high_reg;
            5'd7: adc0_selected_debug_reg = adc1_rx_raw_lane_reg;
            default: adc0_selected_debug_reg = 32'd0;
        endcase
    end

    always @* begin
        case (rw_reg7[4:0])
            5'd0: adc1_selected_debug_reg = adc2_rx_status_reg;
            5'd1: adc1_selected_debug_reg = adc2_rx_lane_status_reg;
            5'd2: adc1_selected_debug_reg = adc2_rx_event_counts_reg;
            5'd3: adc1_selected_debug_reg = adc2_rx_sample_a_low_reg;
            5'd4: adc1_selected_debug_reg = adc2_rx_sample_a_high_reg;
            5'd5: adc1_selected_debug_reg = adc2_rx_sample_b_low_reg;
            5'd6: adc1_selected_debug_reg = adc2_rx_sample_b_high_reg;
            5'd7: adc1_selected_debug_reg = adc2_rx_raw_lane_reg;
            default: adc1_selected_debug_reg = 32'd0;
        endcase
    end

    always @* begin
        case (rw_reg7[9:8])
            2'd0: adc_channel_selected_reg = rw_reg7[10] ? adc_ch0_reg[63:32] : adc_ch0_reg[31:0];
            2'd1: adc_channel_selected_reg = rw_reg7[10] ? adc_ch1_reg[63:32] : adc_ch1_reg[31:0];
            2'd2: adc_channel_selected_reg = rw_reg7[10] ? adc_ch2_reg[63:32] : adc_ch2_reg[31:0];
            default: adc_channel_selected_reg = rw_reg7[10] ? adc_ch3_reg[63:32] : adc_ch3_reg[31:0];
        endcase
    end

    (* ASYNC_REG = "TRUE" *) reg [2:0] dac_sync_pipe = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [2:0] dac_alarm_pipe = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [2:0] hmc_sdio_pipe = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [2:0] dac_sdout_pipe = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [2:0] adc1_sdout_pipe = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [2:0] adc2_sdout_pipe = 3'b000;

    always @(posedge clk_200) begin
        dac_sync_pipe <= {dac_sync_pipe[1:0], dac_sync_raw};
        dac_alarm_pipe <= {dac_alarm_pipe[1:0], DAC_ALARM};
        hmc_sdio_pipe <= {hmc_sdio_pipe[1:0], hmc_sdio_in};
        dac_sdout_pipe <= {dac_sdout_pipe[1:0], DAC_SDOUT};
        adc1_sdout_pipe <= {adc1_sdout_pipe[1:0], ADC1_SDOUT};
        adc2_sdout_pipe <= {adc2_sdout_pipe[1:0], ADC2_SDOUT};
    end

    wire dac_sync_level = dac_sync_pipe[2];
    wire dac_alarm_level = dac_alarm_pipe[2];

    wire [31:0] hmc_auto_status_reg = {
        15'd0,
        hmc_restart_pulse,
        manual_spi_enable,
        hmc_auto_done,
        hmc_auto_busy,
        hmc_auto_reset,
        hmc_auto_cs_n,
        hmc_auto_sclk,
        hmc_auto_sdio_oe,
        hmc_auto_sdio_o,
        hmc_auto_step
    };
    wire [31:0] hmc_auto_last_write_reg = {
        12'd0,
        hmc_auto_last_addr,
        hmc_auto_last_data
    };
    wire hmc_id_rev_e = hmc_readback_id_word[23:0] == 24'h045201;
    wire hmc_id_legacy = hmc_readback_id_word[23:0] == 24'h301651;
    wire hmc_scratch_ok = hmc_readback_scratch_word[7:0] == 8'hAD;
    wire [31:0] hmc_readback_summary_reg = {
        3'd0,
        hmc_readback_done,
        hmc_readback_sdio_stuck,
        hmc_scratch_ok,
        hmc_id_rev_e,
        hmc_id_legacy,
        hmc_readback_index,
        hmc_readback_last_addr,
        hmc_readback_last_data
    };
    wire [31:0] adc1_analog_summary_reg = {
        4'hA,
        adc_readback_done1,
        adc_readback_ok1,
        adc_sdout_stuck1,
        1'b0,
        adc1_analog_word
    };
    wire [31:0] adc1_jesd_digital_summary_reg = {
        4'hB,
        adc_readback_done1,
        adc_readback_ok1,
        adc_sdout_stuck1,
        1'b0,
        adc1_jesd_digital_word
    };
    wire [31:0] adc1_jesd_analog_summary_reg = {
        8'hC1,
        adc_readback_done1,
        adc_readback_ok1,
        adc_sdout_stuck1,
        5'd0,
        adc1_jesd_analog_word
    };
    wire [31:0] adc2_analog_summary_reg = {
        4'hA,
        adc_readback_done2,
        adc_readback_ok2,
        adc_sdout_stuck2,
        1'b0,
        adc2_analog_word
    };
    wire [31:0] adc2_jesd_digital_summary_reg = {
        4'hB,
        adc_readback_done2,
        adc_readback_ok2,
        adc_sdout_stuck2,
        1'b0,
        adc2_jesd_digital_word
    };
    wire [31:0] adc2_jesd_analog_summary_reg = {
        8'hC2,
        adc_readback_done2,
        adc_readback_ok2,
        adc_sdout_stuck2,
        5'd0,
        adc2_jesd_analog_word
    };

    wire [31:0] raw_pin_reg = {
        CH4_ENDCC,
        CH3_ENDCC,
        CH2_ENDCC,
        CH1_ENDCC,
        adc2_sdout_pipe[2],
        ADC2_SDOUT,
        adc1_sdout_pipe[2],
        ADC1_SDOUT,
        adc_auto_done,
        adc_auto_busy,
        ADC2_CS_N,
        ADC1_CS_N,
        hmc_auto_done,
        hmc_auto_busy,
        hmc_auto_reset,
        hmc_auto_cs_n,
        hmc_auto_step[3:0],
        DAC_SDOUT,
        dac_sdout_pipe[2],
        hmc_sdio_in,
        hmc_sdio_pipe[2],
        DAC_ALARM,
        dac_alarm_level,
        dac_sync_raw,
        dac_sync_level,
        fmc_pg_m2c,
        fmc_pg_m2c,
        fmc_present,
        fmc_present
    };

    wire [31:0] build_id = 32'hDA01_003C;
    wire [31:0] litejesd_wave_word = {
        litejesd_sine_word[15:0],
        litejesd_triangle_word[15:0]
    };
    reg [31:0] dac_program_word_debug;

    always @* begin
        case (rw_reg2[30:28])
            3'd0: dac_program_word_debug = dac_program_word0_reg[31:0];
            3'd1: dac_program_word_debug = dac_program_word0_reg[63:32];
            3'd2: dac_program_word_debug = dac_program_word1_reg[31:0];
            3'd3: dac_program_word_debug = dac_program_word1_reg[63:32];
            3'd4: dac_program_word_debug = dac_program_word2_reg[31:0];
            3'd5: dac_program_word_debug = dac_program_word2_reg[63:32];
            3'd6: dac_program_word_debug = dac_program_word3_reg[31:0];
            default: dac_program_word_debug = dac_program_word3_reg[63:32];
        endcase
    end

    always @* begin
        case (rw_reg1[4:0])
            5'd0:  selected_count = hmc_auto_status_reg;
            5'd1:  selected_count = hmc_auto_last_write_reg;
            5'd2:  selected_count = raw_pin_reg;
            5'd3:  selected_count = build_id;
            5'd4:  selected_count = gth_status_reg;
            5'd5:  selected_count = gth_rx_status_reg;
            5'd6:  selected_count = litejesd_status_reg;
            5'd7:  selected_count = (rw_reg3[5:4] == 2'd3) ? dac_neuron_debug_reg :
                                     (rw_reg3[6] ? (rw_reg2[31] ? dac_program_word_debug :
                                                                 dac_program_status_reg) :
                                                   litejesd_wave_word);
            5'd8:  selected_count = hmc_readback_summary_reg;
            5'd9:  selected_count = hmc_readback_id_word;
            5'd10: selected_count = hmc_readback_alarm_word;
            5'd11: selected_count = hmc_readback_pll1_word;
            5'd12: selected_count = hmc_readback_pll2_word;
            5'd13: selected_count = hmc_readback_scratch_word;
            5'd14: selected_count = dac_auto_status_reg;
            5'd15: selected_count = dac_auto_last_write_reg;
            5'd16: selected_count = adc_auto_status_reg;
            5'd17: selected_count = adc1_analog_summary_reg;
            5'd18: selected_count = adc1_jesd_digital_summary_reg;
            5'd19: selected_count = adc1_jesd_analog_summary_reg;
            5'd20: selected_count = adc2_analog_summary_reg;
            5'd21: selected_count = adc2_jesd_digital_summary_reg;
            5'd22: selected_count = adc2_jesd_analog_summary_reg;
            5'd23: selected_count = adc_auto_last_write_reg;
            5'd24: selected_count = adc_auto_last_read_reg;
            5'd25: selected_count = adc1_rx_status_reg;
            5'd26: selected_count = adc1_rx_lane_status_reg;
            5'd27: selected_count = adc1_rx_event_counts_reg;
            5'd28: selected_count = adc1_rx_sample_a_low_reg;
            5'd29: selected_count = adc1_rx_sample_a_high_reg;
            5'd30: selected_count = adc1_rx_sample_b_low_reg;
            5'd31: selected_count = rw_reg2[31] ? adc_capture_status_reg :
                                     adc1_rx_raw_lane_reg;
            default: selected_count = 32'd0;
        endcase
    end

    assign status_reg = {
        dac_auto_done,
        dac_auto_busy,
        hmc_readback_sdio_stuck,
        hmc_readback_done,
        hmc_auto_done,
        hmc_auto_busy,
        hmc_auto_owns,
        rw_reg1[3:0],
        litejesd_ready,
        litejesd_active,
        gth_rx_ready,
        gth_tx_ready,
        gth_qpll_locked,
        dac_sync_level,
        dac_alarm_level,
        hmc_sdio_pipe[2],
        dac_sdout_pipe[2],
        manual_spi_enable,
        gbt1_seen,
        gbt0_seen,
        sysref_seen,
        clk_fmc_seen,
        DAC_TXEN,
        DAC_RESET_N,
        HMC_CLK_RESET,
        fmc_c2m_pg_status,
        fmc_pg_m2c,
        fmc_present,
        mmcm_locked
    };

    wire [31:0] ila_debug_flags = {
        hmc_readback_sdio_stuck,
        hmc_readback_done,
        adc_readback_ok1,
        adc_readback_ok2,
        adc_sdout_stuck1,
        adc_sdout_stuck2,
        hmc_auto_done,
        hmc_auto_busy,
        dac_auto_done,
        dac_auto_busy,
        adc_auto_done,
        adc_auto_busy,
        GPIO_LED,
        litejesd_ready,
        litejesd_active,
        gth_rx_ready,
        gth_tx_ready,
        gth_qpll_locked,
        dac_sync_level,
        dac_alarm_level,
        manual_spi_enable,
        ADC1_RESET,
        ADC2_RESET,
        sysref_seen,
        clk_fmc_seen
    };

    ila_fabric_debug u_ila_fabric_debug (
        .clk     (clk_200),
        .probe0  (status_reg),
        .probe1  (rw_reg0),
        .probe2  (rw_reg1),
        .probe3  (rw_reg2),
        .probe4  (selected_count),
        .probe5  (clk_fmc_count),
        .probe6  (sysref_count),
        .probe7  (raw_pin_reg),
        .probe8  (gth_status_reg),
        .probe9  (gth_rx_status_reg),
        .probe10 (hmc_readback_alarm_word),
        .probe11 (hmc_readback_pll1_word),
        .probe12 (gth_tx_clk_count),
        .probe13 (gth_rx_clk_count),
        .probe14 (hmc_readback_pll2_word),
        .probe15 (hmc_readback_id_word),
        .probe16 (ila_debug_flags),
        .probe17 (uart_debug_reg),
        .probe18 (uart_rx_edge_count),
        .probe19 (uart_tx_edge_count),
        .probe20 (adc1_rx_status_reg),
        .probe21 (adc1_rx_lane_status_reg),
        .probe22 (adc1_rx_event_counts_reg),
        .probe23 (adc1_rx_raw_lane_reg)
    );

    reg [27:0] fabric_clk_cnt = 28'd0;
    reg        fabric_led = 1'b0;
    always @(posedge clk_200) begin
        if (fabric_clk_cnt == 28'd199_999_999) begin
            fabric_clk_cnt <= 28'd0;
            fabric_led <= ~fabric_led;
        end else begin
            fabric_clk_cnt <= fabric_clk_cnt + 1'b1;
        end
    end

    assign GPIO_LED[0] = fabric_led;
    assign GPIO_LED[1] = fmc_present;
    assign GPIO_LED[2] = fmc_pg_m2c;
    assign GPIO_LED[3] = clk_fmc_seen;
    assign GPIO_LED[4] = sysref_seen;
`ifdef DAQ_WITH_GTH
`ifdef DAQ_WITH_LITEJESD
    assign GPIO_LED[5] = gth_qpll0lock_sync[0];
    assign GPIO_LED[6] = gth_qpll0lock_sync[1];
    assign GPIO_LED[7] = litejesd_ready;
`else
    assign GPIO_LED[5] = gth_qpll_locked;
    assign GPIO_LED[6] = gth_tx_ready;
    assign GPIO_LED[7] = gth_rx_ready;
`endif
`else
    assign GPIO_LED[5] = gbt0_seen | gbt1_seen;
    assign GPIO_LED[6] = ~dac_alarm_level;
    assign GPIO_LED[7] = dac_alarm_level;
`endif

    wire unused = clk_100 ^ clk_125 ^ gth_unused_reduce ^
                  ro_reg0_rdint ^ ro_reg1_rdint ^ ro_reg2_rdint ^
                  ro_reg3_rdint ^ rw_reg2[0] ^ rw_reg2[31] ^
                  rw_reg3[0] ^ rw_reg3[1] ^ rw_reg3[2] ^ rw_reg3[3] ^
                  rw_reg3[4] ^ rw_reg3[5] ^ rw_reg3[6] ^ dac_auto_step[0] ^
                  dac_auto_last_addr[0] ^ dac_auto_last_data[0] ^
                  adc_auto_step[0] ^ adc_auto_last_addr[0] ^
                  adc_auto_last_data[0] ^ adc_last_read_addr[0] ^
                  adc_last_read_data[0] ^ adc_last_read_chip ^
                  microblaze_reset;

endmodule
