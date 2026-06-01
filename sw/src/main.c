#include "xparameters.h"
#include "xuartns550.h"
#include "xil_io.h"
#include <stdlib.h>
#include <string.h>

#define UART_BAUD_RATE 115200
#define EXPECTED_UART_CLOCK_HZ 200000000U

#if XPAR_AXI_UART16550_0_CLOCK_FREQ_HZ != EXPECTED_UART_CLOCK_HZ
#error "AXI UART16550 clock mismatch: regenerate the XSA/BSP from create_project.tcl so the UART clock is 200 MHz."
#endif

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

#define RW2_ADC1_ILAS_BYPASS   (1u << 24)
#define RW2_LAUNCH_DEFAULT     RW2_ADC1_ILAS_BYPASS

static XUartNs550 uart;
static char cmd[96];
static int cmd_idx = 0;

static void firmware_marker(u32 stage)
{
    Xil_Out32(RW_REG1, 0xC0DE0000u | ((stage & 0xFFu) << 4));
}

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

static void send_uint(u32 val)
{
    char buf[11];
    int pos = 0;

    if (val == 0) {
        send_byte('0');
        return;
    }

    while (val != 0 && pos < (int)sizeof(buf)) {
        buf[pos++] = (char)('0' + (val % 10));
        val /= 10;
    }

    while (pos > 0)
        send_byte((u8)buf[--pos]);
}

static u32 uart_divisor(void)
{
    return (XPAR_AXI_UART16550_0_CLOCK_FREQ_HZ + (8u * UART_BAUD_RATE)) /
           (16u * UART_BAUD_RATE);
}

static u32 uart_actual_baud(void)
{
    return XPAR_AXI_UART16550_0_CLOCK_FREQ_HZ / (16u * uart_divisor());
}

static void print_uart_config(void)
{
    send_str("UART: clk=");
    send_uint(XPAR_AXI_UART16550_0_CLOCK_FREQ_HZ);
    send_str(" baud=");
    send_uint(UART_BAUD_RATE);
    send_str(" divisor=");
    send_uint(uart_divisor());
    send_str(" actual=");
    send_uint(uart_actual_baud());
    send_str("\r\n");
}

static u32 rw_addr(unsigned int idx)
{
    return RW_REG0 + (idx & 3u) * 4u;
}

static u32 ro_addr(unsigned int idx)
{
    return RO_REG0 + (idx & 3u) * 4u;
}

