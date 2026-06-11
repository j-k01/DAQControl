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

#if defined(XPAR_DAC0_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_DAC1_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_DAC2_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_DAC3_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_ADC0_CAPTURE_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_ADC1_CAPTURE_BRAM_CTRL_S_AXI_BASEADDR)
#define HAS_BRAM_DATAPLANE 1
#define DAC_PROGRAM_CHANNELS 4u
#define DAC_PROGRAM_FRAMES 4096u
#define DAC_PROGRAM_WORDS_PER_CHANNEL (DAC_PROGRAM_FRAMES * 2u)
#define ADC0_CAPTURE_BRAM_BASE XPAR_ADC0_CAPTURE_BRAM_CTRL_S_AXI_BASEADDR
#define ADC1_CAPTURE_BRAM_BASE XPAR_ADC1_CAPTURE_BRAM_CTRL_S_AXI_BASEADDR
#define ADC_CAPTURE_FRAMES 4096u
#define ADC_CAPTURE_WORDS_PER_FRAME 8u
#define ADC_CAPTURE_WORDS_PER_CHIP_FRAME 4u
#else
#define HAS_BRAM_DATAPLANE 0
#endif

#if defined(XPAR_AXI_DMA_0_BASEADDR) && defined(XPAR_AXI_DMA_1_BASEADDR)
#define HAS_PS_DDR_DMA 1
#define ADC_DMA_CHIPS 2u
#define ADC_DMA0_BASE XPAR_AXI_DMA_0_BASEADDR
#define ADC_DMA1_BASE XPAR_AXI_DMA_1_BASEADDR
#define ADC_DMA0_DDR_BASE 0x10000000u
#define ADC_DMA1_DDR_BASE 0x10020000u
#define ADC_DMA_FRAME_BYTES 16u
#else
#define HAS_PS_DDR_DMA 0
#endif
#define RW_REG0   (REG_BASE + 0x00)
#define RW_REG1   (REG_BASE + 0x04)
#define RW_REG2   (REG_BASE + 0x08)
#define RW_REG3   (REG_BASE + 0x0C)
#define RW_REG4   (REG_BASE + 0x10)
#define RW_REG5   (REG_BASE + 0x14)
#define RW_REG6   (REG_BASE + 0x18)
#define RW_REG7   (REG_BASE + 0x1C)
#define RO_REG0   (REG_BASE + 0x20)
#define RO_REG1   (REG_BASE + 0x24)
#define RO_REG2   (REG_BASE + 0x28)
#define RO_REG3   (REG_BASE + 0x2C)
#define RO_REG4   (REG_BASE + 0x30)
#define RO_REG5   (REG_BASE + 0x34)
#define RO_REG6   (REG_BASE + 0x38)
#define RO_REG7   (REG_BASE + 0x3C)

#define CTRL_FMC_PG_VALUE      (1u << 0)
#define CTRL_HMC_RESET         (1u << 1)
#define CTRL_DAC_RESET_N       (1u << 2)
#define CTRL_DAC_TXEN          (1u << 3)
#define CTRL_ADC1_RESET        (1u << 4)
#define CTRL_ADC2_RESET        (1u << 5)
#define CTRL_ADC_CH1_ENDCC     (1u << 6)
#define CTRL_ADC_CH2_ENDCC     (1u << 7)
#define CTRL_ADC_CH3_ENDCC     (1u << 8)
#define CTRL_ADC_CH4_ENDCC     (1u << 9)
#define CTRL_ADC_ENDCC_MASK    (CTRL_ADC_CH1_ENDCC | CTRL_ADC_CH2_ENDCC | \
                                CTRL_ADC_CH3_ENDCC | CTRL_ADC_CH4_ENDCC)
#define CTRL_DAC_CS_N          (1u << 16)
#define CTRL_DAC_SCLK          (1u << 17)
#define CTRL_DAC_SDIN          (1u << 18)
#define CTRL_HMC_CS_N          (1u << 19)
#define CTRL_HMC_SCLK          (1u << 20)
#define CTRL_HMC_SDIO_OUT      (1u << 21)
#define CTRL_HMC_SDIO_OE       (1u << 22)
#define CTRL_ADC_TEST_MODE_SHIFT 26
#define CTRL_ADC_TEST_MODE_MASK  (7u << CTRL_ADC_TEST_MODE_SHIFT)
#define CTRL_ADC_TEST_REQ        (1u << 29)
#define CTRL_SPI_MANUAL_EN     (1u << 30)
#define CTRL_FMC_PG_OVERRIDE   (1u << 31)

#define RW2_ADC1_ILAS_BYPASS   (1u << 24)
#define RW2_ADC1_STPL_CHECK    (1u << 25)
#define RW2_ADC1_DP_ORDER      (1u << 26)
#define RW2_ADC1_RX_POL_SHIFT  16
#define RW2_ADC1_RX_POL_MASK   (0xFFu << RW2_ADC1_RX_POL_SHIFT)
#define RW2_ADC1_RAW_SHIFT     28
#define RW2_ADC1_RAW_MASK      (3u << RW2_ADC1_RAW_SHIFT)
#define RW2_ADC1_CAPTURE_FORMAT_SHIFT 28
#define RW2_ADC1_CAPTURE_FORMAT_MASK  (3u << RW2_ADC1_CAPTURE_FORMAT_SHIFT)
#define RW2_GTH_RESET          (1u << 0)
#define RW2_DAC_SAMPLE_MAP_SHIFT 1
#define RW2_DAC_SAMPLE_MAP_MASK  (3u << RW2_DAC_SAMPLE_MAP_SHIFT)
#define RW2_DAC_TX_LANE_SHIFT    3
#define RW2_DAC_TX_LANE_MASK     (3u << RW2_DAC_TX_LANE_SHIFT)
#define RW2_DAC_CONV_SHIFT       5
#define RW2_DAC_CONV_MASK        (7u << RW2_DAC_CONV_SHIFT)
#define RW2_DAC_TX_POL_SHIFT     8
#define RW2_DAC_TX_POL_MASK      (0xFFu << RW2_DAC_TX_POL_SHIFT)
#define RW2_CAPTURE_STATUS_SEL (1u << 31)
#define RW2_LAUNCH_DEFAULT     ((0u << RW2_DAC_SAMPLE_MAP_SHIFT) | \
                                (3u << RW2_DAC_TX_LANE_SHIFT))

#define RW5_ADC_CTRL_ENABLE       (1u << 31)
#define RW5_ADC_CAPTURE_FORMAT_SHIFT 0
#define RW5_ADC_CAPTURE_FORMAT_MASK  (3u << RW5_ADC_CAPTURE_FORMAT_SHIFT)
#define RW5_ADC_RAW_SHIFT         2
#define RW5_ADC_RAW_MASK          (3u << RW5_ADC_RAW_SHIFT)
#define RW5_ADC0_DP_ORDER         (1u << 4)
#define RW5_ADC1_DP_ORDER         (1u << 5)
#define RW5_ADC_ILAS_BYPASS       (1u << 8)
#define RW5_ADC_STPL_CHECK        (1u << 9)
#define RW5_ADC_RX_POL_SHIFT      16
#define RW5_ADC_RX_POL_MASK       (0xFFu << RW5_ADC_RX_POL_SHIFT)
#define RW5_ADC_TEST_CHIP_MASK_SHIFT 24
#define RW5_ADC_TEST_CHIP_MASK    (3u << RW5_ADC_TEST_CHIP_MASK_SHIFT)
#define RW5_LAUNCH_DEFAULT        (RW5_ADC_CTRL_ENABLE | RW5_ADC_ILAS_BYPASS)

#define RO0_DAC_DONE           (1u << 31)
#define RO0_DAC_BUSY           (1u << 30)
#define RO0_HMC_DONE           (1u << 27)
#define RO0_GTH_QPLL_LOCKED    (1u << 16)
#define RO0_GTH_TX_READY       (1u << 17)
#define RO0_LITEJESD_ACTIVE    (1u << 19)
#define RO0_LITEJESD_READY     (1u << 20)
#define RO0_TX_LINK_READY      (RO0_HMC_DONE | RO0_GTH_QPLL_LOCKED | \
                                RO0_GTH_TX_READY | RO0_LITEJESD_ACTIVE | \
                                RO0_LITEJESD_READY)

#if HAS_BRAM_DATAPLANE
#define RW3_CAPTURE_START      (1u << 3)
#define RW3_DAC_PROGRAM_EN     (1u << 6)
#endif
#define RW3_DAC_SOURCE_SHIFT   4
#define RW3_DAC_SOURCE_MASK    (3u << RW3_DAC_SOURCE_SHIFT)
#define RW3_IZH_CFG_STROBE     (1u << 7)
#define RW3_IZH_CFG_PARAM_SHIFT 8
#define RW3_IZH_CFG_CHANNEL_SHIFT 12
#define RW3_IZH_CFG_ALL        (1u << 14)

