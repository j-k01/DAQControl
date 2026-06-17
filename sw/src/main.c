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

/* IZH neuron config: profiles live in a dual-clock "config bank" BRAM that the
 * MicroBlaze fills over AXI; rw_reg4[0] is toggled to fire one prog_start pulse
 * to the neuron-domain reader, which (re)loads every neuron whose mask bit is
 * set in control word 0. */
#define RW4_NEURON_PROG_TOGGLE (1u << 0)

/* DAC source crossbar: register 17 holds one 4-bit select per DAC (nibble ch),
 * routing any of 16 sources to each DAC independently.  Synced straight into
 * the GT clock domain; independent of the neuron config bank.  The select reset
 * value is 0 (all DACs off), so NSRC must run to route anything. */
#define DAC_XBAR_SEL_REG   (REG_BASE + 0x44)   /* reg17 */
#define XSRC_OFF     0u
#define XSRC_DDS     1u    /* broadcast sine (single entry, any DAC) */
#define XSRC_BRAM0   2u    /* +ch : BRAM channel 0..3   */
#define XSRC_SPIKE0  6u    /* +ch : neuron spike pulse 0..3 */
#define XSRC_MON0    10u   /* +ch : neuron current monitor 0..3 */
#define XSRC_TAG     14u   /* debug tag word */
#define XSRC_NIBBLE(ch)  (((u32)(ch) & 3u) * 4u)

/* Programmable current player: control register (reg16) + waveform BRAM.  The
 * player advances the cur_wave read pointer every cycles_per_sample clk_50
 * cycles, loops 0..last_index, and feeds the held Q16.16 sample as i_external
 * into every neuron (and into the per-neuron current monitors).  Effective
 * current frequency = 50 MHz / (cycles_per_sample * (last_index + 1)). */
#define CUR_PLAYER_CTRL_REG  (REG_BASE + 0x40)   /* reg16 */
#define CUR_PLAYER_CPS_SHIFT   0u                /* [15:0]  cycles_per_sample */
#define CUR_PLAYER_LAST_SHIFT  16u               /* [25:16] last_index        */
#define CUR_PLAYER_RUN         (1u << 30)
#define CUR_PLAYER_RESTART     (1u << 31)        /* toggle to reset to sample 0 */
#define CUR_WAVE_BRAM_BASE     0xC0050000u       /* 1024 x Q16.16, AXI port A */
#define CUR_WAVE_DEPTH         1024u

/* Programmable spike-pulse shape: register-file regs 32-47 hold 8 beat-words
 * (= 32 signed s16 DAC samples, 2/reg), reg 18 holds the pulse length in beats
 * (1..8).  Each neuron's spike shaper plays shape[0..nbeats*4-1] at the DAC rate.
 * Loaded by PULS; boot-initialized to a negative trapezoid. */
#define SPIKE_NBEATS_REG       (REG_BASE + 18u*4u)   /* reg 18: pulse length in beats */
#define SPIKE_SHAPE_BASE       (REG_BASE + 32u*4u)   /* regs 32-47: 8 beat-words */
#define SPIKE_SHAPE_REGS       16u                   /* 16 regs = 8 beat-words */
#define SPIKE_MAX_SAMPLES      32u
#define SPIKE_MAX_BEATS        8u

#if HAS_BRAM_DATAPLANE
#define NEURON_CFG_BRAM_BASE   XPAR_NEURON_CFG_BRAM_CTRL_S_AXI_BASEADDR
/* word offsets within the config bank */
#define NCFG_CTRL_WORD         0u   /* [3:0] program mask, [8] global-set */
#define NCFG_DT_WORD           1u   /* global dt   (Q16.16) */
#define NCFG_PERIOD_WORD       2u   /* global update_period (low 24 bits) */
#define NCFG_NEURON_BASE       4u   /* neuron 0 profile base */
#define NCFG_NEURON_STRIDE     8u   /* words per neuron (a,b,c,d,Ic,I + pad) */
#define NCFG_CTRL_GLOBAL_SET   (1u << 8)

/* NEUR param codes (host-facing).  Per-neuron: a/b/c/d/i/iconst.  Global:
 * dt/period.  default = reload regular profile; reset = re-pulse (reset v/u). */
#define NPARAM_A        0u
#define NPARAM_B        1u
#define NPARAM_C        2u
#define NPARAM_D        3u
#define NPARAM_I        4u
#define NPARAM_ICONST   5u
#define NPARAM_DT       6u
#define NPARAM_PERIOD   7u
#define NPARAM_DEFAULT  8u
#define NPARAM_RESET    9u
#endif

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
#define DMA_S2MM_CURDESC     0x38u
#define DMA_S2MM_CURDESC_MSB 0x3Cu
#define DMA_S2MM_TAILDESC    0x40u
#define DMA_S2MM_TAILDESC_MSB 0x44u
#define DMA_DMACR_RS         (1u << 0)
#define DMA_DMACR_RESET      (1u << 2)
#define DMA_DMACR_CYCLIC     (1u << 4)
#define DMA_DMASR_HALTED     (1u << 0)
#define DMA_DMASR_IDLE       (1u << 1)
#define DMA_DMASR_ERR_MASK   ((1u << 4) | (1u << 5) | (1u << 6))
#define DMA_DMASR_IRQ_MASK   ((1u << 12) | (1u << 13) | (1u << 14))

/* Continuous decimated streaming: the PL decimator (RW6) feeds each DMA a
 * tlast-delimited 128 KB chunk stream; a cyclic SG descriptor ring makes the
 * DMA write those chunks into a DDR ring forever with no re-arm gaps. The
 * A53 drains the ring over UDP, reading the write pointer published in the
 * mailbox below. Descriptors and the mailbox live inside the MicroBlaze HP2
 * window (0x10000000..0x1003FFFF); the data rings do not need to (only the
 * DMA writes and the A53 reads them). */
