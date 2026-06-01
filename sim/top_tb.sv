`timescale 1ns/1ps

module top_tb;
    reg SYSCLK_P = 1'b0;
    wire SYSCLK_N = ~SYSCLK_P;
    reg CPU_RESET = 1'b1;
    wire [7:0] GPIO_LED;

    reg UART_RXD = 1'b1;
    wire UART_TXD;

    reg FMC1_HPC_CLK0_M2C_P = 1'b0;
    wire FMC1_HPC_CLK0_M2C_N = ~FMC1_HPC_CLK0_M2C_P;
    reg FMC1_HPC_GBTCLK0_M2C_C_P = 1'b0;
    wire FMC1_HPC_GBTCLK0_M2C_C_N = ~FMC1_HPC_GBTCLK0_M2C_C_P;
    reg FMC1_HPC_GBTCLK1_M2C_C_P = 1'b0;
    wire FMC1_HPC_GBTCLK1_M2C_C_N = ~FMC1_HPC_GBTCLK1_M2C_C_P;

    reg DAQ_SYSREF_P = 1'b0;
    wire DAQ_SYSREF_N = ~DAQ_SYSREF_P;
    reg DAC_SYNC_P = 1'b1;
    wire DAC_SYNC_N = ~DAC_SYNC_P;

    wire DAC_SCLK;
    wire DAC_SDIN;
    reg DAC_SDOUT = 1'b1;
    wire DAC_CS_N;
    wire DAC_TXEN;
    wire DAC_RESET_N;
    reg DAC_ALARM = 1'b0;

    wire ADC1_RESET;
    wire ADC2_RESET;
    wire ADC_SCLK;
    wire ADC_SDIN;
    reg ADC1_SDOUT = 1'b1;
    reg ADC2_SDOUT = 1'b1;
    wire ADC1_CS_N;
    wire ADC2_CS_N;
    wire ADC1_SYNC_N;

    wire HMC_CLK_RESET;
    wire HMC_CLK_CS_N;
    wire HMC_CLK_SCLK;
    tri  HMC_CLK_SDIO;
    reg  HMC_CLK_SDIO_PULL = 1'b1;
    assign HMC_CLK_SDIO = HMC_CLK_SDIO_PULL;

    top #(
        .MONITOR_WINDOW_CYCLES (32)
    ) uut (
        .SYSCLK_P                  (SYSCLK_P),
        .SYSCLK_N                  (SYSCLK_N),
        .CPU_RESET                 (CPU_RESET),
        .GPIO_LED                  (GPIO_LED),
        .UART_RXD                  (UART_RXD),
        .UART_TXD                  (UART_TXD),
        .FMC1_HPC_CLK0_M2C_P       (FMC1_HPC_CLK0_M2C_P),
        .FMC1_HPC_CLK0_M2C_N       (FMC1_HPC_CLK0_M2C_N),
        .FMC1_HPC_GBTCLK0_M2C_C_P  (FMC1_HPC_GBTCLK0_M2C_C_P),
        .FMC1_HPC_GBTCLK0_M2C_C_N  (FMC1_HPC_GBTCLK0_M2C_C_N),
        .FMC1_HPC_GBTCLK1_M2C_C_P  (FMC1_HPC_GBTCLK1_M2C_C_P),
        .FMC1_HPC_GBTCLK1_M2C_C_N  (FMC1_HPC_GBTCLK1_M2C_C_N),
        .DAQ_SYSREF_P              (DAQ_SYSREF_P),
        .DAQ_SYSREF_N              (DAQ_SYSREF_N),
        .DAC_SYNC_P                (DAC_SYNC_P),
        .DAC_SYNC_N                (DAC_SYNC_N),
        .DAC_SCLK                  (DAC_SCLK),
        .DAC_SDIN                  (DAC_SDIN),
        .DAC_SDOUT                 (DAC_SDOUT),
        .DAC_CS_N                  (DAC_CS_N),
        .DAC_TXEN                  (DAC_TXEN),
        .DAC_RESET_N               (DAC_RESET_N),
        .DAC_ALARM                 (DAC_ALARM),
        .ADC1_RESET                (ADC1_RESET),
        .ADC2_RESET                (ADC2_RESET),
        .ADC_SCLK                  (ADC_SCLK),
        .ADC_SDIN                  (ADC_SDIN),
        .ADC1_SDOUT                (ADC1_SDOUT),
        .ADC2_SDOUT                (ADC2_SDOUT),
        .ADC1_CS_N                 (ADC1_CS_N),
        .ADC2_CS_N                 (ADC2_CS_N),
        .ADC1_SYNC_N               (ADC1_SYNC_N),
        .HMC_CLK_RESET             (HMC_CLK_RESET),
        .HMC_CLK_CS_N              (HMC_CLK_CS_N),
        .HMC_CLK_SCLK              (HMC_CLK_SCLK),
        .HMC_CLK_SDIO              (HMC_CLK_SDIO)
    );

    always #1.667 SYSCLK_P = ~SYSCLK_P;
    always #5.000 FMC1_HPC_CLK0_M2C_P = ~FMC1_HPC_CLK0_M2C_P;
    always #20.000 DAQ_SYSREF_P = ~DAQ_SYSREF_P;
    always #4.000 FMC1_HPC_GBTCLK0_M2C_C_P = ~FMC1_HPC_GBTCLK0_M2C_C_P;
    always #4.500 FMC1_HPC_GBTCLK1_M2C_C_P = ~FMC1_HPC_GBTCLK1_M2C_C_P;

    integer errors = 0;

    task expect_bit;
        input value;
        input expected;
        input [511:0] name;
        begin
            if (value !== expected) begin
                $display("FAIL: %0s expected %0b got %0b at %0t", name, expected, value, $time);
                errors = errors + 1;
            end else begin
                $display("PASS: %0s = %0b", name, value);
            end
        end
    endtask

    initial begin
        $dumpfile("top_tb.vcd");
        $dumpvars(0, top_tb);

        #75 CPU_RESET = 1'b0;
        #900;

        expect_bit(GPIO_LED[1], 1'b1, "FMC present placeholder LED");
        expect_bit(GPIO_LED[2], 1'b1, "FMC power-good placeholder LED");
        expect_bit(GPIO_LED[3], 1'b1, "CLK_FMC activity LED");
        expect_bit(GPIO_LED[4], 1'b1, "SYSREF activity LED");
        expect_bit(GPIO_LED[5], 1'b0, "reserved LED");
        expect_bit(GPIO_LED[6], 1'b1, "DAC alarm deasserted LED");
        expect_bit(GPIO_LED[7], 1'b0, "error LED with DAC alarm low");
        expect_bit(DAC_CS_N, 1'b1, "DAC chip-select idle high");
        expect_bit(ADC1_CS_N, 1'b1, "ADC1 chip-select idle high before HMC init");
        expect_bit(ADC2_CS_N, 1'b1, "ADC2 chip-select idle high before HMC init");
        expect_bit(ADC1_RESET, 1'b1, "ADC1 reset asserted before HMC init");
        expect_bit(ADC2_RESET, 1'b1, "ADC2 reset asserted before HMC init");
        expect_bit(HMC_CLK_CS_N, 1'b1, "HMC chip-select idle high");
        expect_bit(DAC_TXEN, 1'b0, "DAC TX disabled by default");

        DAC_ALARM = 1'b1;
        #50;
        expect_bit(GPIO_LED[6], 1'b0, "DAC alarm asserted clears OK LED");
        expect_bit(GPIO_LED[7], 1'b1, "DAC alarm asserted lights error LED");

        if (errors == 0) begin
            $display("TEST PASSED");
        end else begin
            $display("TEST FAILED with %0d errors", errors);
            $fatal(1);
        end
        $finish;
    end
endmodule