#if HAS_BRAM_DATAPLANE
#define CAPTURE_SYNC0          0xFEu
#define CAPTURE_SYNC1          0x10u
#define CAPTURE_SYNC2          0xCAu
#define CAPTURE_SYNC3          0xFEu
#define CAPTURE_STATUS_MARKER  0xC4000000u
#define CAPTURE_STATUS_DONE    (1u << 19)
#define CAPTURE_STATUS_BUSY    (1u << 18)

static const u32 dac_program_bram_base[DAC_PROGRAM_CHANNELS] = {
    XPAR_DAC0_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR,
    XPAR_DAC1_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR,
    XPAR_DAC2_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR,
    XPAR_DAC3_PROGRAM_BRAM_CTRL_S_AXI_BASEADDR
};
#endif

#if HAS_PS_DDR_DMA
#define DMA_S2MM_DMACR       0x30u
#define DMA_S2MM_DMASR       0x34u
#define DMA_S2MM_DA          0x48u
#define DMA_S2MM_DA_MSB      0x4Cu
#define DMA_S2MM_LENGTH      0x58u
#define DMA_DMACR_RS         (1u << 0)
#define DMA_DMACR_RESET      (1u << 2)
#define DMA_DMASR_HALTED     (1u << 0)
#define DMA_DMASR_IDLE       (1u << 1)
#define DMA_DMASR_ERR_MASK   ((1u << 4) | (1u << 5) | (1u << 6))
#define DMA_DMASR_IRQ_MASK   ((1u << 12) | (1u << 13) | (1u << 14))

static const u32 adc_dma_base[ADC_DMA_CHIPS] = {
    ADC_DMA0_BASE,
    ADC_DMA1_BASE
};

static const u32 adc_dma_ddr_base[ADC_DMA_CHIPS] = {
    ADC_DMA0_DDR_BASE,
    ADC_DMA1_DDR_BASE
};
#endif

static XUartNs550 uart;
static char cmd[96];
static int cmd_idx = 0;

static void pulse_neuron_config(u32 channel, u32 all_channels, u32 param, u32 value);

struct neuron_profile {
    const char *name;
    const char *alias;
    u32 a;
    u32 b;
    u32 c;
    u32 d;
    u32 iconst;
};

#define Q16_16_POS(integer, frac_hex) (((u32)(integer) << 16) | (u32)(frac_hex))
#define Q16_16_NEG(integer)          ((u32)(0u - ((u32)(integer) << 16)))

static const struct neuron_profile neuron_profiles[] = {
    /* Standard Izhikevich profile names. I input is kept at 0; iconst supplies drive. */
    {"regular",    "rs",  0x0000051Fu, 0x00003333u, Q16_16_NEG(65), Q16_16_POS(8, 0),    Q16_16_POS(10, 0)},
    {"bursting",   "ib",  0x0000051Fu, 0x00003333u, Q16_16_NEG(55), Q16_16_POS(4, 0),    Q16_16_POS(10, 0)},
    {"chattering", "ch",  0x0000051Fu, 0x00003333u, Q16_16_NEG(50), Q16_16_POS(2, 0),    Q16_16_POS(10, 0)},
    {"fast",       "fs",  0x0000199Au, 0x00003333u, Q16_16_NEG(65), Q16_16_POS(2, 0),    Q16_16_POS(10, 0)},
    {"lts",        "lts", 0x0000051Fu, 0x00004000u, Q16_16_NEG(65), Q16_16_POS(2, 0),    Q16_16_POS(10, 0)},
    {"tc",         "tc",  0x0000051Fu, 0x00004000u, Q16_16_NEG(65), Q16_16_POS(0, 0x0CCDu), Q16_16_POS(10, 0)},
    {"resonator",  "rz",  0x0000199Au, 0x0000428Fu, Q16_16_NEG(65), Q16_16_POS(2, 0),    Q16_16_POS(10, 0)},
    {"rebound",    "rb",  0x000007AEu, 0x00004000u, Q16_16_NEG(60), Q16_16_POS(4, 0),    Q16_16_POS(10, 0)}
};

#define NEURON_PROFILE_COUNT (sizeof(neuron_profiles) / sizeof(neuron_profiles[0]))
#define NEURON_DEFAULT_DT     0x00001000u
#define NEURON_DEFAULT_I      0x00000000u
/* update_period counts neuron-clock cycles; the neurons moved from the
 * 200 MHz fabric clock to a dedicated 50 MHz clock, so 256 preserves the
 * original ~5.12 us default step rate. */
#define NEURON_DEFAULT_PERIOD 256u

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
    return RW_REG0 + (idx & 7u) * 4u;
}

static u32 ro_addr(unsigned int idx)
{
    return RO_REG0 + (idx & 7u) * 4u;
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
    send_byte('0' + (u8)(idx & 7u));
    send_str(" = ");
    send_hex(val);
    send_str("\r\n");
}

static u32 read_selected_count(u32 selector)
{
    u32 old_rw1 = Xil_In32(RW_REG1);
    u32 value;

    Xil_Out32(RW_REG1, selector);
    value = Xil_In32(RO_REG3);
    Xil_Out32(RW_REG1, old_rw1);
    return value;
}

static u32 read_adc_debug(u32 chip, u32 selector)
{
    u32 old_rw7 = Xil_In32(RW_REG7);
    u32 value;

    Xil_Out32(RW_REG7, (old_rw7 & ~0x1Fu) | (selector & 0x1Fu));
    value = Xil_In32(chip == 0u ? RO_REG5 : RO_REG6);
    Xil_Out32(RW_REG7, old_rw7);
    return value;
}

static u32 read_adc_channel_half(u32 channel, u32 high_half)
{
    u32 old_rw7 = Xil_In32(RW_REG7);
    u32 value;

    Xil_Out32(RW_REG7, (old_rw7 & ~0x700u) |
                         ((channel & 3u) << 8) |
                         ((high_half & 1u) << 10));
    value = Xil_In32(RO_REG7);
    Xil_Out32(RW_REG7, old_rw7);
    return value;
}

static void print_named_hex(const char *name, u32 value)
{
    send_str(name);
    send_str("=");
    send_hex(value);
}

static void print_adc_rx_decode(const char *name, u32 status, u32 lane)
{
    send_str(name);
    send_str("_rx: ready=");
    send_uint((status >> 23) & 1u);
    send_str(" sync_n=");
    send_uint((status >> 22) & 1u);
    send_str(" ilas_check=");
    send_uint((status >> 20) & 1u);
    send_str(" bytealign=");
    send_uint((status >> 18) & 1u);
    send_str(" cdr=");
    send_uint((status >> 17) & 1u);
    send_str(" pma=");
    send_uint((status >> 16) & 1u);
    send_str(" link_ready=");
    send_hex((status >> 12) & 0xFu);
    send_str(" link_sync=");
    send_hex((status >> 8) & 0xFu);
    send_str(" k_seen=");
    send_hex((status >> 4) & 0xFu);
    send_str(" data_seen=");
    send_hex(status & 0xFu);
    send_str("\r\n");

    send_str(name);
    send_str("_lanes: align=");
    send_hex((lane >> 24) & 0xFu);
    send_str(" err_seen=");
    send_hex((lane >> 20) & 0xFu);
    send_str(" notintable=");
    send_hex((lane >> 16) & 0xFu);
    send_str(" disperr=");
    send_hex((lane >> 12) & 0xFu);
    send_str(" bytealigned=");
    send_hex((lane >> 8) & 0xFu);
    send_str(" cdrlock=");
    send_hex((lane >> 4) & 0xFu);
    send_str(" pma_done=");
    send_hex(lane & 0xFu);
    send_str("\r\n");
}