#define STRM_CHUNK_BYTES     0x20000u   /* = decimator CHUNK_BEATS * 16 B */
#define STRM_RING_CHUNKS     256u
#define STRM_RING_BYTES      (STRM_CHUNK_BYTES * STRM_RING_CHUNKS) /* 32 MB */
#define STRM_DESC_STRIDE     0x40u
#define STRM_ONESHOT_DESC0   0x1003C000u
#define STRM_ONESHOT_DESC1   0x1003C040u
#define STRM_MAILBOX         0x1003FF00u
#define STRM_MAGIC_RUNNING   0x53545201u
#define STRM_MAGIC_STOPPED   0x53545200u
#define RW6_STREAM_ENABLE    (1u << 31)
#define RW6_STREAM_USECIC    (1u << 30)  /* chip 1 (ch2/3): CIC vs keep-1-of-D */

/* ---- Full-rate burst capture (no decimation) -----------------------------
 * Both DMAs capture in parallel into separate 512 MB DDR regions, sample-
 * aligned (one trigger fires both, ADCs SYSREF-synced). RW6 carries the beat
 * count per chip (16 B/beat). The A53 reads the regions out over UDP on MB
 * request via the burst mailbox. */
#define BURST_DDR_BASE0   0x40000000u    /* chip0 capture region (512 MB)     */
#define BURST_DDR_BASE1   0x60000000u    /* chip1 capture region (512 MB)     */
#define BURST_MAX_BYTES   0x20000000u    /* 512 MB/chip                       */
/* Per-descriptor byte count. MUST fit the SG buffer-length field, which is 26
 * bits (c_sg_length_width=26) -> max 0x03FFFFFF. 0x04000000 (64 MB) is 2^26,
 * one too many: it masks to length 0 -> zero-length descriptor -> DMAIntErr on
 * any capture >= 64 MB/chip. 32 MB chunks fit and split 512 MB into 16 descs. */
#define BURST_DESC_BYTES  0x02000000u    /* 32 MB/descriptor (fits 26-bit len) */
#define BURST_MAGIC       0x42435054u    /* mailbox magic: burst armed        */
/* burst mailbox layout (at STRM_MAILBOX): 00=magic 04=bytes/chip 08=base0
 * 0C=base1 10=readout_req(MB++) 14=readout_done(A53 echo) 18=beats */

static const u32 strm_desc_base[ADC_DMA_CHIPS] = { 0x10030000u, 0x10034000u };
static const u32 strm_ring_base[ADC_DMA_CHIPS] = { 0x18000000u, 0x1A000000u };

static u32 stream_active = 0;
static u32 stream_decim = 0;
static u32 stream_usecic = 0;
static u32 stream_pub_count = 0;
/* Monotonic per-chip count of bytes the DMA has completed, so the A53 can
 * compute an unambiguous reader-vs-writer distance (wrapped offsets can't tell
 * "behind" from "lapped"). */
static u32 stream_write_total[ADC_DMA_CHIPS];
static u32 stream_last_idx[ADC_DMA_CHIPS];

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

#if HAS_BRAM_DATAPLANE
static void neuron_program(u32 mask, u32 global_set);
#endif

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

#if HAS_BRAM_DATAPLANE
/* Firmware-side shadow of the config bank.  We keep all 4 neurons + globals so
 * a partial update (e.g. just change neuron 2's profile) rewrites a coherent
 * full image into the BRAM; the reader only resets/reloads masked neurons. */
struct neuron_image {
    u32 a, b, c, d, iconst, i;   /* Q16.16 */
};
static struct neuron_image neuron_img[4];
static u32 neuron_g_dt = NEURON_DEFAULT_DT;
static u32 neuron_g_period = NEURON_DEFAULT_PERIOD;

static void neuron_image_init(void)
{
    int n;
    for (n = 0; n < 4; n++) {
        neuron_img[n].a = neuron_profiles[0].a;     /* regular-spiking */
        neuron_img[n].b = neuron_profiles[0].b;
        neuron_img[n].c = neuron_profiles[0].c;
        neuron_img[n].d = neuron_profiles[0].d;
        neuron_img[n].iconst = neuron_profiles[0].iconst;
        neuron_img[n].i = NEURON_DEFAULT_I;
    }
    neuron_g_dt = NEURON_DEFAULT_DT;
    neuron_g_period = NEURON_DEFAULT_PERIOD;
}
#endif

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

/* Match `word` as a whole token, optionally followed by a single 0-3 digit
 * (e.g. "bram" or "bram2").  On match sets *digit (0 if none) and *had_digit. */
static int token_pref_digit(const char *token, const char *word,
                            u32 *digit, int *had_digit)
{
    const char *t = token;

    while (*word != '\0') {
        if (ascii_upper(*t) != ascii_upper(*word))
            return 0;
        t++;
        word++;
    }

    if (*t >= '0' && *t <= '3') {
        *digit = (u32)(*t - '0');
        *had_digit = 1;
        t++;
    } else {
        *digit = 0;
        *had_digit = 0;
    }

    return *t == '\0' || *t == ' ' || *t == '\t';
}

/* Resolve a DAC crossbar source token into a 4-bit code (0..15) for `channel`.
 * Channel-relative names (bram/spike/mon with no digit) map to that DAC's own
 * index; an explicit digit (bram2, mon0, ...) or a raw 0..15 picks absolutely. */
static int parse_dac_source(char **cursor, u32 channel, u32 *code)
{
    char *p = *cursor;
    u32 d;
    int hd;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0')
        return 0;

    if (token_eq_ci(p, "off") || token_eq_ci(p, "none") || token_eq_ci(p, "zero")) {
        *code = XSRC_OFF;
    } else if (token_eq_ci(p, "dds") || token_eq_ci(p, "sine")) {
        *code = XSRC_DDS;
    } else if (token_pref_digit(p, "bram", &d, &hd) ||
               token_pref_digit(p, "program", &d, &hd)) {
        *code = XSRC_BRAM0 + (hd ? d : (channel & 3u));
    } else if (token_pref_digit(p, "spike", &d, &hd) ||
               token_pref_digit(p, "izh", &d, &hd) ||
               token_pref_digit(p, "neuron", &d, &hd)) {
        *code = XSRC_SPIKE0 + (hd ? d : (channel & 3u));
    } else if (token_pref_digit(p, "mon", &d, &hd) ||
               token_pref_digit(p, "monitor", &d, &hd) ||
               token_pref_digit(p, "imon", &d, &hd)) {
        *code = XSRC_MON0 + (hd ? d : (channel & 3u));
    } else if (token_eq_ci(p, "tag")) {
        *code = XSRC_TAG;
    } else if (parse_u32_arg(&p, code)) {
        if (*code > 15u)
            return 0;
        *cursor = p;                 /* parse_u32_arg already advanced p */
        return 1;
    } else {
        return 0;
    }

    while (*p != '\0' && *p != ' ' && *p != '\t')    /* step past named token */
        p++;
    *cursor = p;
    return 1;
}

