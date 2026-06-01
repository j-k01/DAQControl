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

#define REG_BASE              XPAR_AXI4_REGISTER_FILE_0_S00_AXI_BASEADDR

#if defined(XPAR_DAC_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_ADC_CAPTURE_BRAM_CTRL_S_AXI_BASEADDR)
#define HAS_BRAM_DATAPLANE 1
#define DAC_PROGRAM_BRAM_BASE XPAR_DAC_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR
#define ADC_CAPTURE_BRAM_BASE XPAR_ADC_CAPTURE_BRAM_CTRL_S_AXI_BASEADDR
#else
#define HAS_BRAM_DATAPLANE 0
#endif
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
#define RW2_CAPTURE_STATUS_SEL (1u << 31)
#define RW2_LAUNCH_DEFAULT     RW2_ADC1_ILAS_BYPASS

#if HAS_BRAM_DATAPLANE
#define RW3_CAPTURE_START      (1u << 3)
#define RW3_CAPTURE_SRC_SHIFT  4
#define RW3_CAPTURE_SRC_MASK   (3u << RW3_CAPTURE_SRC_SHIFT)
#define RW3_DAC_PROGRAM_EN     (1u << 6)

#define CAPTURE_WORDS          262144u
#define CAPTURE_SYNC0          0xFEu
#define CAPTURE_SYNC1          0x10u
#define CAPTURE_SYNC2          0xCAu
#define CAPTURE_SYNC3          0xFEu
#define CAPTURE_STATUS_MARKER  0xC4000000u
#define CAPTURE_STATUS_DONE    (1u << 19)
#define CAPTURE_STATUS_BUSY    (1u << 18)
#endif

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

static u8 recv_byte_blocking(void)
{
    u8 c;

    while (XUartNs550_Recv(&uart, &c, 1) == 0)
        ;

    return c;
}