static void cmd_loop(void)
{
    u32 old_rw1 = Xil_In32(RW_REG1);
    u32 old_rw2 = Xil_In32(RW_REG2);
    u32 gth = read_selected_count(4u);
    u32 gth_lanes = read_selected_count(5u);
    u32 dac_tx = read_selected_count(6u);
    u32 adc_init = read_selected_count(16u);
    u32 adc1_analog = read_selected_count(17u);
    u32 adc1_digital = read_selected_count(18u);
    u32 adc0_rx = read_adc_debug(0u, 0u);
    u32 adc0_lanes = read_adc_debug(0u, 1u);
    u32 adc0_events = read_adc_debug(0u, 2u);
    u32 adc1_rx = read_adc_debug(1u, 0u);
    u32 adc1_lanes = read_adc_debug(1u, 1u);
    u32 adc1_events = read_adc_debug(1u, 2u);
    u32 ch0_low = read_adc_channel_half(0u, 0u);
    u32 ch0_high = read_adc_channel_half(0u, 1u);
    u32 ch1_low = read_adc_channel_half(1u, 0u);
    u32 ch2_low = read_adc_channel_half(2u, 0u);
    u32 ch3_low = read_adc_channel_half(3u, 0u);
    u32 raw0 = read_adc_debug(0u, 7u);
    u32 raw1 = read_adc_debug(1u, 7u);

    send_str("LOOP ");
    print_named_hex("build", read_selected_count(3u));
    send_str(" ");
    print_named_hex("rw2", old_rw2);
    send_str("\r\n");

    print_named_hex("gth", gth);
    send_str(" ");
    print_named_hex("gth_lanes", gth_lanes);
    send_str(" ");
    print_named_hex("dac_tx", dac_tx);
    send_str("\r\n");

    print_named_hex("adc_init", adc_init);
    send_str(" ");
    print_named_hex("adc1_analog", adc1_analog);
    send_str(" ");
    print_named_hex("adc1_digital", adc1_digital);
    send_str("\r\n");

    print_named_hex("adc0_rx", adc0_rx);
    send_str(" ");
    print_named_hex("adc0_lanes", adc0_lanes);
    send_str(" ");
    print_named_hex("events", adc0_events);
    send_str("\r\n");
    print_adc_rx_decode("adc0", adc0_rx, adc0_lanes);

    print_named_hex("adc1_rx", adc1_rx);
    send_str(" ");
    print_named_hex("adc1_lanes", adc1_lanes);
    send_str(" ");
    print_named_hex("events", adc1_events);
    send_str("\r\n");
    print_adc_rx_decode("adc1", adc1_rx, adc1_lanes);

    print_named_hex("adc_ch0_lo", ch0_low);
    send_str(" ");
    print_named_hex("adc_ch0_hi", ch0_high);
    send_str(" ");
    print_named_hex("adc_ch1_lo", ch1_low);
    send_str(" ");
    print_named_hex("adc_ch2_lo", ch2_low);
    send_str(" ");
    print_named_hex("adc_ch3_lo", ch3_low);
    send_str("\r\n");

    print_named_hex("adc0_raw", raw0);
    send_str(" ");
    print_named_hex("adc1_raw", raw1);
    send_str("\r\n");

    Xil_Out32(RW_REG2, old_rw2);
    Xil_Out32(RW_REG1, old_rw1);
}

static void short_delay(void)
{
    volatile u32 i;
    for (i = 0; i < 1000000u; i++)
        ;
}

static void delay_short_delays(u32 count)
{
    while (count-- > 0u)
        short_delay();
}

static int wait_ro0_mask(u32 mask, u32 timeout_delays)
{
    while (timeout_delays-- > 0u) {
        if ((Xil_In32(RO_REG0) & mask) == mask)
            return 1;
        short_delay();
    }
    return 0;
}

static int wait_dac_init_done(u32 timeout_delays)
{
    while (timeout_delays-- > 0u) {
        u32 ro0 = Xil_In32(RO_REG0);
        if ((ro0 & RO0_DAC_DONE) != 0u && (ro0 & RO0_DAC_BUSY) == 0u)
            return 1;
        short_delay();
    }
    return 0;
}

static void restart_dac_tx_path(void)
{
    Xil_Out32(RW_REG3, 0);
    Xil_Out32(RW_REG5, RW5_LAUNCH_DEFAULT);
    Xil_Out32(RW_REG2, RW2_LAUNCH_DEFAULT | RW2_GTH_RESET);
    delay_short_delays(50u);
    Xil_Out32(RW_REG2, RW2_LAUNCH_DEFAULT);

    /*
     * Loading firmware can disturb an already-running bitstream JESD stream.
     * Bring TX back up in the order that recovered the DAC on hardware:
     * release GT, let the TX/LiteJESD stream become stable, then reinitialize
     * the DAC so it sees a clean CGS/ILAS/data sequence.
     */
    wait_ro0_mask(RO0_TX_LINK_READY, 500u);
    delay_short_delays(50u);

    Xil_Out32(RW_REG3, 2u);
    short_delay();
    Xil_Out32(RW_REG3, 0);
    wait_dac_init_done(500u);
}

static char ascii_upper(char c)
{
    if (c >= 'a' && c <= 'z')
        return (char)(c - ('a' - 'A'));
    return c;
}

static int token_eq_ci(const char *token, const char *word)
{
    while (*word != '\0') {
        if (ascii_upper(*token) != ascii_upper(*word))
            return 0;
        token++;
        word++;
    }

    return *token == '\0' || *token == ' ' || *token == '\t';
}

static void advance_token(char **cursor)
{
    char *p = *cursor;

    while (*p != '\0' && *p != ' ' && *p != '\t')
        p++;
    while (*p == ' ' || *p == '\t')
        p++;
    *cursor = p;
}

static void print_adc_coupling(void)
{
    u32 rw0 = Xil_In32(RW_REG0);

    send_str("adc_coupling: ch1=");
    send_str((rw0 & CTRL_ADC_CH1_ENDCC) ? "dc" : "ac");
    send_str(" ch2=");
    send_str((rw0 & CTRL_ADC_CH2_ENDCC) ? "dc" : "ac");
    send_str(" ch3=");
    send_str((rw0 & CTRL_ADC_CH3_ENDCC) ? "dc" : "ac");
    send_str(" ch4=");
    send_str((rw0 & CTRL_ADC_CH4_ENDCC) ? "dc" : "ac");
    send_str("\r\n");
}

static int parse_adc_coupling_target(char **cursor, u32 *mask)
{
    char *p = *cursor;
    u32 channel;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0')
        return 0;

    if (token_eq_ci(p, "all")) {
        *mask = CTRL_ADC_ENDCC_MASK;
        advance_token(&p);
        *cursor = p;
        return 1;
    }

    if (!parse_u32_arg(&p, &channel) || channel < 1u || channel > 4u)
        return 0;

    *mask = 1u << (channel + 5u);
    *cursor = p;
    return 1;
}

static int parse_adc_coupling_mode(char **cursor, u32 *dc_enable)
{
    char *p = *cursor;
    u32 value;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0')
        return 0;

    if (token_eq_ci(p, "ac")) {
        *dc_enable = 0u;
        advance_token(&p);
    } else if (token_eq_ci(p, "dc")) {
        *dc_enable = 1u;
        advance_token(&p);
    } else if (parse_u32_arg(&p, &value) && value <= 1u) {
        *dc_enable = value;
    } else {
        return 0;
    }

    *cursor = p;
    return 1;
}

static void cmd_coup(void)
{
    char *p = &cmd[4];
    u32 mask;
    u32 dc_enable;
    u32 rw0;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0') {
        print_adc_coupling();
        return;
    }

    if (!parse_adc_coupling_target(&p, &mask)) {
        send_str("ERR COUP expects [all|1..4] [ac|dc|0|1]\r\n");
        return;
    }

    if (!parse_adc_coupling_mode(&p, &dc_enable)) {
        send_str("ERR COUP expects [all|1..4] [ac|dc|0|1]\r\n");
        return;
    }

    rw0 = Xil_In32(RW_REG0);
    if (dc_enable) {
        rw0 |= mask;
    } else {
        rw0 &= ~mask;
    }
    Xil_Out32(RW_REG0, rw0);

    send_str("OK COUP ");
    send_str(dc_enable ? "dc" : "ac");
    send_str(" RW0=");
    send_hex(rw0);
    send_str("\r\n");
    if (dc_enable) {
        send_str("WARNING: ADC DC coupling enabled; use only with known-safe source amplitude.\r\n");
    }
    print_adc_coupling();
}

static int parse_adc_test_mode(char **cursor, u32 *mode)
{
    char *p = *cursor;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0')
        return 0;

    if (token_eq_ci(p, "off") || token_eq_ci(p, "normal")) {
        *mode = 0u;
    } else if (token_eq_ci(p, "d21")) {
        *mode = 1u;
    } else if (token_eq_ci(p, "k28")) {
        *mode = 2u;
    } else if (token_eq_ci(p, "ila")) {
        *mode = 3u;
    } else if (token_eq_ci(p, "rpat")) {
        *mode = 4u;
    } else if (token_eq_ci(p, "transport")) {
        *mode = 5u;
    } else if (!parse_u32_arg(&p, mode)) {
        return 0;
    }

    return *mode <= 5u;
}