static int is_hex_alpha(char c)
{
    return (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

static u32 parse_u32_token(const char *s, char **endp)
{
    const char *p = s;
    int base = 10;

    while (*p == ' ' || *p == '\t')
        p++;

    if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X')) {
        base = 16;
    } else {
        const char *q = p;
        while (*q != '\0' && *q != ' ' && *q != '\t') {
            if (is_hex_alpha(*q)) {
                base = 16;
                break;
            }
            q++;
        }
    }

    return (u32)strtoul(p, endp, base);
}

static int parse_u32_arg(char **cursor, u32 *value)
{
    char *endp;
    char *p = *cursor;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0')
        return 0;

    *value = parse_u32_token(p, &endp);
    if (endp == p)
        return 0;

    *cursor = endp;
    return 1;
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
    send_str("  WRTE n value     write RW register 0..3; use 0x prefix for hex masks\r\n");
    send_str("\r\n");
    send_str("RW0 control bits:\r\n");
    send_str("  [0]/[31] FMC_C2M_PG override unused on ZCU102 HPC0\r\n");
    send_str("  [1] HMC reset, [2] DAC_RESET_N, [3] DAC_TXEN\r\n");
    send_str("  [4] ADC1 reset, [5] ADC2 reset when manual SPI is enabled\r\n");
    send_str("  [16:22] manual DAC/HMC SPI pins, enabled by [30]\r\n");
    send_str("  [31] FMC_C2M_PG override enable\r\n");
    send_str("RW1[4:0] selects RO3: 0=HMC status, 1=HMC last write\r\n");
    send_str("                 2=raw pins, 3=build ID, 4=GTH, 5=GTH lanes\r\n");
    send_str("                 6=LiteJESD, 7=DAC wave {sine,triangle}, 8..D=HMC readbacks\r\n");
    send_str("                 E=DAC status, F=DAC last write\r\n");
    send_str("                 16/0x10=ADC status, 17..19/0x11..0x13=ADC1\r\n");
    send_str("                 20..22/0x14..0x16=ADC2, 23/0x17=ADC write, 24/0x18=ADC read\r\n");
    send_str("                 25..31/0x19..0x1F=ADC1 JESD RX debug/sample\r\n");
    send_str("RW2 ADC1 JESD RX: [24] bypass ILAS check, [25] STPL check, [26] DP order\r\n");
    send_str("                  firmware default is RW2=0x01000000 for ADC1 bring-up\r\n");
    send_str("                  [29:28] raw RX lane shown in selector 31/0x1F\r\n");
    send_str("RW3 restart pulses: [0] HMC, [1] DAC, [2] ADC\r\n");
    send_str("    [31:8] DAC converter-1 sine DDS step; 0 uses hardware default 0x010000\r\n");
    print_uart_config();
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
    print_uart_config();
}

static void launch_defaults(void)
{
    u32 ctrl = CTRL_DAC_CS_N | CTRL_HMC_CS_N;

    Xil_Out32(RW_REG0, ctrl);
    Xil_Out32(RW_REG2, RW2_LAUNCH_DEFAULT);
    Xil_Out32(RW_REG3, 0);
}

static void process_cmd(void)
{
    if (strncmp(cmd, "HELP", 4) == 0) {
        cmd_help();
    } else if (strncmp(cmd, "STAT", 4) == 0) {
        cmd_status();
    } else if (strncmp(cmd, "RDRO", 4) == 0) {
        char *p = &cmd[5];
        u32 idx;
        if (!parse_u32_arg(&p, &idx) || idx > 3u) {
            send_str("ERR RDRO expects register 0..3\r\n");
            return;
        }
        print_reg("RO", idx, Xil_In32(ro_addr(idx)));
    } else if (strncmp(cmd, "RDRW", 4) == 0) {
        char *p = &cmd[5];
        u32 idx;
        if (!parse_u32_arg(&p, &idx) || idx > 3u) {
            send_str("ERR RDRW expects register 0..3\r\n");
            return;
        }
        print_reg("RW", idx, Xil_In32(rw_addr(idx)));
    } else if (strncmp(cmd, "WRTE", 4) == 0) {
        char *p = &cmd[5];
        u32 idx;
        u32 val;
        if (!parse_u32_arg(&p, &idx) || idx > 3u) {
            send_str("ERR WRTE expects register 0..3 and value\r\n");
            return;
        }
        if (!parse_u32_arg(&p, &val)) {
            send_str("ERR WRTE expects register 0..3 and value\r\n");
            return;
        }
        Xil_Out32(rw_addr(idx), val);
        send_str("OK\r\n");
    } else {
        send_str("ERR unknown command; try HELP\r\n");
    }
}

int main(void)
{
    firmware_marker(1);
    launch_defaults();
    firmware_marker(2);

    XUartNs550_Initialize(&uart, XPAR_AXI_UART16550_0_DEVICE_ID);
    firmware_marker(3);
    XUartNs550_SetLineControlReg(&uart, XUN_LCR_8_DATA_BITS);
    firmware_marker(4);

    u32 base = uart.BaseAddress;
    u32 divisor = uart_divisor();
    Xil_Out32(base + 0x0C, Xil_In32(base + 0x0C) | 0x80);
    firmware_marker(5);
    Xil_Out32(base + 0x00, divisor & 0xFF);
    Xil_Out32(base + 0x04, (divisor >> 8) & 0xFF);
    Xil_Out32(base + 0x0C, Xil_In32(base + 0x0C) & ~0x80);
    firmware_marker(6);

    send_str("DAQ_LAUNCH MicroBlaze ready\r\n");
    firmware_marker(7);
    print_uart_config();
    send_str("Type HELP or STAT\r\n");
    firmware_marker(8);

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