/* Per-DAC source select is the 16:4 crossbar in the GT clock domain (reg17, one
 * 4-bit nibble per DAC).  Any of 16 sources -- off / DDS / BRAM 0-3 / spike 0-3
 * / current monitor 0-3 / tag -- routes to any DAC, independent of the neuron
 * config bank.  program_enable (rw_reg3[6]) gates the BRAM read pipeline, so we
 * keep it on whenever any DAC routes a BRAM source. */
static void cmd_nsrc(void)
{
    char *p = &cmd[4];
    char *src_tok;
    u32 channel = 0;
    u32 all_channels = 1;
    u32 first;
    u32 code;
    u32 ch;
    u32 sel;

    while (*p == ' ' || *p == '\t')
        p++;

    if (token_eq_ci(p, "all")) {
        advance_token(&p);
    } else {
        char *save_p = p;
        if (parse_u32_arg(&p, &first) && first < 4u) {
            channel = first;
            all_channels = 0;
            while (*p == ' ' || *p == '\t')
                p++;
        } else {
            p = save_p;
        }
    }

    /* Validate the source token (resolved for the first/target channel). */
    src_tok = p;
    {
        char *vp = src_tok;
        if (!parse_dac_source(&vp, all_channels ? 0u : channel, &code)) {
            send_str("ERR NSRC [all|0..3] "
                     "off|dds|bram[0-3]|spike[0-3]|mon[0-3]|tag|0..15\r\n");
            return;
        }
    }

    sel = Xil_In32(DAC_XBAR_SEL_REG);
    if (all_channels) {
        for (ch = 0; ch < 4u; ch++) {
            char *vp = src_tok;               /* re-resolve per DAC (relative names) */
            parse_dac_source(&vp, ch, &code);
            sel = (sel & ~(0xFu << XSRC_NIBBLE(ch))) |
                  ((code & 0xFu) << XSRC_NIBBLE(ch));
        }
    } else {
        sel = (sel & ~(0xFu << XSRC_NIBBLE(channel))) |
              ((code & 0xFu) << XSRC_NIBBLE(channel));
    }
    Xil_Out32(DAC_XBAR_SEL_REG, sel);

#if HAS_BRAM_DATAPLANE
    {
        u32 rw3 = Xil_In32(RW_REG3);
        u32 any_bram = 0;
        for (ch = 0; ch < 4u; ch++) {
            u32 c = (sel >> XSRC_NIBBLE(ch)) & 0xFu;
            if (c >= XSRC_BRAM0 && c <= XSRC_BRAM0 + 3u)
                any_bram = 1;
        }
        if (any_bram)
            rw3 |= RW3_DAC_PROGRAM_EN;
        else
            rw3 &= ~RW3_DAC_PROGRAM_EN;
        Xil_Out32(RW_REG3, rw3);
    }
#endif

    send_str("DAC xbar ");
    if (all_channels) {
        send_str("all =");
        for (ch = 0; ch < 4u; ch++) {
            send_str(" ");
            send_uint((sel >> XSRC_NIBBLE(ch)) & 0xFu);
        }
    } else {
        send_str("ch");
        send_uint(channel);
        send_str(" = ");
        send_uint((sel >> XSRC_NIBBLE(channel)) & 0xFu);
    }
    send_str("\r\n");
}

#if HAS_BRAM_DATAPLANE
/* CURP off                       -> stop the current player (run=0)
 * CURP <cps> <last_index> <amp>  -> fill cur_wave[0..last_index] with a
 *   zero-mean Q16.16 triangle of amplitude +/-amp, then run the player at that
 *   rate.  The held sample becomes i_external on every neuron, so routing a
 *   current monitor (NSRC <ch> mon<n>) mirrors it out a DAC. */
static u32 cur_player_restart_tog = 0u;
static void cmd_curp(void)
{
    char *p = &cmd[4];
    u32 cps, last, amp, n, half, i;

    while (*p == ' ' || *p == '\t')
        p++;

    if (token_eq_ci(p, "off") || token_eq_ci(p, "stop")) {
        Xil_Out32(CUR_PLAYER_CTRL_REG, 0u);
        send_str("CURP off\r\n");
        return;
    }

    if (!parse_u32_arg(&p, &cps) || !parse_u32_arg(&p, &last) ||
        !parse_u32_arg(&p, &amp)) {
        send_str("ERR CURP [off | <cycles_per_sample> <last_index> <amp_q16>]\r\n");
        return;
    }
    if (last >= CUR_WAVE_DEPTH)
        last = CUR_WAVE_DEPTH - 1u;

    n = last + 1u;
    half = (n >> 1) ? (n >> 1) : 1u;
    for (i = 0u; i < n; i++) {
        s64 v;
        if (i < half)
            v = -(s64)amp + (2 * (s64)amp * (s64)i) / (s64)half;
        else
            v = (s64)amp - (2 * (s64)amp * (s64)(i - half)) / (s64)half;
        Xil_Out32(CUR_WAVE_BRAM_BASE + i * 4u, (u32)(s32)v);
    }

    cur_player_restart_tog ^= 1u;
    Xil_Out32(CUR_PLAYER_CTRL_REG,
              ((cps  & 0xFFFFu) << CUR_PLAYER_CPS_SHIFT) |
              ((last & 0x3FFu)  << CUR_PLAYER_LAST_SHIFT) |
              CUR_PLAYER_RUN |
              (cur_player_restart_tog ? CUR_PLAYER_RESTART : 0u));

    send_str("CURP run cps=");
    send_uint(cps);
    send_str(" last=");
    send_uint(last);
    send_str(" amp=0x");
    send_hex(amp);
    send_str("\r\n");
}
#endif