static int parse_dac_source_mode(char **cursor, u32 *mode)
{
    char *p = *cursor;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0')
        return 0;

    if (token_eq_ci(p, "auto")) {
        *mode = 0u;
    } else if (token_eq_ci(p, "dds") || token_eq_ci(p, "sine")) {
        *mode = 1u;
    } else if (token_eq_ci(p, "bram") || token_eq_ci(p, "program")) {
        *mode = 2u;
    } else if (token_eq_ci(p, "izh") || token_eq_ci(p, "neuron")) {
        *mode = 3u;
    } else if (token_eq_ci(p, "vout") || token_eq_ci(p, "voltage") ||
               token_eq_ci(p, "direct")) {
        *mode = 4u;
    } else if (!parse_u32_arg(&p, mode)) {
        return 0;
    }

    *cursor = p;
    return 1;
}

static void cmd_nsrc(void)
{
    char *p = &cmd[4];
    char *save_p;
    u32 channel = 0;
    u32 all_channels = 1;
    u32 first;
    u32 mode;

    while (*p == ' ' || *p == '\t')
        p++;

    if (token_eq_ci(p, "all")) {
        while (*p != '\0' && *p != ' ' && *p != '\t')
            p++;
    } else {
        save_p = p;
        if (parse_u32_arg(&p, &first) && first < 4u) {
            channel = first;
            all_channels = 0;
        } else {
            p = save_p;
        }
    }

    if (!parse_dac_source_mode(&p, &mode) || mode > 4u) {
        send_str("ERR NSRC expects [all|0..3] auto, dds, bram, izh, vout, or 0..4\r\n");
        return;
    }

    if (mode == 4u) {
        pulse_neuron_config(channel, all_channels, 10u, 1u);
        pulse_neuron_config(channel, all_channels, 8u, 3u);
    } else {
        if (mode == 3u) {
            pulse_neuron_config(channel, all_channels, 10u, 0u);
        }
        pulse_neuron_config(channel, all_channels, 8u, mode);
    }

    {
        u32 rw3 = Xil_In32(RW_REG3);
        u32 rw3_source_mode = (mode == 4u) ? 3u : mode;
        rw3 &= ~(RW3_DAC_SOURCE_MASK
#if HAS_BRAM_DATAPLANE
                 | (all_channels ? RW3_DAC_PROGRAM_EN : 0u)
#endif
                 | RW3_IZH_CFG_STROBE);
        rw3 |= (rw3_source_mode << RW3_DAC_SOURCE_SHIFT);
#if HAS_BRAM_DATAPLANE
        if (mode == 2u) {
            rw3 |= RW3_DAC_PROGRAM_EN;
        }
#endif
        Xil_Out32(RW_REG3, rw3);
    }

    send_str("DAC source ");
    send_str(all_channels ? "all" : "ch");
    if (!all_channels) {
        send_uint(channel);
    }
    send_str(" = ");
    send_uint(mode);
    send_str("\r\n");
}

static int parse_neuron_param(char **cursor, u32 *param, int *needs_value)
{
    char *p = *cursor;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0')
        return 0;

    *needs_value = 1;
    if (token_eq_ci(p, "a")) {
        *param = 0u;
    } else if (token_eq_ci(p, "b")) {
        *param = 1u;
    } else if (token_eq_ci(p, "c")) {
        *param = 2u;
    } else if (token_eq_ci(p, "d")) {
        *param = 3u;
    } else if (token_eq_ci(p, "i") || token_eq_ci(p, "current")) {
        *param = 4u;
    } else if (token_eq_ci(p, "dt") || token_eq_ci(p, "timestep")) {
        *param = 5u;
    } else if (token_eq_ci(p, "iconst") || token_eq_ci(p, "bias")) {
        *param = 6u;
    } else if (token_eq_ci(p, "offset") || token_eq_ci(p, "offs")) {
        *param = 7u;
    } else if (token_eq_ci(p, "source") || token_eq_ci(p, "src")) {
        *param = 8u;
    } else if (token_eq_ci(p, "period") || token_eq_ci(p, "rate") ||
               token_eq_ci(p, "divider") || token_eq_ci(p, "update")) {
        *param = 9u;
    } else if (token_eq_ci(p, "output") || token_eq_ci(p, "out") ||
               token_eq_ci(p, "vout_mode")) {
        *param = 10u;
    } else if (token_eq_ci(p, "default") || token_eq_ci(p, "defaults")) {
        *param = 14u;
        *needs_value = 0;
    } else if (token_eq_ci(p, "reset")) {
        *param = 15u;
        *needs_value = 0;
    } else if (!parse_u32_arg(&p, param)) {
        return 0;
    }

    while (*p != '\0' && *p != ' ' && *p != '\t')
        p++;
    *cursor = p;
    return *param <= 15u;
}

static void pulse_neuron_config(u32 channel, u32 all_channels, u32 param, u32 value)
{
    u32 old_rw1 = Xil_In32(RW_REG1);
    u32 old_rw3 = Xil_In32(RW_REG3);
    u32 base_rw3 = old_rw3 & RW3_DAC_SOURCE_MASK;
    u32 restore_rw3 = old_rw3 & ~RW3_IZH_CFG_STROBE;
    u32 cfg_rw3 = base_rw3 |
                  RW3_IZH_CFG_STROBE |
                  ((param & 0xFu) << RW3_IZH_CFG_PARAM_SHIFT) |
                  ((channel & 3u) << RW3_IZH_CFG_CHANNEL_SHIFT);

    if (all_channels) {
        cfg_rw3 |= RW3_IZH_CFG_ALL;
    }

    Xil_Out32(RW_REG1, value);
    short_delay();
    Xil_Out32(RW_REG3, cfg_rw3);
    short_delay();
    Xil_Out32(RW_REG3, restore_rw3);
    short_delay();
    Xil_Out32(RW_REG1, old_rw1);
}

static const struct neuron_profile *find_neuron_profile(const char *token)
{
    unsigned int i;

    for (i = 0; i < NEURON_PROFILE_COUNT; i++) {
        if (token_eq_ci(token, neuron_profiles[i].name) ||
            token_eq_ci(token, neuron_profiles[i].alias)) {
            return &neuron_profiles[i];
        }
    }

    return NULL;
}

static void print_neuron_profiles(void)
{
    unsigned int i;

    send_str("Neuron profiles:");
    for (i = 0; i < NEURON_PROFILE_COUNT; i++) {
        send_str(" ");
        send_str(neuron_profiles[i].name);
        send_str("/");
        send_str(neuron_profiles[i].alias);
    }
    send_str("\r\n");
}

static void apply_neuron_profile(u32 channel, u32 all_channels,
                                 const struct neuron_profile *profile)
{
    pulse_neuron_config(channel, all_channels, 0u, profile->a);
    pulse_neuron_config(channel, all_channels, 1u, profile->b);
    pulse_neuron_config(channel, all_channels, 2u, profile->c);
    pulse_neuron_config(channel, all_channels, 3u, profile->d);
    pulse_neuron_config(channel, all_channels, 4u, NEURON_DEFAULT_I);
    pulse_neuron_config(channel, all_channels, 5u, NEURON_DEFAULT_DT);
    pulse_neuron_config(channel, all_channels, 6u, profile->iconst);
    pulse_neuron_config(channel, all_channels, 9u, NEURON_DEFAULT_PERIOD);
    pulse_neuron_config(channel, all_channels, 15u, 0u);
}

