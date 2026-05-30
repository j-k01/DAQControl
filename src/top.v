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
    wire mmcm_locked;

    clk_wiz_0 u_clk_wiz (
        .clk_in1_p (SYSCLK_P),
        .clk_in1_n (SYSCLK_N),
        .clk_out1  (clk_200),
        .clk_out2  (clk_100),
        .clk_out3  (clk_125),
        .locked    (mmcm_locked)
    );

    wire fabric_rst = CPU_RESET | ~mmcm_locked;

    wire [31:0] rw_reg0;
    wire [31:0] rw_reg1;
    wire [31:0] rw_reg2;
    wire [31:0] rw_reg3;

    wire ro_reg0_rdint;
    wire ro_reg1_rdint;
    wire ro_reg2_rdint;
    wire ro_reg3_rdint;

    wire [31:0] status_reg;
    wire [31:0] clk_fmc_count;
    wire [31:0] sysref_count;
    reg  [31:0] selected_count;

    // ZCU102 HPC0 routes FMC power/presence through fixed board logic and
    // I2C/system-controller paths, not simple PL GPIO pins as on VC709.
    wire fmc_present = 1'b1;
    wire fmc_pg_m2c = 1'b1;
    wire fmc_c2m_pg_status = 1'b1;

    microblaze_bd_wrapper u_microblaze (
        .Clk                  (clk_200),
        .reset                (CPU_RESET),
        .rs232_uart_txd       (UART_TXD),
        .rs232_uart_rxd       (UART_RXD),
        .RW_REG0_0            (rw_reg0),
        .RW_REG1_0            (rw_reg1),
        .RW_REG2_0            (rw_reg2),
        .RW_REG3_0            (rw_reg3),
        .RO_REG0_IN_0         (status_reg),
        .RO_REG0_WE_0         (1'b1),
        .RO_REG1_IN_0         (clk_fmc_count),
        .RO_REG1_WE_0         (1'b1),
        .RO_REG2_IN_0         (sysref_count),
        .RO_REG2_WE_0         (1'b1),
        .RO_REG3_IN_0         (selected_count),
        .RO_REG3_WE_0         (1'b1),
        .RO_REG0_RDINT_0      (ro_reg0_rdint),
        .RO_REG1_RDINT_0      (ro_reg1_rdint),
        .RO_REG2_RDINT_0      (ro_reg2_rdint),
        .RO_REG3_RDINT_0      (ro_reg3_rdint)
    );

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

    wire manual_spi_enable = rw_reg0[30];
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
    wire [31:0] hmc_readback_id_word;
    wire [31:0] hmc_readback_alarm_word;
    wire [31:0] hmc_readback_pll1_word;
    wire [31:0] hmc_readback_pll2_word;
    wire hmc_auto_owns = ~manual_spi_enable;

    hmc7044_init #(
        .CLK_HZ           (200_000_000),
        .SPI_HZ           (1_000_000),
        .RESET_ASSERT_US  (10_000),
        .RESET_RELEASE_US (10_000)
    ) u_hmc7044_init (
        .clk         (clk_200),
        .rst         (fabric_rst),
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
        .readback_id_word    (hmc_readback_id_word),
        .readback_alarm_word (hmc_readback_alarm_word),
        .readback_pll1_word  (hmc_readback_pll1_word),
        .readback_pll2_word  (hmc_readback_pll2_word)
    );

    assign HMC_CLK_RESET = hmc_auto_owns ? hmc_auto_reset : rw_reg0[1];
`ifdef DAQ_WITH_LITEJESD
    assign DAC_RESET_N   = ~fabric_rst;
    assign DAC_TXEN      = gth_tx_ready_async;
`else
    assign DAC_RESET_N   = rw_reg0[2];
    assign DAC_TXEN      = rw_reg0[3];
`endif
    assign ADC1_RESET    = rw_reg0[4];
    assign ADC2_RESET    = rw_reg0[5];

    assign DAC_CS_N      = manual_spi_enable ? rw_reg0[16] : 1'b1;
    assign DAC_SCLK      = manual_spi_enable ? rw_reg0[17] : 1'b0;
    assign DAC_SDIN      = manual_spi_enable ? rw_reg0[18] : 1'b0;

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

`ifdef DAQ_WITH_GTH
    wire        gth_reset_all = fabric_rst | rw_reg2[0] | ~hmc_auto_done;
    wire        gth_tx_userclk_active;
    wire        gth_rx_userclk_active;
    wire        gth_tx_usrclk;
    wire        gth_tx_usrclk2;
    wire        gth_rx_usrclk;
    wire        gth_rx_usrclk2;
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

    daq_litejesd_dac_tx_path u_litejesd_dac_tx_path (
        .jesd_clk         (gth_tx_usrclk2),
        .jesd_rst         (litejesd_reset),
        .phy_tx_clk       (gth_tx_usrclk2),
        .phy_tx_rst       (litejesd_phy_tx_rst),
        .enable           (litejesd_active_async),
        .stpl_enable      (1'b0),
        .sysref           (litejesd_sysref_pipe[2]),
        .sync_n           (litejesd_sync_pipe[2]),
        .active_converter (3'd0),
        .triangle_step    (16'd256),
        .litejesd_ready   (litejesd_ready_async),
        .status           (litejesd_status_async),
        .triangle_word    (litejesd_triangle_async),
        .gth_txdata       (litejesd_txdata),
        .gth_txcharisk    (litejesd_txcharisk)
    );

    assign gth_userdata_tx = litejesd_txdata;
    assign gth_txctrl2 = {
        4'd0, litejesd_txcharisk[31:28],
        4'd0, litejesd_txcharisk[27:24],
        4'd0, litejesd_txcharisk[23:20],
        4'd0, litejesd_txcharisk[19:16],
        4'd0, litejesd_txcharisk[15:12],
        4'd0, litejesd_txcharisk[11:8],
        4'd0, litejesd_txcharisk[7:4],
        4'd0, litejesd_txcharisk[3:0]
    };

`ifdef DAQ_WITH_GTH_TX_ILA
    ila_gth_tx_debug u_ila_gth_tx_debug (
        .clk    (gth_tx_usrclk2),
        .probe0 (gth_userdata_tx[31:0]),
        .probe1 (gth_userdata_tx[63:32]),
        .probe2 (gth_txctrl2),
        .probe3 (litejesd_status_async),
        .probe4 (litejesd_triangle_async),
        .probe5 ({
            19'd0,
            litejesd_sysref_pipe,
            litejesd_sync_pipe,
            litejesd_ready_async,
            litejesd_active_async,
            gth_qpll0lock,
            gth_tx_userclk_active,
            gth_reset_tx_done,
            litejesd_reset
        })
    );
`endif
`else
    assign litejesd_active_async = 1'b0;
    assign litejesd_ready_async = 1'b0;
    assign litejesd_status_async = 32'd0;
    assign litejesd_triangle_async = 32'd0;

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
        .rxpolarity_in                          (rw_reg2[23:16]),
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
    assign gth_tx_clk_count = 32'd0;
    assign gth_rx_clk_count = 32'd0;
    assign gth_tx_clk_count_short = 16'd0;
    assign gth_rx_clk_count_short = 16'd0;
    assign gth_txdata_lane0_async = 32'd0;
    assign gth_txctrl2_lane0_async = 8'd0;
`endif

    wire [167:0] fabric_debug_sync;
    cdc_vector_sync #(
        .WIDTH (168)
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
        litejesd_triangle_word,
        litejesd_status_reg,
        gth_rx_status_reg,
        gth_status_reg
    } = fabric_debug_sync;

    (* ASYNC_REG = "TRUE" *) reg [2:0] dac_sync_pipe = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [2:0] dac_alarm_pipe = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [2:0] hmc_sdio_pipe = 3'b000;
    (* ASYNC_REG = "TRUE" *) reg [2:0] dac_sdout_pipe = 3'b000;

    always @(posedge clk_200) begin
        dac_sync_pipe <= {dac_sync_pipe[1:0], dac_sync_raw};
        dac_alarm_pipe <= {dac_alarm_pipe[1:0], DAC_ALARM};
        hmc_sdio_pipe <= {hmc_sdio_pipe[1:0], hmc_sdio_in};
        dac_sdout_pipe <= {dac_sdout_pipe[1:0], DAC_SDOUT};
    end

    wire dac_sync_level = dac_sync_pipe[2];
    wire dac_alarm_level = dac_alarm_pipe[2];

    wire [31:0] hmc_auto_status_reg = {
        16'd0,
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
    wire [31:0] hmc_readback_summary_reg = {
        4'd0,
        hmc_readback_done,
        hmc_readback_sdio_stuck,
        hmc_id_rev_e,
        hmc_id_legacy,
        hmc_readback_index,
        hmc_readback_last_addr,
        hmc_readback_last_data
    };

    wire [31:0] raw_pin_reg = {
        12'd0,
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

    wire [31:0] build_id = 32'hDA01_0003;

    always @* begin
        case (rw_reg1[3:0])
            4'd0:  selected_count = hmc_auto_status_reg;
            4'd1:  selected_count = hmc_auto_last_write_reg;
            4'd2:  selected_count = raw_pin_reg;
            4'd3:  selected_count = build_id;
            4'd4:  selected_count = gth_status_reg;
            4'd5:  selected_count = gth_rx_status_reg;
            4'd6:  selected_count = litejesd_status_reg;
            4'd7:  selected_count = litejesd_triangle_word;
            4'd8:  selected_count = hmc_readback_summary_reg;
            4'd9:  selected_count = hmc_readback_id_word;
            4'd10: selected_count = hmc_readback_alarm_word;
            4'd11: selected_count = hmc_readback_pll1_word;
            4'd12: selected_count = hmc_readback_pll2_word;
            default: selected_count = 32'd0;
        endcase
    end

    assign status_reg = {
        2'd0,
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
        hmc_auto_done,
        hmc_auto_busy,
        hmc_auto_reset,
        hmc_auto_cs_n,
        GPIO_LED,
        litejesd_ready,
        litejesd_active,
        gth_rx_ready,
        gth_tx_ready,
        gth_qpll_locked,
        dac_sync_raw,
        dac_sync_level,
        DAC_ALARM,
        dac_alarm_level,
        manual_spi_enable,
        DAC_TXEN,
        DAC_RESET_N,
        HMC_CLK_RESET,
        sysref_seen,
        clk_fmc_seen,
        fabric_rst,
        CPU_RESET,
        mmcm_locked
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
        .probe16 (ila_debug_flags)
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
                  ro_reg3_rdint ^ rw_reg2[0] ^ rw_reg3[0];

endmodule