/* Parse an optionally-signed integer (decimal or 0x hex), for pulse samples. */
static int parse_s32_arg(char **cursor, s32 *value)
{
    char *p = *cursor;
    int neg = 0;
    u32 mag;

    while (*p == ' ' || *p == '\t')
        p++;
    if (*p == '-') { neg = 1; p++; }
    else if (*p == '+') { p++; }
    if (!parse_u32_arg(&p, &mag))
        return 0;
    *value = neg ? -(s32)mag : (s32)mag;
    *cursor = p;
    return 1;
}

/* Write `count` (<=32) signed s16 samples as the spike-pulse shape: 2 samples
 * per register (regs 32-47), zero-padded, and set nbeats = ceil(count/4). */
static void spike_shape_write(const s16 *samples, u32 count)
{
    u32 buf[SPIKE_SHAPE_REGS];
    u32 i, nb;

    if (count > SPIKE_MAX_SAMPLES)
        count = SPIKE_MAX_SAMPLES;
    for (i = 0; i < SPIKE_SHAPE_REGS; i++)
        buf[i] = 0u;
    for (i = 0; i < count; i++) {
        u32 v = (u32)(u16)samples[i];
        if (i & 1u) buf[i >> 1] |= (v << 16);
        else        buf[i >> 1] |= v;
    }
    for (i = 0; i < SPIKE_SHAPE_REGS; i++)
        Xil_Out32(SPIKE_SHAPE_BASE + i * 4u, buf[i]);

    nb = (count + 3u) >> 2;                 /* ceil(count/4) beats */
    if (nb == 0u) nb = 1u;
    if (nb > SPIKE_MAX_BEATS) nb = SPIKE_MAX_BEATS;
    Xil_Out32(SPIKE_NBEATS_REG, nb);
}

/* Power-on default: the original 7 ns trapezoid (1/4,1/2,1,1,1,1/2,1/4 of
 * 0x6000), matching the old fixed izh_spike_trapezoid. */
static void spike_shape_init_default(void)
{
    static const s16 orig_trap[7] = {
        (s16)0x1800, (s16)0x3000, (s16)0x6000, (s16)0x6000,
        (s16)0x6000, (s16)0x3000, (s16)0x1800
    };
    spike_shape_write(orig_trap, 7u);
}

#if HAS_BRAM_DATAPLANE
/* Power-on default for the DAC program BRAMs: one period of a 10 MHz sine.  At
 * 1 GS/s that's exactly 100 samples = 25 frames, so RW3[31:8]=25 loops it
 * seamlessly.  Same sine on all four DAC BRAMs (amplitude 0x6000).  Built from a
 * quarter-wave table (sin(2*pi*k/100)*0x6000, k=0..25) + symmetry -- no libm. */
static void dac_bram_init_default(void)
{
    static const s16 qt[26] = {
            0,  1543,  3080,  4605,  6112,  7594,  9047, 10464, 11839, 13169,
        14446, 15666, 16824, 17916, 18937, 19882, 20750, 21536, 22237, 22850,
        23373, 23804, 24141, 24382, 24528, 24576
    };
    s16 s[100];
    u32 ch, w, rw3;
    int k;

    for (k = 0; k < 100; k++) {
        if      (k <= 25) s[k] =  qt[k];
        else if (k <= 50) s[k] =  qt[50 - k];
        else if (k <= 75) s[k] = -qt[k - 50];
        else              s[k] = -qt[100 - k];
    }
    for (ch = 0; ch < DAC_PROGRAM_CHANNELS; ch++)
        for (w = 0; w < 50u; w++)
            Xil_Out32(dac_program_bram_base[ch] + w * 4u,
                      ((u32)(u16)s[2u*w + 1u] << 16) | (u32)(u16)s[2u*w]);

    rw3 = (Xil_In32(RW_REG3) & 0x000000FFu) | (25u << 8);   /* loop = 25 frames */
    Xil_Out32(RW_REG3, rw3);
}
#endif

/* PULS default              -> reload the negative-trapezoid default shape
 * PULS <s0> <s1> ... <sN>    -> set the spike pulse to N (<=32) signed samples
 *   (decimal counts or 0x hex), full-range s16; nbeats auto = ceil(N/4). */
static void cmd_puls(void)
{
    char *p = &cmd[4];
    s16 samples[SPIKE_MAX_SAMPLES];
    u32 count = 0u;
    s32 v;

    while (*p == ' ' || *p == '\t')
        p++;
    if (token_eq_ci(p, "default")) {
        spike_shape_init_default();
        send_str("PULS default (original 7 ns trapezoid, 7 samples)\r\n");
        return;
    }
    while (count < SPIKE_MAX_SAMPLES && parse_s32_arg(&p, &v))
        samples[count++] = (s16)v;
    if (count == 0u) {
        send_str("ERR PULS [default | s0 s1 ... up to 32 signed samples]\r\n");
        return;
    }
    spike_shape_write(samples, count);
    send_str("PULS loaded ");
    send_uint(count);
    send_str(" samples, nbeats=");
    send_uint((count + 3u) >> 2);
    send_str("\r\n");
}

static int parse_neuron_param(char **cursor, u32 *param, int *needs_value)
{
    char *p = *cursor;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0')
        return 0;

    /* Param codes match the BRAM image layout: per-neuron a/b/c/d/i/iconst and
     * the two globals dt/period.  offset/source/output are gone (source is NSRC;
     * there are no v_offset / direct-voltage modes any more). */
    *needs_value = 1;
    if (token_eq_ci(p, "a")) {
        *param = NPARAM_A;
    } else if (token_eq_ci(p, "b")) {
        *param = NPARAM_B;
    } else if (token_eq_ci(p, "c")) {
        *param = NPARAM_C;
    } else if (token_eq_ci(p, "d")) {
        *param = NPARAM_D;
    } else if (token_eq_ci(p, "i") || token_eq_ci(p, "current")) {
        *param = NPARAM_I;
    } else if (token_eq_ci(p, "dt") || token_eq_ci(p, "timestep")) {
        *param = NPARAM_DT;
    } else if (token_eq_ci(p, "iconst") || token_eq_ci(p, "bias")) {
        *param = NPARAM_ICONST;
    } else if (token_eq_ci(p, "period") || token_eq_ci(p, "rate") ||
               token_eq_ci(p, "divider") || token_eq_ci(p, "update")) {
        *param = NPARAM_PERIOD;
    } else if (token_eq_ci(p, "default") || token_eq_ci(p, "defaults")) {
        *param = NPARAM_DEFAULT;
        *needs_value = 0;
    } else if (token_eq_ci(p, "reset")) {
        *param = NPARAM_RESET;
        *needs_value = 0;
    } else if (!parse_u32_arg(&p, param)) {
        return 0;
    }

    while (*p != '\0' && *p != ' ' && *p != '\t')
        p++;
    *cursor = p;
    return *param <= NPARAM_RESET;
}