static void cmd_neur(void)
{
    char *p = &cmd[4];
    u32 channel = 0;
    u32 all_channels = 0;
    u32 param;
    u32 value = 0;
    int needs_value;
    const struct neuron_profile *profile;

    while (*p == ' ' || *p == '\t')
        p++;

    if (token_eq_ci(p, "profiles") || token_eq_ci(p, "list")) {
        print_neuron_profiles();
        return;
    }

    if (token_eq_ci(p, "all")) {
        all_channels = 1u;
        while (*p != '\0' && *p != ' ' && *p != '\t')
            p++;
    } else if (!parse_u32_arg(&p, &channel) || channel > 3u) {
        send_str("ERR NEUR expects channel 0..3 or all\r\n");
        return;
    }

    while (*p == ' ' || *p == '\t')
        p++;

    if (token_eq_ci(p, "profile") || token_eq_ci(p, "type")) {
        advance_token(&p);

        while (*p == ' ' || *p == '\t')
            p++;

        profile = find_neuron_profile(p);
        if (profile == NULL) {
            send_str("ERR NEUR profile expects one of: regular/rs, bursting/ib, chattering/ch, fast/fs, lts, tc, resonator/rz, rebound/rb\r\n");
            return;
        }

        apply_neuron_profile(channel, all_channels, profile);
        send_str("OK NEUR ");
        send_str(all_channels ? "all" : "ch");
        if (!all_channels) {
            send_uint(channel);
        }
        send_str(" profile=");
        send_str(profile->name);
        send_str(" iconst=");
        send_hex(profile->iconst);
        send_str(" period=");
        send_uint(NEURON_DEFAULT_PERIOD);
        send_str("\r\n");
        return;
    }

    profile = find_neuron_profile(p);
    if (profile != NULL) {
        apply_neuron_profile(channel, all_channels, profile);
        send_str("OK NEUR ");
        send_str(all_channels ? "all" : "ch");
        if (!all_channels) {
            send_uint(channel);
        }
        send_str(" profile=");
        send_str(profile->name);
        send_str(" iconst=");
        send_hex(profile->iconst);
        send_str(" period=");
        send_uint(NEURON_DEFAULT_PERIOD);
        send_str("\r\n");
        return;
    }

    if (token_eq_ci(p, "profiles") || token_eq_ci(p, "list")) {
        print_neuron_profiles();
        return;
    }

    if (!parse_neuron_param(&p, &param, &needs_value)) {
        send_str("ERR NEUR expects profile or param a,b,c,d,i,dt,iconst,offset,period,source,output,reset,default\r\n");
        return;
    }

    if (needs_value && !parse_u32_arg(&p, &value)) {
        send_str("ERR NEUR param expects raw Q16.16 value\r\n");
        return;
    }

    pulse_neuron_config(channel, all_channels, param, value);
    send_str("OK NEUR ");
    send_str(all_channels ? "all" : "ch");
    if (!all_channels) {
        send_uint(channel);
    }
    send_str(" param=");
    send_uint(param);
    send_str(" value=");
    send_hex(value);
    send_str("\r\n");
}

static void cmd_adct(void)
{
    char *p = &cmd[4];
    u32 mode;
    u32 chip_mask = 3u;
    u32 rw0;
    u32 rw5;

    while (*p == ' ' || *p == '\t')
        p++;

    if (token_eq_ci(p, "ALL")) {
        chip_mask = 3u;
        advance_token(&p);
    } else if (token_eq_ci(p, "ADC0") || token_eq_ci(p, "CHIP0")) {
        chip_mask = 1u;
        advance_token(&p);
    } else if (token_eq_ci(p, "ADC1") || token_eq_ci(p, "CHIP1")) {
        chip_mask = 2u;
        advance_token(&p);
    }

    if (!parse_adc_test_mode(&p, &mode)) {
        send_str("ERR ADCT expects [all|adc0|adc1] off|d21|k28|ila|rpat|transport or 0..5\r\n");
        return;
    }

    rw5 = Xil_In32(RW_REG5);
    rw5 &= ~RW5_ADC_TEST_CHIP_MASK;
    rw5 |= RW5_ADC_CTRL_ENABLE |
           ((chip_mask & 3u) << RW5_ADC_TEST_CHIP_MASK_SHIFT);
    Xil_Out32(RW_REG5, rw5);

    rw0 = Xil_In32(RW_REG0);
    rw0 &= ~(CTRL_ADC_TEST_MODE_MASK | CTRL_ADC_TEST_REQ);
    rw0 |= (mode & 7u) << CTRL_ADC_TEST_MODE_SHIFT;

    Xil_Out32(RW_REG0, rw0);
    short_delay();
    Xil_Out32(RW_REG0, rw0 | CTRL_ADC_TEST_REQ);
    short_delay();
    Xil_Out32(RW_REG0, rw0);

    send_str("OK ADCT mode=");
    send_uint(mode);
    send_str(" chip_mask=");
    send_hex(chip_mask);
    send_str(" RW0=");
    send_hex(rw0);
    send_str(" RW5=");
    send_hex(rw5);
    send_str("\r\n");
}

static void cmd_rxsw(void)
{
    static const char *names[4] = {
        "ilas_on,sundance_order",
        "ilas_bypass,sundance_order",
        "ilas_on,physical_order",
        "ilas_bypass,physical_order"
    };
    char *p = &cmd[4];
    u32 chip = 0;
    u32 old_rw2 = Xil_In32(RW_REG2);
    u32 old_rw5 = Xil_In32(RW_REG5);
    u32 order_bit;
    u32 lane_shift;
    unsigned int mode;
    unsigned int combo;

    parse_u32_arg(&p, &chip);
    chip &= 1u;
    order_bit = chip == 0u ? RW5_ADC0_DP_ORDER : RW5_ADC1_DP_ORDER;
    lane_shift = chip == 0u ? 4u : 0u;

    send_str("RXSW chip=");
    send_uint(chip);
    send_str(" uses RW5: bit8=ILAS_bypass chip_order_bit=");
    send_uint(chip == 0u ? 4u : 5u);
    send_str(" polarity_bits=");
    send_uint(chip == 0u ? 4u : 0u);
    send_str("..");
    send_uint(chip == 0u ? 7u : 3u);
    send_str("\r\n");

    for (mode = 0; mode < 4u; mode++) {
        for (combo = 0; combo < 16u; combo++) {
            u32 rxmask = (u32)combo << lane_shift;
            u32 rw5 = RW5_ADC_CTRL_ENABLE | (rxmask << RW5_ADC_RX_POL_SHIFT);
            u32 status;
            u32 lane;
            u32 events;

            if ((mode & 1u) != 0u) {
                rw5 |= RW5_ADC_ILAS_BYPASS;
            }
            if ((mode & 2u) != 0u) {
                rw5 |= order_bit;
            }

            Xil_Out32(RW_REG5, rw5);
            Xil_Out32(RW_REG2, old_rw2 | RW2_GTH_RESET);
            short_delay();
            Xil_Out32(RW_REG2, old_rw2 & ~RW2_GTH_RESET);
            short_delay();

            status = read_adc_debug(chip, 0u);
            lane = read_adc_debug(chip, 1u);
            events = read_adc_debug(chip, 2u);

            send_str(names[mode]);
            send_str(" rxmask=");
            send_hex(rxmask);
            send_str(" rw5=");
            send_hex(rw5);
            send_str(" ready=");
            send_uint((status >> 23) & 1u);
            send_str(" sync=");
            send_uint((status >> 22) & 1u);
            send_str(" link=");
            send_hex((status >> 12) & 0xFu);
            send_str(" err=");
            send_hex((lane >> 20) & 0xFu);
            send_str(" nit=");
            send_hex((lane >> 16) & 0xFu);
            send_str(" disp=");
            send_hex((lane >> 12) & 0xFu);
            send_str(" status=");
            send_hex(status);
            send_str(" lane=");
            send_hex(lane);
            send_str(" events=");
            send_hex(events);
            send_str("\r\n");
        }
    }

    Xil_Out32(RW_REG2, old_rw2);
    Xil_Out32(RW_REG5, old_rw5);
}