static u32 recv_le32_blocking(void)
{
    u32 value = 0;

    value |= (u32)recv_byte_blocking();
    value |= (u32)recv_byte_blocking() << 8;
    value |= (u32)recv_byte_blocking() << 16;
    value |= (u32)recv_byte_blocking() << 24;
    return value;
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

#if HAS_BRAM_DATAPLANE
static u32 capture_status_word(void)
{
    u32 old_rw1 = Xil_In32(RW_REG1);
    u32 old_rw2 = Xil_In32(RW_REG2);
    u32 status;

    Xil_Out32(RW_REG1, 31u);
    Xil_Out32(RW_REG2, old_rw2 | RW2_CAPTURE_STATUS_SEL);
    status = Xil_In32(RO_REG3);
    Xil_Out32(RW_REG2, old_rw2);
    Xil_Out32(RW_REG1, old_rw1);
    return status;
}

static void print_capture_status(void)
{
    u32 s = capture_status_word();

    send_str("CAPS = ");
    send_hex(s);
    send_str(" marker=");
    send_str((s & 0xFF000000u) == CAPTURE_STATUS_MARKER ? "ok" : "bad");
    send_str(" state=");
    send_uint((s >> 22) & 3u);
    send_str(" source=");
    send_uint((s >> 20) & 3u);
    send_str(" done=");
    send_uint((s & CAPTURE_STATUS_DONE) ? 1u : 0u);
    send_str(" busy=");
    send_uint((s & CAPTURE_STATUS_BUSY) ? 1u : 0u);
    send_str(" count=");
    send_uint(s & 0x3FFFFu);
    send_str("\r\n");
}

static int wait_capture_done(u32 *last_status)
{
    u32 timeout;

    for (timeout = 0; timeout < 10000000u; timeout++) {
        u32 s = capture_status_word();
        *last_status = s;
        if ((s & 0xFF000000u) == CAPTURE_STATUS_MARKER &&
            (s & CAPTURE_STATUS_DONE) != 0u) {
            return 1;
        }
    }

    return 0;
}

static void trigger_capture(u32 source)
{
    u32 rw3 = Xil_In32(RW_REG3);

    rw3 &= ~(RW3_CAPTURE_SRC_MASK | RW3_CAPTURE_START);
    rw3 |= ((source & 3u) << RW3_CAPTURE_SRC_SHIFT) | RW3_DAC_PROGRAM_EN;
    Xil_Out32(RW_REG3, rw3 | RW3_CAPTURE_START);
    Xil_Out32(RW_REG3, rw3);
}

static void cmd_program(u32 words)
{
    u32 i;

    if (words == 0u || words > CAPTURE_WORDS) {
        words = CAPTURE_WORDS;
    }

    send_str("PGRD ");
    send_uint(words);
    send_str("\r\n");

    for (i = 0; i < words; i++) {
        Xil_Out32(DAC_PROGRAM_BRAM_BASE + i * 4u, recv_le32_blocking());
    }

    send_str("OK PROG ");
    send_uint(words);
    send_str("\r\n");
}

static void cmd_capture(u32 words, u32 source)
{
    u32 status = 0;
    u32 i;

    if (words == 0u || words > CAPTURE_WORDS) {
        words = CAPTURE_WORDS;
    }

    trigger_capture(source);
    if (!wait_capture_done(&status)) {
        send_str("ERR capture timeout; ");
        send_hex(status);
        send_str("\r\n");
        return;
    }

    send_byte(CAPTURE_SYNC0);
    send_byte(CAPTURE_SYNC1);
    send_byte(CAPTURE_SYNC2);
    send_byte(CAPTURE_SYNC3);

    for (i = 0; i < words; i++) {
        u32 sample = Xil_In32(ADC_CAPTURE_BRAM_BASE + i * 4u);
        send_byte((u8)(sample & 0xFFu));
        send_byte((u8)((sample >> 8) & 0xFFu));
        send_byte((u8)((sample >> 16) & 0xFFu));
        send_byte((u8)((sample >> 24) & 0xFFu));
    }
}
#endif

static void cmd_help(void)
{
    send_str("DAQ_LAUNCH commands:\r\n");
    send_str("  HELP\r\n");
    send_str("  STAT             dump status, counters, controls\r\n");
    send_str("  RDRO n           read RO register 0..3\r\n");
    send_str("  RDRW n           read RW register 0..3\r\n");
    send_str("  WRTE n value     write RW register 0..3; use 0x prefix for hex masks\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("  PROG [n]         upload n little-endian 32-bit DAC program words after PGRD\r\n");
    send_str("  CAPS             print ADC BRAM capture status\r\n");
    send_str("  CAPT [n] [src]   restart DAC program, capture ADC BRAM, stream binary data\r\n");
#else
    send_str("  PROG/CAPS/CAPT   unavailable; rebuild with --with-bram-dataplane\r\n");
#endif
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
#if HAS_BRAM_DATAPLANE
    send_str("                 selector 7 returns DAC program player status when RW3[6]=1\r\n");
#endif
    send_str("RW2 ADC1 JESD RX: [24] bypass ILAS check, [25] STPL check, [26] DP order\r\n");
    send_str("                  firmware default is RW2=0x01000000 for ADC1 bring-up\r\n");
    send_str("                  [29:28] raw RX lane shown in selector 31/0x1F\r\n");
    send_str("RW3 restart pulses: [0] HMC, [1] DAC, [2] ADC\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("    [3] ADC BRAM capture/DAC program restart pulse, [5:4] capture source\r\n");
    send_str("    [6] DAC program BRAM mode enable; CAPT sets this automatically\r\n");
#endif
    send_str("    [31:8] DAC converter-1 sine DDS step; 0 uses hardware default 0x010000\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("Capture sources: 0=A low, 1=A high, 2=B low, 3=B high; full buffer is 262144 words\r\n");
#endif
    print_uart_config();
}

static void cmd_status(void)
{
    unsigned int i;
    u32 s;
    u32 rw2;

    for (i = 0; i < 4; i++)
        print_reg("RW", i, Xil_In32(rw_addr(i)));
    for (i = 0; i < 4; i++)
        print_reg("RO", i, Xil_In32(ro_addr(i)));

    s = Xil_In32(RO_REG0);
    rw2 = Xil_In32(RW_REG2);
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
    send_str("gth_gate: ");
    send_str((s & (1u << 27)) ? "hmc_done " : "hmc_not_done ");
    send_str((rw2 & (1u << 0)) ? "sw_reset_asserted " : "sw_reset_released ");
    send_str((s & (1u << 16)) ? "qpll_locked " : "qpll_unlocked ");
    send_str((s & (1u << 17)) ? "tx_ready " : "tx_not_ready ");
    send_str((s & (1u << 18)) ? "rx_ready " : "rx_not_ready ");
    send_str((s & (1u << 19)) ? "litejesd_active " : "litejesd_in_reset ");
    send_str((s & (1u << 20)) ? "litejesd_ready" : "litejesd_not_ready");
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
#if HAS_BRAM_DATAPLANE
    } else if (strncmp(cmd, "PROG", 4) == 0) {
        char *p = &cmd[4];
        u32 words = CAPTURE_WORDS;
        parse_u32_arg(&p, &words);
        cmd_program(words);
    } else if (strncmp(cmd, "CAPS", 4) == 0) {
        print_capture_status();
    } else if (strncmp(cmd, "CAPT", 4) == 0) {
        char *p = &cmd[4];
        u32 words = CAPTURE_WORDS;
        u32 source = 0;
        parse_u32_arg(&p, &words);
        parse_u32_arg(&p, &source);
        cmd_capture(words, source);
#else
    } else if (strncmp(cmd, "PROG", 4) == 0 ||
               strncmp(cmd, "CAPS", 4) == 0 ||
               strncmp(cmd, "CAPT", 4) == 0) {
        send_str("ERR BRAM dataplane not built; rebuild with --with-bram-dataplane\r\n");
#endif
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
