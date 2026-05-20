#include "xparameters.h"
#include "xuartns550.h"
#include "xil_io.h"
#include <stdlib.h>
#include <string.h>

#define UART_BAUD_RATE 250000

#define REG_BASE  XPAR_AXI4_REGISTER_FILE_0_S00_AXI_BASEADDR
#define RW_REG0   (REG_BASE + 0x00)
#define RW_REG1   (REG_BASE + 0x04)
#define RW_REG2   (REG_BASE + 0x08)
#define RW_REG3   (REG_BASE + 0x0C)
#define RO_REG0   (REG_BASE + 0x10)
#define RO_REG1   (REG_BASE + 0x14)
#define RO_REG2   (REG_BASE + 0x18)
#define RO_REG3   (REG_BASE + 0x1C)

#define CTRL_FMC_PG_VALUE      (1u << 0)
#define CTRL_HMC_RESET         (1u << 1)
#define CTRL_DAC_RESET_N       (1u << 2)
#define CTRL_DAC_TXEN          (1u << 3)
#define CTRL_ADC1_RESET        (1u << 4)
#define CTRL_ADC2_RESET        (1u << 5)
#define CTRL_DAC_CS_N          (1u << 16)
#define CTRL_DAC_SCLK          (1u << 17)
#define CTRL_DAC_SDIN          (1u << 18)
#define CTRL_HMC_CS_N          (1u << 19)
#define CTRL_HMC_SCLK          (1u << 20)
#define CTRL_HMC_SDIO_OUT      (1u << 21)
#define CTRL_HMC_SDIO_OE       (1u << 22)
#define CTRL_SPI_MANUAL_EN     (1u << 30)
#define CTRL_FMC_PG_OVERRIDE   (1u << 31)

static XUartNs550 uart;
static char cmd[96];
static int cmd_idx = 0;

static void send_byte(u8 c)
{
    while ((XUartNs550_GetLineStatusReg(uart.BaseAddress) & XUN_LSR_TX_BUFFER_EMPTY) == 0)
        ;
    XUartNs550_Send(&uart, &c, 1);
}

static void send_str(const char *s)
{
    while (*s)
        send_byte((u8)*s++);
}

static void send_hex(u32 val)
{
    int i;
    send_str("0x");
    for (i = 7; i >= 0; i--)
        send_byte((u8)"0123456789ABCDEF"[(val >> (i * 4)) & 0xF]);
}

static u32 rw_addr(unsigned int idx)
{
    return RW_REG0 + (idx & 3u) * 4u;
}

static u32 ro_addr(unsigned int idx)
{
    return RO_REG0 + (idx & 3u) * 4u;
}

static void print_reg(const char *name, unsigned int idx, u32 val)
{
    send_str(name);
    send_byte('0' + (u8)(idx & 3u));
    send_str(" = ");
    send_hex(val);
    send_str("\r\n");
}

static void cmd_help(void)
{
    send_str("DAQ_LAUNCH commands:\r\n");
    send_str("  HELP\r\n");
    send_str("  STAT             dump status, counters, controls\r\n");
    send_str("  RDRO n           read RO register 0..3\r\n");
    send_str("  RDRW n           read RW register 0..3\r\n");
    send_str("  WRTE n value     write RW register 0..3\r\n");
    send_str("\r\n");
    send_str("RW0 control bits:\r\n");
    send_str("  [0]/[31] FMC_C2M_PG override unused on ZCU102 HPC1\r\n");
    send_str("  [1] HMC reset, [2] DAC_RESET_N, [3] DAC_TXEN\r\n");
    send_str("  [4] ADC1 reset, [5] ADC2 reset\r\n");
    send_str("  [16:22] manual DAC/HMC SPI pins, enabled by [30]\r\n");
    send_str("  [31] FMC_C2M_PG override enable\r\n");
    send_str("RW1[1:0] selects RO3: 0=GBT0, 1=GBT1, 2=raw pins, 3=build ID\r\n");
}