static void cmd_adcs(void)
{
    char *p = &cmd[4];
    u32 first;
    u32 second;
    u32 third;

    while (*p == ' ' || *p == '\t')
        p++;

    if (token_eq_ci(p, "CH")) {
        u32 ch;
        u32 high_half = 0u;
        advance_token(&p);
        if (!parse_u32_arg(&p, &ch)) {
            send_str("ERR ADCS CH expects channel 0..3 and optional hi|lo\r\n");
            return;
        }
        while (*p == ' ' || *p == '\t')
            p++;
        if (token_eq_ci(p, "HI") || token_eq_ci(p, "HIGH")) {
            high_half = 1u;
        } else if (parse_u32_arg(&p, &third)) {
            high_half = third & 1u;
        }
        send_str("ADCS ch=");
        send_uint(ch & 3u);
        send_str(high_half ? " high " : " low ");
        print_named_hex("value", read_adc_channel_half(ch, high_half));
        send_str("\r\n");
        return;
    }

    if (!parse_u32_arg(&p, &first)) {
        send_str("ADC frontend ");
        print_named_hex("RO4", Xil_In32(RO_REG4));
        send_str("\r\n");
        send_str("ADC0 ");
        print_named_hex("status", read_adc_debug(0u, 0u));
        send_str(" ");
        print_named_hex("lane", read_adc_debug(0u, 1u));
        send_str(" ");
        print_named_hex("events", read_adc_debug(0u, 2u));
        send_str("\r\n");
        send_str("ADC1 ");
        print_named_hex("status", read_adc_debug(1u, 0u));
        send_str(" ");
        print_named_hex("lane", read_adc_debug(1u, 1u));
        send_str(" ");
        print_named_hex("events", read_adc_debug(1u, 2u));
        send_str("\r\n");
        return;
    }

    if (first <= 1u) {
        if (!parse_u32_arg(&p, &second)) {
            second = 0u;
        }
        send_str("ADCS chip=");
        send_uint(first);
        send_str(" sel=");
        send_uint(second & 31u);
        send_str(" ");
        print_named_hex("value", read_adc_debug(first, second));
        send_str("\r\n");
        return;
    }

    send_str("ERR ADCS expects no args, chip selector, or ADCS CH channel [hi|lo]\r\n");
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
    send_str(" done=");
    send_uint((s & CAPTURE_STATUS_DONE) ? 1u : 0u);
    send_str(" busy=");
    send_uint((s & CAPTURE_STATUS_BUSY) ? 1u : 0u);
    send_str(" count=");
    send_uint(s & 0xFFFFu);
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

static void trigger_capture(int use_dac_program)
{
    u32 rw3 = Xil_In32(RW_REG3);

    rw3 &= ~(RW3_CAPTURE_START | RW3_DAC_PROGRAM_EN);
    if (use_dac_program) {
        rw3 |= RW3_DAC_PROGRAM_EN;
    }
    Xil_Out32(RW_REG3, rw3);
    short_delay();
    Xil_Out32(RW_REG3, rw3 | RW3_CAPTURE_START);
    short_delay();
    Xil_Out32(RW_REG3, rw3);
}

static void cmd_program(u32 channel, u32 words)
{
    u32 i;

    if (channel >= DAC_PROGRAM_CHANNELS) {
        send_str("ERR PROG channel must be 0..3\r\n");
        return;
    }

    if (words == 0u || words > DAC_PROGRAM_WORDS_PER_CHANNEL) {
        words = DAC_PROGRAM_WORDS_PER_CHANNEL;
    }
    if ((words & 1u) != 0u) {
        send_str("ERR PROG word count must be even for 64-bit DAC frames\r\n");
        return;
    }

    send_str("PGRD ch=");
    send_uint(channel);
    send_str(" words=");
    send_uint(words);
    send_str("\r\n");

    for (i = 0; i < words; i++) {
        Xil_Out32(dac_program_bram_base[channel] + i * 4u, recv_le32_blocking());
    }

    send_str("OK PROG ch=");
    send_uint(channel);
    send_str(" words=");
    send_uint(words);
    send_str("\r\n");
}

static void cmd_dpwr(u32 channel, u32 start_word, u32 words)
{
    u32 i;

    if (channel >= DAC_PROGRAM_CHANNELS) {
        send_str("ERR DPWR channel must be 0..3\r\n");
        return;
    }

    if (start_word >= DAC_PROGRAM_WORDS_PER_CHANNEL) {
        send_str("ERR DPWR start out of range\r\n");
        return;
    }

    if (words == 0u || start_word + words > DAC_PROGRAM_WORDS_PER_CHANNEL) {
        send_str("ERR DPWR word count out of range\r\n");
        return;
    }

    send_str("DPWR ch=");
    send_uint(channel);
    send_str(" start=");
    send_uint(start_word);
    send_str(" words=");
    send_uint(words);
    send_str("\r\n");

    for (i = 0; i < words; i++) {
        Xil_Out32(dac_program_bram_base[channel] + (start_word + i) * 4u,
                  recv_le32_blocking());
    }

    send_str("OK DPWR ch=");
    send_uint(channel);
    send_str(" start=");
    send_uint(start_word);
    send_str(" words=");
    send_uint(words);
    send_str("\r\n");
}

static void cmd_dprd(u32 channel, u32 start_word, u32 words)
{
    u32 i;

    if (channel >= DAC_PROGRAM_CHANNELS) {
        send_str("ERR DPRD channel must be 0..3\r\n");
        return;
    }
    if (start_word >= DAC_PROGRAM_WORDS_PER_CHANNEL) {
        send_str("ERR DPRD start out of range\r\n");
        return;
    }
    if (words == 0u) {
        words = 16u;
    }
    if (start_word + words > DAC_PROGRAM_WORDS_PER_CHANNEL) {
        words = DAC_PROGRAM_WORDS_PER_CHANNEL - start_word;
    }

    send_str("DPRD ch=");
    send_uint(channel);
    send_str(" start=");
    send_uint(start_word);
    send_str(" words=");
    send_uint(words);
    send_str("\r\n");

    for (i = 0; i < words; i++) {
        u32 index = start_word + i;
        send_uint(index);
        send_str(": ");
        send_hex(Xil_In32(dac_program_bram_base[channel] + index * 4u));
        send_str("\r\n");
    }
}

static void cmd_capture(u32 frames, int use_dac_program)
{
    u32 status = 0;
    u32 frame;
    u32 word;

    if (frames == 0u || frames > ADC_CAPTURE_FRAMES) {
        frames = ADC_CAPTURE_FRAMES;
    }
    trigger_capture(use_dac_program);
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

    for (frame = 0; frame < frames; frame++) {
        for (word = 0; word < ADC_CAPTURE_WORDS_PER_CHIP_FRAME; word++) {
            u32 sample = Xil_In32(ADC0_CAPTURE_BRAM_BASE +
                                  (frame * ADC_CAPTURE_WORDS_PER_CHIP_FRAME + word) * 4u);
            send_byte((u8)(sample & 0xFFu));
            send_byte((u8)((sample >> 8) & 0xFFu));
            send_byte((u8)((sample >> 16) & 0xFFu));
            send_byte((u8)((sample >> 24) & 0xFFu));
        }
        for (word = 0; word < ADC_CAPTURE_WORDS_PER_CHIP_FRAME; word++) {
            u32 sample = Xil_In32(ADC1_CAPTURE_BRAM_BASE +
                                  (frame * ADC_CAPTURE_WORDS_PER_CHIP_FRAME + word) * 4u);
            send_byte((u8)(sample & 0xFFu));
            send_byte((u8)((sample >> 8) & 0xFFu));
            send_byte((u8)((sample >> 16) & 0xFFu));
            send_byte((u8)((sample >> 24) & 0xFFu));
        }
    }
}
#endif

#if HAS_PS_DDR_DMA
static u32 dma_status(u32 chip)
{
    if (chip >= ADC_DMA_CHIPS) {
        return 0xFFFFFFFFu;
    }
    return Xil_In32(adc_dma_base[chip] + DMA_S2MM_DMASR);
}

static void dma_reset(u32 chip)
{
    u32 timeout;
    u32 base;

    if (chip >= ADC_DMA_CHIPS) {
        return;
    }
    base = adc_dma_base[chip];
    Xil_Out32(base + DMA_S2MM_DMACR, DMA_DMACR_RESET);
    for (timeout = 0; timeout < 100000u; timeout++) {
        if ((Xil_In32(base + DMA_S2MM_DMACR) & DMA_DMACR_RESET) == 0u) {
            return;
        }
    }
}

static void dma_arm_s2mm(u32 chip, u32 dest_addr, u32 bytes)
{
    u32 base = adc_dma_base[chip];

    dma_reset(chip);
    Xil_Out32(base + DMA_S2MM_DMASR, DMA_DMASR_IRQ_MASK | DMA_DMASR_ERR_MASK);
    Xil_Out32(base + DMA_S2MM_DMACR, DMA_DMACR_RS);
    Xil_Out32(base + DMA_S2MM_DA, dest_addr);
    Xil_Out32(base + DMA_S2MM_DA_MSB, 0u);
    Xil_Out32(base + DMA_S2MM_LENGTH, bytes);
}

static int wait_dma_done(u32 *s0, u32 *s1)
{
    u32 timeout;

    for (timeout = 0; timeout < 10000000u; timeout++) {
        *s0 = dma_status(0u);
        *s1 = dma_status(1u);
        if (((*s0 | *s1) & DMA_DMASR_ERR_MASK) != 0u) {
            return 0;
        }
        if (((*s0 & DMA_DMASR_IDLE) != 0u) &&
            ((*s1 & DMA_DMASR_IDLE) != 0u)) {
            return 1;
        }
    }

    return 0;
}

static void print_dma_status(void)
{
    u32 i;

    for (i = 0; i < ADC_DMA_CHIPS; i++) {
        u32 s = dma_status(i);
        send_str("DMA");
        send_uint(i);
        send_str(" S2MM ");
        print_named_hex("DMASR", s);
        send_str(" halted=");
        send_uint((s & DMA_DMASR_HALTED) ? 1u : 0u);
        send_str(" idle=");
        send_uint((s & DMA_DMASR_IDLE) ? 1u : 0u);
        send_str(" err=");
        send_uint((s & DMA_DMASR_ERR_MASK) ? 1u : 0u);
        send_str(" ddr=");
        send_hex(adc_dma_ddr_base[i]);
        send_str("\r\n");
    }
}

static void cmd_dma_capture(u32 frames)
{
    u32 bytes;
    u32 s0 = 0;
    u32 s1 = 0;

    if (frames == 0u || frames > ADC_CAPTURE_FRAMES) {
        frames = ADC_CAPTURE_FRAMES;
    }
    bytes = frames * ADC_DMA_FRAME_BYTES;

    dma_arm_s2mm(0u, ADC_DMA0_DDR_BASE, bytes);
    dma_arm_s2mm(1u, ADC_DMA1_DDR_BASE, bytes);
    trigger_capture(0);

    if (!wait_dma_done(&s0, &s1)) {
        send_str("ERR DMAC timeout/error ");
        print_named_hex("dma0", s0);
        send_str(" ");
        print_named_hex("dma1", s1);
        send_str("\r\n");
        return;
    }

    send_str("OK DMAC frames=");
    send_uint(frames);
    send_str(" bytes_per_chip=");
    send_uint(bytes);
    send_str(" ");
    print_named_hex("dma0", s0);
    send_str(" ");
    print_named_hex("dma1", s1);
    send_str("\r\n");
}

static void cmd_ddr_read(u32 chip, u32 start_word, u32 words)
{
    u32 i;
    u32 base;

    if (chip >= ADC_DMA_CHIPS) {
        send_str("ERR DDRD chip must be 0 or 1\r\n");
        return;
    }
    if (words == 0u) {
        words = 16u;
    }

    base = adc_dma_ddr_base[chip];
    send_str("DDRD chip=");
    send_uint(chip);
    send_str(" base=");
    send_hex(base);
    send_str(" start=");
    send_uint(start_word);
    send_str(" words=");
    send_uint(words);
    send_str("\r\n");

    for (i = 0; i < words; i++) {
        u32 index = start_word + i;
        send_uint(index);
        send_str(": ");
        send_hex(Xil_In32(base + index * 4u));
        send_str("\r\n");
    }
}
#endif

static void cmd_help(void)
{
    send_str("DAQ_LAUNCH commands:\r\n");
    send_str("  HELP\r\n");
    send_str("  STAT             dump status, counters, controls\r\n");
    send_str("  TXRS             restart GTH/LiteJESD TX, then reinitialize DAC\r\n");
    send_str("  LOOP             dump DAC TX / dual-ADC RX loopback diagnostics\r\n");
    send_str("  RXSW [0|1]       sweep ADC chip RX ILAS/order/polarity diagnostics\r\n");
    send_str("  ADCS [chip sel]  ADC frontend status; ADCS CH n [hi|lo] reads adc_chN\r\n");
    send_str("  ADCT [all|adc0|adc1] mode  ADS54J60 test mode: off,d21,k28,ila,rpat,transport\r\n");
    send_str("  COUP [all|1..4] [ac|dc] ADC input coupling; default/safe state is AC\r\n");
    send_str("  NSRC [ch|all] mode DAC source: auto,dds,bram,izh,vout\r\n");
    send_str("  NEUR ch param value  program IZH Q16.16 param on ch=0..3 or all\r\n");
    send_str("                 params: a,b,c,d,i/current,dt,iconst/bias,offset,period,source,output,reset,default\r\n");
    send_str("  NEUR [ch|all] profile name  profiles: regular/rs, bursting/ib, chattering/ch, fast/fs, lts, tc, resonator/rz, rebound/rb\r\n");
    send_str("  NEUR profiles       list built-in neuron profiles\r\n");
    send_str("  RDRO n           read RO register 0..7\r\n");
    send_str("  RDRW n           read RW register 0..7\r\n");
    send_str("  WRTE n value     write RW register 0..7; use 0x prefix for hex masks\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("  PROG ch [n]      upload even n little-endian u32 words to DAC channel ch=0..3\r\n");
    send_str("  DPWR ch start n  write n little-endian u32 words to DAC BRAM 32-bit word addresses\r\n");
    send_str("  DPRD ch [start] [n] read back DAC program BRAM u32 words\r\n");
    send_str("  CAPS             print ADC BRAM capture status\r\n");
    send_str("  CAPT [frames]    capture 256-bit adc_ch0..3 frames; stream 8 u32 words/frame\r\n");
    send_str("  PCAP [frames]    restart DAC BRAM program, then capture ADC frames\r\n");
#else
    send_str("  PROG/CAPS/CAPT/PCAP unavailable; rebuild with --with-bram-dataplane\r\n");
#endif
#if HAS_PS_DDR_DMA
    send_str("  DMAC [frames]    arm ADC0/ADC1 S2MM DMA to PS DDR, then pulse ADC capture\r\n");
    send_str("  DSTA             print AXI DMA S2MM status registers\r\n");
    send_str("  DDRD chip [start] [n] read PS DDR DMA buffer as u32 words; chip=0|1\r\n");
#else
    send_str("  DMAC/DSTA/DDRD unavailable; rebuild with --with-ps-ddr-dma\r\n");
#endif
    send_str("\r\n");
    send_str("RW0 control bits:\r\n");
    send_str("  [0]/[31] FMC_C2M_PG override unused on ZCU102 HPC0\r\n");
    send_str("  [1] HMC reset, [2] DAC_RESET_N, [3] DAC_TXEN\r\n");
    send_str("  [4] ADC1 reset, [5] ADC2 reset when manual SPI is enabled\r\n");
    send_str("  [9:6] ADC CH4..CH1 ENDCC; 0=AC coupling, 1=DC coupling\r\n");
    send_str("  [16:22] manual DAC/HMC SPI pins, enabled by [30]\r\n");
    send_str("  [28:26] ADC test mode, [29] ADC test-mode SPI one-shot request\r\n");
    send_str("  [31] FMC_C2M_PG override enable\r\n");
    send_str("RW1[4:0] selects RO3: 0=HMC status, 1=HMC last write\r\n");
    send_str("                 2=raw pins, 3=build ID, 4=GTH, 5=GTH lanes\r\n");
    send_str("                 6=LiteJESD, 7=DAC wave debug, 8..D=HMC readbacks\r\n");
    send_str("                 E=DAC status, F=DAC last write\r\n");
    send_str("                 16/0x10=ADC status, 17..19/0x11..0x13=ADC1\r\n");
    send_str("                 20..22/0x14..0x16=ADC2, 23/0x17=ADC write, 24/0x18=ADC read\r\n");
    send_str("                 25..31/0x19..0x1F=ADC1 JESD RX debug/sample\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("                 selector 7 returns DAC program player status when RW3[6]=1\r\n");
    send_str("                 with RW3[6]=1 and RW2[31]=1, selector 7 returns DAC program word\r\n");
    send_str("                 word select is RW2[30:28]: 0/1=ch0 lo/hi ... 6/7=ch3 lo/hi\r\n");
#endif
    send_str("RW2 DAC TX diag: [2:1] sample_map is ILA-only; live path uses source-to-converter preimage\r\n");
    send_str("                 [4:3] tx_lane 0=identity 1=board_map 2=inverse_check 3=dac_xbar\r\n");
    send_str("                 [7:5] DAC debug select only; channel source select is NSRC\r\n");
    send_str("                 [15:8] TX polarity invert mask only; keep 0 unless debugging polarity\r\n");
    send_str("RW5 ADC frontend: [31] enable new controls, [1:0] capture format\r\n");
    send_str("                  [3:2] raw lane select, [4] ADC0 physical DP order, [5] ADC1 physical DP order\r\n");
    send_str("                  [8] bypass ILAS check, [9] STPL check, [23:16] RX polarity mask\r\n");
    send_str("                  [25:24] ADCT chip mask: 0/3=both, 1=ADC0, 2=ADC1\r\n");
    send_str("                  firmware default is RW5=0x80000100\r\n");
    send_str("RO4=ADC frontend summary, RO5=ADC0 selected debug, RO6=ADC1 selected debug, RO7=adc_chN half\r\n");
    send_str("RW7 ADC debug: [4:0] chip selector 0=status,1=lane,2=events,3..6=samples,7=raw\r\n");
    send_str("               [9:8] logical channel for RO7, [10] high half\r\n");
#if HAS_PS_DDR_DMA
    send_str("PS DDR DMA buffers: chip0 base=0x10000000, chip1 base=0x10020000, 16 bytes/frame/chip\r\n");
#endif
    send_str("RW3 restart pulses: [0] HMC, [1] DAC, [2] ADC\r\n");
    send_str("    [5:4] DAC source: 0=auto legacy, 1=DDS, 2=BRAM, 3=IZH neuron/vout\r\n");
    send_str("    NEUR uses RW3[7] pulse, [11:8] param, [13:12] channel, [14] all\r\n");
    send_str("    IZH debug via RW1=7: conv_sel 5=ch0 dt, 6=ch0 last spike interval, 7=ch0 update period\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("    [3] ADC BRAM capture/DAC program restart pulse\r\n");
    send_str("    [6] DAC program BRAM mode enable; PCAP sets this, CAPT clears it\r\n");
    send_str("    [31:8] DAC BRAM loop frame count when [6]=1; 0 loops full 4096-frame BRAM\r\n");
    send_str("           DDS channels mixed with BRAM use the hardware default sine step\r\n");
#endif
    send_str("    [31:8] DAC sine DDS step when [6]=0; 0 uses hardware default 0x19999A\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("ADC capture frame words: ch0 low/high, ch1 low/high, ch2 low/high, ch3 low/high; max 4096 frames\r\n");
#endif
    print_uart_config();
}

static void cmd_status(void)
{
    unsigned int i;
    u32 s;
    u32 rw2;

    for (i = 0; i < 8; i++)
        print_reg("RW", i, Xil_In32(rw_addr(i)));
    for (i = 0; i < 8; i++)
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
    send_str("dac_diag: sample_map=");
    send_uint((rw2 & RW2_DAC_SAMPLE_MAP_MASK) >> RW2_DAC_SAMPLE_MAP_SHIFT);
    send_str(" tx_lane=");
    send_uint((rw2 & RW2_DAC_TX_LANE_MASK) >> RW2_DAC_TX_LANE_SHIFT);
    send_str(" debug_sel=");
    send_uint((rw2 & RW2_DAC_CONV_MASK) >> RW2_DAC_CONV_SHIFT);
    send_str(" txpol=");
    send_hex((rw2 & RW2_DAC_TX_POL_MASK) >> RW2_DAC_TX_POL_SHIFT);
    send_str(" last_src=");
    send_uint((Xil_In32(RW_REG3) & RW3_DAC_SOURCE_MASK) >> RW3_DAC_SOURCE_SHIFT);
    send_str("\r\n");
    send_str("adc_diag: rw5=");
    send_hex(Xil_In32(RW_REG5));
    send_str(" frontend=");
    send_hex(Xil_In32(RO_REG4));
    send_str(" capture_format=");
    send_uint((Xil_In32(RW_REG5) & RW5_ADC_CAPTURE_FORMAT_MASK) >> RW5_ADC_CAPTURE_FORMAT_SHIFT);
    send_str("\r\n");
#if HAS_PS_DDR_DMA
    print_dma_status();
#endif
    print_adc_coupling();
    print_uart_config();
}

static void launch_defaults(void)
{
    u32 ctrl = CTRL_DAC_CS_N | CTRL_HMC_CS_N;

    Xil_Out32(RW_REG0, ctrl);
    restart_dac_tx_path();
}

static void process_cmd(void)
{
    if (strncmp(cmd, "HELP", 4) == 0) {
        cmd_help();
    } else if (strncmp(cmd, "STAT", 4) == 0) {
        cmd_status();
    } else if (strncmp(cmd, "TXRS", 4) == 0) {
        restart_dac_tx_path();
        send_str("OK\r\n");
    } else if (strncmp(cmd, "LOOP", 4) == 0) {
        cmd_loop();
    } else if (strncmp(cmd, "RXSW", 4) == 0) {
        cmd_rxsw();
    } else if (strncmp(cmd, "ADCS", 4) == 0) {
        cmd_adcs();
    } else if (strncmp(cmd, "ADCT", 4) == 0) {
        cmd_adct();
    } else if (strncmp(cmd, "COUP", 4) == 0) {
        cmd_coup();
    } else if (strncmp(cmd, "NSRC", 4) == 0) {
        cmd_nsrc();
    } else if (strncmp(cmd, "NEUR", 4) == 0) {
        cmd_neur();
    } else if (strncmp(cmd, "RDRO", 4) == 0) {
        char *p = &cmd[5];
        u32 idx;
        if (!parse_u32_arg(&p, &idx) || idx > 7u) {
            send_str("ERR RDRO expects register 0..7\r\n");
            return;
        }
        print_reg("RO", idx, Xil_In32(ro_addr(idx)));
    } else if (strncmp(cmd, "RDRW", 4) == 0) {
        char *p = &cmd[5];
        u32 idx;
        if (!parse_u32_arg(&p, &idx) || idx > 7u) {
            send_str("ERR RDRW expects register 0..7\r\n");
            return;
        }
        print_reg("RW", idx, Xil_In32(rw_addr(idx)));
    } else if (strncmp(cmd, "WRTE", 4) == 0) {
        char *p = &cmd[5];
        u32 idx;
        u32 val;
        if (!parse_u32_arg(&p, &idx) || idx > 7u) {
            send_str("ERR WRTE expects register 0..7 and value\r\n");
            return;
        }
        if (!parse_u32_arg(&p, &val)) {
            send_str("ERR WRTE expects register 0..7 and value\r\n");
            return;
        }
        Xil_Out32(rw_addr(idx), val);
        send_str("OK\r\n");
#if HAS_BRAM_DATAPLANE
    } else if (strncmp(cmd, "PROG", 4) == 0) {
        char *p = &cmd[4];
        u32 channel = 0;
        u32 words = DAC_PROGRAM_WORDS_PER_CHANNEL;
        int has_first = parse_u32_arg(&p, &channel);
        int has_second = 0;
        if (has_first) {
            has_second = parse_u32_arg(&p, &words);
        }
        if (has_first && !has_second && channel >= DAC_PROGRAM_CHANNELS) {
            words = channel;
            channel = 0;
        }
        cmd_program(channel, words);
    } else if (strncmp(cmd, "DPWR", 4) == 0) {
        char *p = &cmd[4];
        u32 channel = 0;
        u32 start_word = 0;
        u32 words = 0;
        if (!parse_u32_arg(&p, &channel) ||
            !parse_u32_arg(&p, &start_word) ||
            !parse_u32_arg(&p, &words)) {
            send_str("ERR DPWR expects channel, start, and word count\r\n");
            return;
        }
        cmd_dpwr(channel, start_word, words);
    } else if (strncmp(cmd, "DPRD", 4) == 0) {
        char *p = &cmd[4];
        u32 channel = 0;
        u32 start_word = 0;
        u32 words = 16;
        int has_first = parse_u32_arg(&p, &channel);
        int has_second = 0;
        if (has_first) {
            has_second = parse_u32_arg(&p, &start_word);
            parse_u32_arg(&p, &words);
        }
        if (has_first && !has_second && channel >= DAC_PROGRAM_CHANNELS) {
            start_word = channel;
            channel = 0;
        }
        cmd_dprd(channel, start_word, words);
    } else if (strncmp(cmd, "CAPS", 4) == 0) {
        print_capture_status();
    } else if (strncmp(cmd, "CAPT", 4) == 0) {
        char *p = &cmd[4];
        u32 frames = ADC_CAPTURE_FRAMES;
        parse_u32_arg(&p, &frames);
        cmd_capture(frames, 0);
    } else if (strncmp(cmd, "PCAP", 4) == 0) {
        char *p = &cmd[4];
        u32 frames = ADC_CAPTURE_FRAMES;
        parse_u32_arg(&p, &frames);
        cmd_capture(frames, 1);
#else
    } else if (strncmp(cmd, "PROG", 4) == 0 ||
               strncmp(cmd, "DPRD", 4) == 0 ||
               strncmp(cmd, "CAPS", 4) == 0 ||
               strncmp(cmd, "CAPT", 4) == 0 ||
               strncmp(cmd, "PCAP", 4) == 0) {
        send_str("ERR BRAM dataplane not built; rebuild with --with-bram-dataplane\r\n");
#endif
#if HAS_PS_DDR_DMA
    } else if (strncmp(cmd, "DMAC", 4) == 0) {
        char *p = &cmd[4];
        u32 frames = ADC_CAPTURE_FRAMES;
        parse_u32_arg(&p, &frames);
        cmd_dma_capture(frames);
    } else if (strncmp(cmd, "DSTA", 4) == 0) {
        print_dma_status();
    } else if (strncmp(cmd, "DDRD", 4) == 0) {
        char *p = &cmd[4];
        u32 chip = 0;
        u32 start_word = 0;
        u32 words = 16;
        if (!parse_u32_arg(&p, &chip)) {
            send_str("ERR DDRD expects chip 0|1, optional start, optional word count\r\n");
            return;
        }
        parse_u32_arg(&p, &start_word);
        parse_u32_arg(&p, &words);
        cmd_ddr_read(chip, start_word, words);
#else
    } else if (strncmp(cmd, "DMAC", 4) == 0 ||
               strncmp(cmd, "DSTA", 4) == 0 ||
               strncmp(cmd, "DDRD", 4) == 0) {
        send_str("ERR PS DDR DMA not built; rebuild with --with-ps-ddr-dma\r\n");
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