static void neuron_bram_write(u32 word_index, u32 value)
{
    Xil_Out32(NEURON_CFG_BRAM_BASE + (word_index << 2), value);
}

/* Write the full shadow image + control word into the config bank, then toggle
 * rw_reg4[0] so the neuron-domain reader (re)loads every masked neuron.  The
 * reader holds masked neurons in reset while it copies, then releases them;
 * unmasked neurons keep free-running and streaming spikes. */
static void neuron_program(u32 mask, u32 global_set)
{
    int n;
    u32 base;

    neuron_bram_write(NCFG_DT_WORD, neuron_g_dt);
    neuron_bram_write(NCFG_PERIOD_WORD, neuron_g_period & 0x00FFFFFFu);
    for (n = 0; n < 4; n++) {
        base = NCFG_NEURON_BASE + (u32)n * NCFG_NEURON_STRIDE;
        neuron_bram_write(base + 0u, neuron_img[n].a);
        neuron_bram_write(base + 1u, neuron_img[n].b);
        neuron_bram_write(base + 2u, neuron_img[n].c);
        neuron_bram_write(base + 3u, neuron_img[n].d);
        neuron_bram_write(base + 4u, neuron_img[n].iconst);
        neuron_bram_write(base + 5u, neuron_img[n].i);
    }
    /* control word last; all AXI BRAM writes retire before the toggle, so the
     * reader sees a coherent image. */
    neuron_bram_write(NCFG_CTRL_WORD,
                      (mask & 0xFu) | (global_set ? NCFG_CTRL_GLOBAL_SET : 0u));

    Xil_Out32(RW_REG4, Xil_In32(RW_REG4) ^ RW4_NEURON_PROG_TOGGLE);
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

static void apply_neuron_profile(u32 mask, const struct neuron_profile *profile)
{
    int n;

    for (n = 0; n < 4; n++) {
        if (mask & (1u << n)) {
            neuron_img[n].a = profile->a;
            neuron_img[n].b = profile->b;
            neuron_img[n].c = profile->c;
            neuron_img[n].d = profile->d;
            neuron_img[n].iconst = profile->iconst;
            neuron_img[n].i = NEURON_DEFAULT_I;
        }
    }
    neuron_program(mask, 1u);
}

static void cmd_neur(void)
{
    char *p = &cmd[4];
    u32 channel = 0;
    u32 all_channels = 0;
    u32 mask;
    u32 param;
    u32 value = 0;
    int needs_value;
    int n;
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
    mask = all_channels ? 0xFu : (1u << channel);

    while (*p == ' ' || *p == '\t')
        p++;

    /* optional "profile"/"type" keyword before the profile name */
    if (token_eq_ci(p, "profile") || token_eq_ci(p, "type")) {
        advance_token(&p);
        while (*p == ' ' || *p == '\t')
            p++;
    }

    profile = find_neuron_profile(p);
    if (profile != NULL) {
        apply_neuron_profile(mask, profile);
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
        send_uint(neuron_g_period);
        send_str("\r\n");
        return;
    }

    if (!parse_neuron_param(&p, &param, &needs_value)) {
        send_str("ERR NEUR expects profile or param a,b,c,d,i,dt,iconst,period,reset,default\r\n");
        return;
    }

    if (needs_value && !parse_u32_arg(&p, &value)) {
        send_str("ERR NEUR param expects raw Q16.16 value\r\n");
        return;
    }

    switch (param) {
    case NPARAM_A:
    case NPARAM_B:
    case NPARAM_C:
    case NPARAM_D:
    case NPARAM_I:
    case NPARAM_ICONST:
        for (n = 0; n < 4; n++) {
            if (!(mask & (1u << n)))
                continue;
            switch (param) {
            case NPARAM_A:      neuron_img[n].a = value;      break;
            case NPARAM_B:      neuron_img[n].b = value;      break;
            case NPARAM_C:      neuron_img[n].c = value;      break;
            case NPARAM_D:      neuron_img[n].d = value;      break;
            case NPARAM_I:      neuron_img[n].i = value;      break;
            case NPARAM_ICONST: neuron_img[n].iconst = value; break;
            default: break;
            }
        }
        neuron_program(mask, 1u);
        break;
    case NPARAM_DT:                     /* global: live, no neuron reset */
        neuron_g_dt = value;
        neuron_program(0u, 1u);
        break;
    case NPARAM_PERIOD:                 /* global */
        neuron_g_period = value;
        neuron_program(0u, 1u);
        break;
    case NPARAM_DEFAULT:
        apply_neuron_profile(mask, &neuron_profiles[0]);
        break;
    case NPARAM_RESET:                  /* re-pulse: masked neurons reset v/u */
        neuron_program(mask, 1u);
        break;
    default:
        send_str("ERR NEUR unknown param\r\n");
        return;
    }

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

static void dma_write_desc(u32 desc, u32 next, u32 buf, u32 bytes)
{
    Xil_Out32(desc + 0x00u, next);
    Xil_Out32(desc + 0x04u, 0u);
    Xil_Out32(desc + 0x08u, buf);
    Xil_Out32(desc + 0x0Cu, 0u);
    Xil_Out32(desc + 0x10u, 0u);
    Xil_Out32(desc + 0x14u, 0u);
    Xil_Out32(desc + 0x18u, bytes & 0x03FFFFFFu);
    Xil_Out32(desc + 0x1Cu, 0u);
}

/* The DMA is built with scatter-gather (for cyclic streaming), which removes
 * the simple-mode DA/LENGTH registers, so the one-shot burst capture is a
 * single self-terminating descriptor: tail == head stops after one chunk. */
static void dma_arm_s2mm(u32 chip, u32 dest_addr, u32 bytes)
{
    u32 base = adc_dma_base[chip];
    u32 desc = (chip == 0u) ? STRM_ONESHOT_DESC0 : STRM_ONESHOT_DESC1;

    dma_reset(chip);
    dma_write_desc(desc, desc, dest_addr, bytes);
    Xil_Out32(base + DMA_S2MM_DMASR, DMA_DMASR_IRQ_MASK | DMA_DMASR_ERR_MASK);
    Xil_Out32(base + DMA_S2MM_CURDESC, desc);
    Xil_Out32(base + DMA_S2MM_CURDESC_MSB, 0u);
    Xil_Out32(base + DMA_S2MM_DMACR, DMA_DMACR_RS);
    Xil_Out32(base + DMA_S2MM_TAILDESC, desc);
    Xil_Out32(base + DMA_S2MM_TAILDESC_MSB, 0u);
}

/* Pulse the ADC capture trigger (RW3[3] rising edge) WITHOUT disturbing the
 * DAC source/program bits, so a DAC loopback keeps playing through the burst. */
static void pulse_adc_capture(void)
{
    u32 rw3 = Xil_In32(RW_REG3) & ~RW3_CAPTURE_START;

    Xil_Out32(RW_REG3, rw3);
    Xil_Out32(RW_REG3, rw3 | RW3_CAPTURE_START);
    short_delay();
    Xil_Out32(RW_REG3, rw3);
}

/* Arm a one-shot SG chain spanning total_bytes into a contiguous DDR region.
 * total_bytes is split across 64 MB descriptors (the SG 26-bit length max);
 * the PL asserts tlast at the end so the transfer self-terminates. The chain
 * reuses the per-chip descriptor ring region (strm_desc_base). */
static void dma_arm_burst(u32 chip, u32 base, u32 total_bytes)
{
    u32 dmabase = adc_dma_base[chip];
    u32 desc_base = strm_desc_base[chip];
    u32 n = (total_bytes + BURST_DESC_BYTES - 1u) / BURST_DESC_BYTES;
    u32 i;
    u32 off = 0u;

    if (n == 0u) {
        n = 1u;
    }
    dma_reset(chip);
    for (i = 0u; i < n; i++) {
        u32 desc = desc_base + i * STRM_DESC_STRIDE;
        u32 next = desc_base + ((i + 1u) % n) * STRM_DESC_STRIDE;
        u32 chunk = total_bytes - off;

        if (chunk > BURST_DESC_BYTES) {
            chunk = BURST_DESC_BYTES;
        }
        dma_write_desc(desc, next, base + off, chunk);
        off += chunk;
    }
    Xil_Out32(dmabase + DMA_S2MM_DMASR, DMA_DMASR_IRQ_MASK | DMA_DMASR_ERR_MASK);
    Xil_Out32(dmabase + DMA_S2MM_CURDESC, desc_base);
    Xil_Out32(dmabase + DMA_S2MM_CURDESC_MSB, 0u);
    Xil_Out32(dmabase + DMA_S2MM_DMACR, DMA_DMACR_RS);
    Xil_Out32(dmabase + DMA_S2MM_TAILDESC, desc_base + (n - 1u) * STRM_DESC_STRIDE);
    Xil_Out32(dmabase + DMA_S2MM_TAILDESC_MSB, 0u);
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

static u32 burst_bytes = BURST_MAX_BYTES;

/* BCAP [N[k|m]] -- full-rate, un-decimated, sample-aligned burst capture of all
 * 4 ADC channels into two 512 MB DDR regions. N defaults to MB ('m' optional,
 * back-compatible with BCAP <MB>); a 'k' suffix means KB for small grabs
 * (e.g. BCAP 64k = 64 KB/chip = 16384 samples/ch). Default 512 MB/chip. */
static void cmd_burst(char *args)
{
    char *p = args;
    u32 cnt = 512u;
    u32 unit = 0x100000u;                   /* default unit: MB */
    u32 bytes;
    u32 beats;
    u32 s0 = 0;
    u32 s1 = 0;

    while (*p == ' ') {
        p++;
    }
    parse_u32_arg(&p, &cnt);
    if (*p == 'k' || *p == 'K') {           /* KB */
        unit = 0x400u;
        p++;
    } else if (*p == 'm' || *p == 'M') {    /* MB (explicit) */
        p++;
    }
    if (cnt == 0u) {
        cnt = 512u;
        unit = 0x100000u;
    }
    bytes = cnt * unit;
    if (bytes > BURST_MAX_BYTES) {
        bytes = BURST_MAX_BYTES;
    }
    bytes &= ~0x0Fu;                         /* whole 16 B beats */
    if (bytes == 0u) {
        bytes = 0x10u;                       /* at least one beat */
    }
    beats = bytes >> 4;
    burst_bytes = bytes;

    Xil_Out32(RW_REG6, beats);              /* PL capture length (both chips) */
    dma_arm_burst(0u, BURST_DDR_BASE0, bytes);
    dma_arm_burst(1u, BURST_DDR_BASE1, bytes);

    /* publish region info for the A53 readout. readout_req (0x10) stays
     * monotonic across captures -- the A53 drains on any change -- so we do not
     * reset it here. */
    Xil_Out32(STRM_MAILBOX + 0x04u, bytes);
    Xil_Out32(STRM_MAILBOX + 0x08u, BURST_DDR_BASE0);
    Xil_Out32(STRM_MAILBOX + 0x0Cu, BURST_DDR_BASE1);
    Xil_Out32(STRM_MAILBOX + 0x18u, beats);
    Xil_Out32(STRM_MAILBOX + 0x00u, BURST_MAGIC);

    pulse_adc_capture();                    /* fire both chips together */

    if (!wait_dma_done(&s0, &s1)) {
        send_str("ERR BCAP timeout ");
        print_named_hex("dma0", s0);
        send_str(" ");
        print_named_hex("dma1", s1);
        send_str("\r\n");
        return;
    }
    send_str("OK BCAP bytes_per_chip=");
    send_uint(bytes);
    send_str(" beats=");
    send_uint(beats);
    send_str(" base0=");
    send_hex(BURST_DDR_BASE0);
    send_str(" base1=");
    send_hex(BURST_DDR_BASE1);
    send_str(" ");
    print_named_hex("dma0", s0);
    send_str(" ");
    print_named_hex("dma1", s1);
    send_str("\r\n");
}

/* BRDO -- ask the A53 to read the last captured regions out over UDP. Capture
 * (BCAP) and readout (BRDO) are decoupled, both MB-controlled. */
static void cmd_burst_readout(char *args)
{
    u32 req;

    (void)args;
    if (Xil_In32(STRM_MAILBOX + 0x00u) != BURST_MAGIC) {
        send_str("ERR BRDO no capture armed; run BCAP first\r\n");
        return;
    }
    req = Xil_In32(STRM_MAILBOX + 0x10u) + 1u;
    Xil_Out32(STRM_MAILBOX + 0x10u, req);   /* request A53 readout */
    send_str("OK BRDO request=");
    send_uint(req);
    send_str(" bytes_per_chip=");
    send_uint(burst_bytes);
    send_str(" (A53 draining over UDP)\r\n");
}

static void stream_stop(void)
{
    Xil_Out32(RW_REG6, 0u);
    dma_reset(0u);
    dma_reset(1u);
    stream_active = 0;
    Xil_Out32(STRM_MAILBOX + 0x00u, STRM_MAGIC_STOPPED);
}

static void stream_start(u32 decim, u32 usecic)
{
    u32 chip;
    u32 i;

    stream_stop();

    for (chip = 0; chip < ADC_DMA_CHIPS; chip++) {
        u32 base = adc_dma_base[chip];

        for (i = 0; i < STRM_RING_CHUNKS; i++) {
            u32 desc = strm_desc_base[chip] + i * STRM_DESC_STRIDE;
            u32 next = strm_desc_base[chip] +
                       ((i + 1u) % STRM_RING_CHUNKS) * STRM_DESC_STRIDE;
            dma_write_desc(desc, next,
                           strm_ring_base[chip] + i * STRM_CHUNK_BYTES,
                           STRM_CHUNK_BYTES);
        }

        Xil_Out32(base + DMA_S2MM_DMASR, DMA_DMASR_IRQ_MASK | DMA_DMASR_ERR_MASK);
        Xil_Out32(base + DMA_S2MM_CURDESC, strm_desc_base[chip]);
        Xil_Out32(base + DMA_S2MM_CURDESC_MSB, 0u);
        Xil_Out32(base + DMA_S2MM_DMACR, DMA_DMACR_RS | DMA_DMACR_CYCLIC);
        /* Cyclic mode: tail points outside the chain so the ring never halts. */
        Xil_Out32(base + DMA_S2MM_TAILDESC, 0x50u);
        Xil_Out32(base + DMA_S2MM_TAILDESC_MSB, 0u);
    }

    stream_decim = decim;
    stream_usecic = usecic ? 1u : 0u;
    stream_pub_count = 0;
    for (chip = 0; chip < ADC_DMA_CHIPS; chip++) {
        stream_write_total[chip] = 0u;
        stream_last_idx[chip] = 0u;
    }
    Xil_Out32(STRM_MAILBOX + 0x04u, decim);
    Xil_Out32(STRM_MAILBOX + 0x08u, STRM_RING_BYTES);
    Xil_Out32(STRM_MAILBOX + 0x0Cu, STRM_CHUNK_BYTES);
    Xil_Out32(STRM_MAILBOX + 0x10u, strm_ring_base[0]);
    Xil_Out32(STRM_MAILBOX + 0x14u, 0u);
    Xil_Out32(STRM_MAILBOX + 0x18u, strm_ring_base[1]);
    Xil_Out32(STRM_MAILBOX + 0x1Cu, 0u);
    Xil_Out32(STRM_MAILBOX + 0x28u, 0u);
    Xil_Out32(STRM_MAILBOX + 0x00u, STRM_MAGIC_RUNNING);

    stream_active = 1;
    Xil_Out32(RW_REG6, RW6_STREAM_ENABLE |
                       (stream_usecic ? RW6_STREAM_USECIC : 0u) |
                       (decim & 0xFFFFu));
}

/* Publish each chip's MONOTONIC bytes-written counter (not a wrapped ring
 * offset) so the A53 can compute an unambiguous reader-vs-writer distance.
 * Called from the main loop. */
static void stream_publish(void)
{
    u32 i;

    if (!stream_active) {
        return;
    }
    for (i = 0; i < ADC_DMA_CHIPS; i++) {
        u32 cur = Xil_In32(adc_dma_base[i] + DMA_S2MM_CURDESC);
        u32 idx = 0;
        u32 dchunks;

        if (cur >= strm_desc_base[i]) {
            idx = ((cur - strm_desc_base[i]) / STRM_DESC_STRIDE) % STRM_RING_CHUNKS;
        }
        /* chunks completed since last publish (mod ring), accumulated into a
         * free-running byte total. */
        dchunks = (idx - stream_last_idx[i]) & (STRM_RING_CHUNKS - 1u);
        stream_write_total[i] += dchunks * STRM_CHUNK_BYTES;
        stream_last_idx[i] = idx;
        Xil_Out32(STRM_MAILBOX + 0x14u + i * 8u, stream_write_total[i]);
        Xil_Out32(STRM_MAILBOX + 0x20u + i * 4u, dma_status(i));
    }
    stream_pub_count++;
    Xil_Out32(STRM_MAILBOX + 0x28u, stream_pub_count);
}

static void cmd_stream(char *args)
{
    char *p = args;
    u32 decim = 256u;

    while (*p == ' ') {
        p++;
    }
    if (strncmp(p, "STOP", 4) == 0) {
        stream_stop();
        send_str("OK STRM stopped\r\n");
        return;
    }
    if (strncmp(p, "CIC", 3) == 0) {
        /* Live A/B toggle of chip 1 (ch2/ch3) between CIC and keep-1-of-D. */
        u32 want = stream_usecic;

        p += 3;
        while (*p == ' ') {
            p++;
        }
        if (strncmp(p, "on", 2) == 0 || *p == '1') {
            want = 1u;
        } else if (strncmp(p, "off", 3) == 0 || *p == '0') {
            want = 0u;
        }
        if (!stream_active) {
            send_str("ERR STRM not running; start with STRM <decim> [cic] first\r\n");
            return;
        }
        stream_usecic = want;
        Xil_Out32(RW_REG6, RW6_STREAM_ENABLE |
                           (stream_usecic ? RW6_STREAM_USECIC : 0u) |
                           (stream_decim & 0xFFFFu));
        send_str("OK STRM cic=");
        send_uint(stream_usecic);
        send_str(" (chip1 ch2/ch3)\r\n");
        return;
    }
    if (strncmp(p, "STAT", 4) == 0) {
        send_str("STRM active=");
        send_uint(stream_active);
        send_str(" decim=");
        send_uint(stream_decim);
        send_str(" cic=");
        send_uint(stream_usecic);
        send_str(" w0=");
        send_hex(Xil_In32(STRM_MAILBOX + 0x14u));
        send_str(" w1=");
        send_hex(Xil_In32(STRM_MAILBOX + 0x1Cu));
        send_str(" ");
        print_named_hex("dmasr0", dma_status(0u));
        send_str(" ");
        print_named_hex("dmasr1", dma_status(1u));
        send_str("\r\n");
        return;
    }

    parse_u32_arg(&p, &decim);
    if (decim < 4u || decim > 0xFFFCu || (decim & 3u) != 0u) {
        send_str("ERR STRM decim must be a multiple of 4 in 4..65532\r\n");
        return;
    }

    u32 usecic = 0u;
    while (*p == ' ') {
        p++;
    }
    if (strncmp(p, "cic", 3) == 0 || strncmp(p, "CIC", 3) == 0) {
        usecic = 1u;  /* CIC D=128 on chip 1; run decim=128 to match chip 0 */
    }

    stream_start(decim, usecic);
    send_str("OK STRM decim=");
    send_uint(decim);
    send_str(" cic=");
    send_uint(usecic);
    send_str(" ring_bytes=");
    send_uint(STRM_RING_BYTES);
    send_str(" chunk_bytes=");
    send_uint(STRM_CHUNK_BYTES);
    send_str(" mailbox=");
    send_hex(STRM_MAILBOX);
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
    send_str("  NSRC [ch|all] src  DAC crossbar (reg17): off,dds,bram[0-3],spike[0-3],mon[0-3],tag,0..15\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("  CURP off | <cps> <last> <amp_q16>  current player: triangle into i_external; f=50MHz/(cps*(last+1))\r\n");
    send_str("  PULS default | <s0..sN>  spike pulse shape: up to 32 signed samples (full s16); default=neg trapezoid\r\n");
#endif
    send_str("  NEUR ch param value  set IZH Q16.16 param on ch=0..3 or all (writes config-bank BRAM)\r\n");
    send_str("                 params: a,b,c,d,i/current,iconst/bias (per-neuron); dt,period (global); reset,default\r\n");
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
    send_str("  BCAP [MB]        full-rate un-decimated burst capture of all 4 ADC ch (default 512 MB/chip)\r\n");
    send_str("  BRDO             ask the A53 to read the last BCAP regions out over UDP\r\n");
    send_str("  STRM [decim [cic]]|STOP|STAT  continuous decimated stream into DDR rings (cyclic SG)\r\n");
    send_str("  STRM CIC on|off  live A/B toggle: chip1 ch2/3 CIC anti-alias (D=128) vs keep-1-of-D\r\n");
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
    send_str("    [5:4] RO3 DAC debug select (3=neuron debug word); [6] DAC program/BRAM enable\r\n");
    send_str("RW4 neuron: [0] config-bank prog toggle (NEUR pulses it)\r\n");
    send_str("REG17 DAC crossbar: 4 bits/DAC: 0=off,1=DDS,2-5=BRAM0-3,6-9=spike0-3,10-13=mon0-3,14=tag (NSRC)\r\n");
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
    send_str(" dac_xbar=");
    send_hex(Xil_In32(DAC_XBAR_SEL_REG) & 0xFFFFu);
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
    /* Default crossbar: DDS (broadcast sine, code 1) on all four DACs. */
    Xil_Out32(DAC_XBAR_SEL_REG, 0x00001111u);
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
#if HAS_BRAM_DATAPLANE
    } else if (strncmp(cmd, "CURP", 4) == 0) {
        cmd_curp();
#endif
    } else if (strncmp(cmd, "PULS", 4) == 0) {
        cmd_puls();
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
    } else if (strncmp(cmd, "STRM", 4) == 0) {
        cmd_stream(&cmd[4]);
    } else if (strncmp(cmd, "BCAP", 4) == 0) {
        cmd_burst(&cmd[4]);
    } else if (strncmp(cmd, "BRDO", 4) == 0) {
        cmd_burst_readout(&cmd[4]);
    } else if (strncmp(cmd, "DMAC", 4) == 0) {
        char *p = &cmd[4];
        u32 frames = ADC_CAPTURE_FRAMES;
        if (stream_active) {
            send_str("ERR DMAC unavailable while STRM is running; STRM STOP first\r\n");
            return;
        }
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
#if HAS_BRAM_DATAPLANE
    /* Keep the firmware shadow image consistent with the bank's RTL defaults
     * (regular-spiking) so the first partial NEUR update writes a coherent
     * full image into the config bank. */
    neuron_image_init();
    dac_bram_init_default();      /* boot default DAC BRAMs = 10 MHz sine */
#endif
    spike_shape_init_default();   /* boot default spike-pulse shape = original 7 ns trapezoid */
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
    u32 loop_count = 0;
    while (1) {
#if HAS_PS_DDR_DMA
        /* Publish the write pointer every iteration so the A53 tracks the DMA
         * smoothly (was every 1024 iters ~= 50 ms, which made the reader see
         * the writer in coarse ~1.5 MB jumps and stutter). stream_publish()
         * early-returns when not streaming, so this is cheap otherwise. */
        ++loop_count;
        stream_publish();
#else
        (void)loop_count;
        ++loop_count;
#endif
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