static void cmd_status(void)
{
    unsigned int i;

    for (i = 0; i < 4; i++)
        print_reg("RW", i, Xil_In32(rw_addr(i)));
    for (i = 0; i < 4; i++)
        print_reg("RO", i, Xil_In32(ro_addr(i)));

    u32 s = Xil_In32(RO_REG0);
    send_str("decoded: ");
    send_str((s & (1u << 0)) ? "mmcm " : "no_mmcm ");
    send_str((s & (1u << 1)) ? "present " : "not_present ");
    send_str((s & (1u << 2)) ? "pg_m2c " : "no_pg_m2c ");
    send_str((s & (1u << 7)) ? "clk_fmc " : "no_clk_fmc ");
    send_str((s & (1u << 8)) ? "sysref " : "no_sysref ");
    send_str((s & (1u << 9)) ? "gbt0 " : "no_gbt0 ");
    send_str((s & (1u << 10)) ? "gbt1 " : "no_gbt1 ");
    send_str((s & (1u << 14)) ? "dac_alarm " : "no_dac_alarm ");
    send_str((s & (1u << 15)) ? "dac_sync_high" : "dac_sync_low");
    send_str("\r\n");
}

static void launch_defaults(void)
{
    u32 ctrl = CTRL_DAC_CS_N | CTRL_HMC_CS_N;

    Xil_Out32(RW_REG0, ctrl);
    Xil_Out32(RW_REG1, 0);
    Xil_Out32(RW_REG2, 0);
    Xil_Out32(RW_REG3, 0);
}

static void process_cmd(void)
{
    if (strncmp(cmd, "HELP", 4) == 0) {
        cmd_help();
    } else if (strncmp(cmd, "STAT", 4) == 0) {
        cmd_status();
    } else if (strncmp(cmd, "RDRO", 4) == 0) {
        unsigned int idx = (unsigned int)strtoul(&cmd[5], NULL, 0);
        print_reg("RO", idx, Xil_In32(ro_addr(idx)));
    } else if (strncmp(cmd, "RDRW", 4) == 0) {
        unsigned int idx = (unsigned int)strtoul(&cmd[5], NULL, 0);
        print_reg("RW", idx, Xil_In32(rw_addr(idx)));
    } else if (strncmp(cmd, "WRTE", 4) == 0) {
        char *p = &cmd[5];
        unsigned int idx = (unsigned int)strtoul(p, &p, 0);
        u32 val = (u32)strtoul(p, NULL, 0);
        Xil_Out32(rw_addr(idx), val);
        send_str("OK\r\n");
    } else {
        send_str("ERR unknown command; try HELP\r\n");
    }
}

int main(void)
{
    XUartNs550_Initialize(&uart, XPAR_AXI_UART16550_0_DEVICE_ID);
    XUartNs550_SetLineControlReg(&uart, XUN_LCR_8_DATA_BITS);

    u32 base = uart.BaseAddress;
    u32 divisor = XPAR_AXI_UART16550_0_CLOCK_FREQ_HZ / (16 * UART_BAUD_RATE);
    Xil_Out32(base + 0x0C, Xil_In32(base + 0x0C) | 0x80);
    Xil_Out32(base + 0x00, divisor & 0xFF);
    Xil_Out32(base + 0x04, (divisor >> 8) & 0xFF);
    Xil_Out32(base + 0x0C, Xil_In32(base + 0x0C) & ~0x80);

    launch_defaults();

    send_str("DAQ_LAUNCH MicroBlaze ready\r\n");
    send_str("Type HELP or STAT\r\n");

    u8 c;
    while (1) {
        if (XUartNs550_Recv(&uart, &c, 1) > 0) {
            if (c == '\r' || c == '\n') {
                if (cmd_idx > 0) {
                    send_str("\r\n");
                    cmd[cmd_idx] = '\0';
                    process_cmd();
                }
                cmd_idx = 0;
            } else if (cmd_idx < (int)sizeof(cmd) - 1) {
                cmd[cmd_idx++] = (char)c;
                send_byte(c);
            }
        }
    }

    return 0;
}
