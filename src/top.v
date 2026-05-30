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

    // ZCU102 HPC1 routes FMC power/presence through fixed board logic and
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

    wire manual_spi_enable = rw_reg0[30];

    assign HMC_CLK_RESET = rw_reg0[1];
    assign DAC_RESET_N   = rw_reg0[2];
    assign DAC_TXEN      = rw_reg0[3];
    assign ADC1_RESET    = rw_reg0[4];
    assign ADC2_RESET    = rw_reg0[5];

    assign DAC_CS_N      = manual_spi_enable ? rw_reg0[16] : 1'b1;
    assign DAC_SCLK      = manual_spi_enable ? rw_reg0[17] : 1'b0;
    assign DAC_SDIN      = manual_spi_enable ? rw_reg0[18] : 1'b0;

    assign HMC_CLK_CS_N  = manual_spi_enable ? rw_reg0[19] : 1'b1;
    assign HMC_CLK_SCLK  = manual_spi_enable ? rw_reg0[20] : 1'b0;
    assign HMC_CLK_SDIO  = (manual_spi_enable && rw_reg0[22]) ? rw_reg0[21] : 1'bz;
    wire hmc_sdio_in = HMC_CLK_SDIO;

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

    wire [31:0] gth_status_reg;
    wire [31:0] gth_rx_status_reg;
    wire        gth_qpll_locked;
    wire        gth_tx_ready;
    wire        gth_rx_ready;
    wire        gth_unused_reduce;

`ifdef DAQ_WITH_GTH
    wire        gth_reset_all = fabric_rst | rw_reg2[0];
    wire        gth_tx_userclk_active;
    wire        gth_rx_userclk_active;
    wire        gth_tx_usrclk;
    wire        gth_tx_usrclk2;
    wire        gth_rx_usrclk;
    wire        gth_rx_usrclk2;
    wire        gth_reset_rx_cdr_stable;
    wire        gth_reset_tx_done;
    wire        gth_reset_rx_done;
    wire [1:0]  gth_qpll0lock;
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

    wire [63:0] gth_txctrl2_comma = {
        8'h0f, 8'h0f, 8'h0f, 8'h0f,
        8'h0f, 8'h0f, 8'h0f, 8'h0f
    };

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
        .gtwiz_userdata_tx_in                   ({8{32'hbcbc_bcbc}}),
        .gtwiz_userdata_rx_out                  (gth_userdata_rx),
        .gtrefclk00_in                          ({gbt1_refclk, gbt0_refclk}),
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
        .txctrl2_in                             (gth_txctrl2_comma),
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

    wire [31:0] gth_tx_clk_count;
    wire [31:0] gth_rx_clk_count;
    wire        gth_tx_clk_seen;
    wire        gth_rx_clk_seen;

    clock_activity_monitor #(
        .REF_WINDOW_CYCLES (MONITOR_WINDOW_CYCLES)
    ) u_gth_tx_userclk_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_clk   (gth_tx_usrclk2),
        .last_count (gth_tx_clk_count),
        .seen       (gth_tx_clk_seen)
    );

    clock_activity_monitor #(
        .REF_WINDOW_CYCLES (MONITOR_WINDOW_CYCLES)
    ) u_gth_rx_userclk_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_clk   (gth_rx_usrclk2),
        .last_count (gth_rx_clk_count),
        .seen       (gth_rx_clk_seen)
    );

    assign gth_qpll_locked = &gth_qpll0lock;
    assign gth_tx_ready = gth_reset_tx_done & gth_tx_userclk_active & gth_tx_clk_seen &
                          gth_qpll_locked & (&gth_gtpowergood) & (&gth_txpmaresetdone);
    assign gth_rx_ready = gth_reset_rx_done & gth_rx_userclk_active & gth_rx_clk_seen &
                          gth_qpll_locked & (&gth_rxpmaresetdone);

    assign gth_status_reg = {
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
        gth_qpll_locked,
        gth_qpll0lock,
        gth_rx_userclk_active,
        gth_tx_userclk_active,
        gth_reset_rx_done,
        gth_reset_tx_done,
        gth_reset_all
    };

    assign gth_rx_status_reg = {
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
    assign gth_status_reg = 32'd0;
    assign gth_rx_status_reg = 32'd0;
    assign gth_qpll_locked = 1'b0;
    assign gth_tx_ready = 1'b0;
    assign gth_rx_ready = 1'b0;
    assign gth_unused_reduce = 1'b0;
`endif

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

    wire [31:0] raw_pin_reg = {
        20'd0,
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

    wire [31:0] build_id = 32'hDA01_0001;

    always @* begin
        case (rw_reg1[2:0])
            3'd0: selected_count = gbt0_count;
            3'd1: selected_count = gbt1_count;
            3'd2: selected_count = raw_pin_reg;
            3'd3: selected_count = build_id;
            3'd4: selected_count = gth_status_reg;
            3'd5: selected_count = gth_rx_status_reg;
            default: selected_count = 32'd0;
        endcase
    end

    assign status_reg = {
        10'd0,
        rw_reg1[2:0],
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
    assign GPIO_LED[5] = gth_qpll_locked;
    assign GPIO_LED[6] = gth_tx_ready;
    assign GPIO_LED[7] = gth_rx_ready;
`else
    assign GPIO_LED[5] = gbt0_seen | gbt1_seen;
    assign GPIO_LED[6] = ~dac_alarm_level;
    assign GPIO_LED[7] = dac_alarm_level;
`endif

    wire unused = clk_100 ^ clk_125 ^ gth_unused_reduce ^
                  ro_reg0_rdint ^ ro_reg1_rdint ^ ro_reg2_rdint ^
                  ro_reg3_rdint ^ rw_reg2[0] ^ rw_reg3[0];

endmodule
