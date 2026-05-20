module top (
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
);

    wire clk_200;
    wire clk_100;
    wire mmcm_locked;

    clk_wiz_0 u_clk_wiz (
        .clk_in1_p (SYSCLK_P),
        .clk_in1_n (SYSCLK_N),
        .clk_out1  (clk_200),
        .clk_out2  (clk_100),
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

    wire [31:0] bram_dout;
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
        .BRAM_PORTB_0_addr    (32'd0),
        .BRAM_PORTB_0_clk     (clk_200),
        .BRAM_PORTB_0_din     (32'd0),
        .BRAM_PORTB_0_dout    (bram_dout),
        .BRAM_PORTB_0_en      (1'b0),
        .BRAM_PORTB_0_rst     (1'b0),
        .BRAM_PORTB_0_we      (4'd0),
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
    wire clk_fmc;
    IBUFDS #(
        .DIFF_TERM  ("TRUE"),
        .IOSTANDARD ("LVDS")
    ) u_clk_fmc_ibuf (
        .I  (FMC1_HPC_CLK0_M2C_P),
        .IB (FMC1_HPC_CLK0_M2C_N),
        .O  (clk_fmc_ibuf)
    );
    BUFG u_clk_fmc_bufg (
        .I (clk_fmc_ibuf),
        .O (clk_fmc)
    );

    wire sysref_ibuf;
    wire sysref_clk;
    IBUFDS #(
        .DIFF_TERM  ("TRUE"),
        .IOSTANDARD ("LVDS")
    ) u_sysref_ibuf (
        .I  (DAQ_SYSREF_P),
        .IB (DAQ_SYSREF_N),
        .O  (sysref_ibuf)
    );
    BUFG u_sysref_bufg (
        .I (sysref_ibuf),
        .O (sysref_clk)
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

    wire gbt0_odiv2;
    wire gbt1_odiv2;
    wire gbt0_clk;
    wire gbt1_clk;

    IBUFDS_GTE4 u_gbtclk0_ibuf (
        .I     (FMC1_HPC_GBTCLK0_M2C_C_P),
        .IB    (FMC1_HPC_GBTCLK0_M2C_C_N),
        .CEB   (1'b0),
        .O     (),
        .ODIV2 (gbt0_odiv2)
    );
    BUFG_GT u_gbtclk0_bufg (
        .I       (gbt0_odiv2),
        .CE      (1'b1),
        .CEMASK  (1'b0),
        .CLR     (1'b0),
        .CLRMASK (1'b0),
        .DIV     (3'b000),
        .O       (gbt0_clk)
    );

    IBUFDS_GTE4 u_gbtclk1_ibuf (
        .I     (FMC1_HPC_GBTCLK1_M2C_C_P),
        .IB    (FMC1_HPC_GBTCLK1_M2C_C_N),
        .CEB   (1'b0),
        .O     (),
        .ODIV2 (gbt1_odiv2)
    );
    BUFG_GT u_gbtclk1_bufg (
        .I       (gbt1_odiv2),
        .CE      (1'b1),
        .CEMASK  (1'b0),
        .CLR     (1'b0),
        .CLRMASK (1'b0),
        .DIV     (3'b000),
        .O       (gbt1_clk)
    );

    wire [31:0] gbt0_count;
    wire [31:0] gbt1_count;
    wire        clk_fmc_seen;
    wire        sysref_seen;
    wire        gbt0_seen;
    wire        gbt1_seen;

    clock_activity_monitor u_clk_fmc_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_clk   (clk_fmc),
        .last_count (clk_fmc_count),
        .seen       (clk_fmc_seen)
    );

    clock_activity_monitor u_sysref_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_clk   (sysref_clk),
        .last_count (sysref_count),
        .seen       (sysref_seen)
    );

    clock_activity_monitor u_gbt0_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_clk   (gbt0_clk),
        .last_count (gbt0_count),
        .seen       (gbt0_seen)
    );

    clock_activity_monitor u_gbt1_monitor (
        .ref_clk    (clk_200),
        .ref_rst    (fabric_rst),
        .test_clk   (gbt1_clk),
        .last_count (gbt1_count),
        .seen       (gbt1_seen)
    );

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
        case (rw_reg1[1:0])
            2'd0: selected_count = gbt0_count;
            2'd1: selected_count = gbt1_count;
            2'd2: selected_count = raw_pin_reg;
            default: selected_count = build_id;
        endcase
    end

    assign status_reg = {
        14'd0,
        rw_reg1[1:0],
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
    assign GPIO_LED[5] = gbt0_seen | gbt1_seen;
    assign GPIO_LED[6] = ~dac_alarm_level;
    assign GPIO_LED[7] = dac_alarm_level;

    wire unused = clk_100 ^ ro_reg0_rdint ^ ro_reg1_rdint ^ ro_reg2_rdint ^
                  ro_reg3_rdint ^ bram_dout[0] ^ rw_reg2[0] ^ rw_reg3[0];

endmodule
