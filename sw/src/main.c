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
    defined(XPAR_ADC1_CAPTURE_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_NEURON_CFG_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_CUR_WAVE_BRAM_CTRL_S_AXI_BASEADDR) && \
    defined(XPAR_SPIKE_SHAPE_BRAM_CTRL_S_AXI_BASEADDR)
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
#define RW3_CAPTURE_TRIG_MODE  (1u << 7)  /* 1 = arm ADC capture on current-injection start */
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
#define DDS_PHASE_INC_REG  (REG_BASE + 19u*4u) /* reg19[23:0], 0 = HDL default */
#define CUR_DAC_GAIN_REG   (REG_BASE + 20u*4u) /* reg20[15:0], Q8.8 DAC-only current gain */
#define CUR_DAC_GAIN_ONE   0x0100u
#define XSRC_OFF     0u
#define XSRC_DDS     1u    /* broadcast sine (single entry, any DAC) */
#define XSRC_BRAM0   2u    /* +ch : BRAM channel 0..3   */
#define XSRC_SPIKE0  6u    /* +ch : neuron spike pulse 0..3 */
#define XSRC_MON0    10u   /* +ch : neuron current monitor 0..3 */
#define XSRC_TAG     14u   /* debug tag word */
#define XSRC_CUR     15u   /* pure injected current source */
#define XSRC_NIBBLE(ch)  (((u32)(ch) & 3u) * 4u)

/* Programmable current player: control register (reg16) + waveform BRAM.  The
 * player advances the cur_wave read pointer every cycles_per_sample clk_50
 * cycles, loops 0..last_index unless hold_last is set, and feeds the held
 * Q16.16 sample as i_external into every neuron (and into the per-neuron
 * current monitors).  Effective loop frequency =
 * 50 MHz / (cycles_per_sample * (last_index + 1)). */
#define CUR_PLAYER_CTRL_REG  (REG_BASE + 0x40)   /* reg16 */
#define CUR_PLAYER_CPS_SHIFT   0u                /* [15:0]  cycles_per_sample */
#define CUR_PLAYER_LAST_SHIFT  16u               /* [25:16] last_index        */
#define CUR_PLAYER_HOLD_LAST   (1u << 26)        /* one-shot: hold last_index */
#define CUR_PLAYER_RUN         (1u << 30)
#define CUR_PLAYER_RESTART     (1u << 31)        /* toggle to reset to sample 0 */
#define CUR_WAVE_BRAM_BASE     XPAR_CUR_WAVE_BRAM_CTRL_S_AXI_BASEADDR /* 1024 x Q16.16 */
#define CUR_WAVE_DEPTH         1024u

/* Programmable spike-pulse shape: samples live in a BRAM loaded by PULS.  The
 * BRAM stores signed s16 DAC samples packed two per u32.  Reg 18 holds the
 * pulse length in 64-bit DAC beat-words (1 beat = 4 samples).  The HDL
 * replicates the RAM four times behind one AXI write port so each neuron pulse
 * source can read an independent address while sharing the same programmed
 * shape. */
#define SPIKE_NBEATS_REG       (REG_BASE + 18u*4u)   /* reg 18[10:0]: pulse beat count */
#if HAS_BRAM_DATAPLANE
#define SPIKE_SHAPE_BRAM_BASE  XPAR_SPIKE_SHAPE_BRAM_CTRL_S_AXI_BASEADDR
#define SPIKE_MAX_SAMPLES      4096u
#define SPIKE_MAX_BEATS        (SPIKE_MAX_SAMPLES / 4u)
#define SPIKE_SHAPE_WORDS      (SPIKE_MAX_SAMPLES / 2u)
#else
#define SPIKE_MAX_SAMPLES      0u
#define SPIKE_MAX_BEATS        0u
#endif

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
/* One-shot (non-cyclic) S2MM tail-descriptor slack. The PL burst engine ends
 * the packet with tlast after exactly `bytes`. If the descriptor buffer is
 * sized EXACTLY to the packet, buffer-full and tlast land on the same final
 * beat; on the tail descriptor (no successor) that tie resolves
 * nondeterministically and leaves S2MM stuck non-idle (no error) on a per-chip
 * coin flip. Sizing the buffer a few beats larger makes tlast (RXEOF) always
 * terminate the transfer first; the DMA stops at tlast so the slack is never
 * written. (Cyclic streaming never hits this -- every chunk descriptor has a
 * successor in the ring.) */
#define DMA_S2MM_LEN_SLACK   0x1000u     /* tail headroom (>= DMA max burst): lets the
                                          * final tlast/RXEOF flush fully and beat the
                                          * buffer-full race; never written to DDR */
#define BURST_CHUNK_BYTES    0x10000u    /* 64 KB = FIFO_DEPTH*16 = one FIFO-resident
                                          * chunk. Captures larger than this drain
                                          * continuously and MUST span a chain of
                                          * descriptors (like the cyclic stream ring);
                                          * a lone self-sized descriptor strands S2MM. */

/* Continuous decimated streaming: the PL decimator (RW6) feeds each DMA a
 * tlast-delimited 128 KB chunk stream; a cyclic SG descriptor ring makes the
 * DMA write those chunks into a DDR ring forever with no re-arm gaps. The
 * A53 drains the ring over UDP, reading the write pointer published in the
 * mailbox below. Descriptors and the mailbox live at 0x10030000..0x1003FFFF.
 * Keep data buffers below 0x20000000: the current HP0/HP1 DMA address map is
 * DDR_LOW-only, and live testing shows higher windows can leave S2MM non-idle. */
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
 * Both DMAs capture in parallel into separate low-DDR regions, sample-
 * aligned (one trigger fires both, ADCs SYSREF-synced). RW6 carries the beat
 * count per chip (16 B/beat). The A53 reads the regions out over UDP on MB
 * request via the burst mailbox. */
#define BURST_DDR_BASE0   0x18000000u    /* chip0 capture region               */
#define BURST_DDR_BASE1   0x19000000u    /* chip1 capture region               */
/* One extra ring chunk captured past the read-out window so the last WANTED
 * chunk completes on buffer-full (flushed to DDR) instead of on the burst's
 * final tlast (which strands the chunk's tail in the DMA -> stale capture
 * tail). Captured but never read out. */
#define BURST_FLUSH_GUARD BURST_CHUNK_BYTES
/* Read-out ceiling per chip. The captured size is bytes + BURST_FLUSH_GUARD, so
 * keep the ceiling a guard below the 16 MB chip0->chip1 gap. */
#define BURST_MAX_BYTES   0x00FE0000u    /* ~15.9 MB/chip + 64 KB guard < 16 MB */
/* Per-chip DDR span available to burst captures: the two BCAP regions are laid
 * out this far apart, and BCPT packs its repetitions (rep stride = bytes +
 * BURST_FLUSH_GUARD) into one region up to this limit. */
#define BURST_REGION_SPAN 0x01000000u    /* 16 MB/chip */
#define BURST_MAGIC       0x42435054u    /* mailbox magic: burst armed        */
/* burst mailbox layout (at STRM_MAILBOX): 00=magic 04=bytes/chip 08=base0
 * 0C=base1 10=readout_req(MB++) 14=readout_done(A53 echo) 18=beats */

static const u32 strm_desc_base[ADC_DMA_CHIPS] = { 0x10030000u, 0x10034000u };
static const u32 strm_ring_base[ADC_DMA_CHIPS] = { 0x1A080000u, 0x1C080000u };
static u32 burst_ddr_base[ADC_DMA_CHIPS] = { BURST_DDR_BASE0, BURST_DDR_BASE1 };

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

static void stream_stop(void);
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

static u16 recv_le16_blocking(void)
{
    u16 value = 0;

    value |= (u16)recv_byte_blocking();
    value |= (u16)recv_byte_blocking() << 8;
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

static u32 regf_addr(unsigned int idx)
{
    return REG_BASE + (idx * 4u);
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

static void print_reg_idx(const char *name, unsigned int idx, u32 val)
{
    send_str(name);
    send_uint(idx);
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
    } else if (token_eq_ci(p, "cur") || token_eq_ci(p, "current") ||
               token_eq_ci(p, "current_source") ||
               token_eq_ci(p, "inject") || token_eq_ci(p, "injection")) {
        *code = XSRC_CUR;
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
 * / current monitor 0-3 / current source / tag -- routes to any DAC, independent
 * of the neuron config bank.  program_enable (rw_reg3[6]) gates the BRAM read
 * pipeline, so we keep it on whenever any DAC routes a BRAM source. */
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
                     "off|dds|bram[0-3]|spike[0-3]|mon[0-3]|current|tag|0..15\r\n");
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

static void cmd_ddsi(void)
{
    char *p = &cmd[4];
    u32 inc;

    while (*p == ' ' || *p == '\t')
        p++;

    if (token_eq_ci(p, "default") || token_eq_ci(p, "auto")) {
        inc = 0u;
    } else if (!parse_u32_arg(&p, &inc) || inc > 0x00FFFFFFu) {
        send_str("ERR DDSI expects default|0..0xFFFFFF (phase increment; 0 = HDL default)\r\n");
        return;
    }

    Xil_Out32(DDS_PHASE_INC_REG, inc & 0x00FFFFFFu);
    send_str("DDS inc=");
    send_hex(Xil_In32(DDS_PHASE_INC_REG) & 0x00FFFFFFu);
    send_str("\r\n");
}

static void cmd_curg(void)
{
    char *p = &cmd[4];
    u32 gain;

    while (*p == ' ' || *p == '\t')
        p++;

    if (*p == '\0') {
        send_str("CURG gain_q8_8=");
        send_hex(Xil_In32(CUR_DAC_GAIN_REG) & 0xFFFFu);
        send_str(" (0x0100 = 1x, 0x1400 = 20x)\r\n");
        return;
    }

    if (token_eq_ci(p, "default") || token_eq_ci(p, "one") ||
        token_eq_ci(p, "unity")) {
        gain = CUR_DAC_GAIN_ONE;
    } else if (!parse_u32_arg(&p, &gain) || gain > 0xFFFFu) {
        send_str("ERR CURG expects default|0..0xFFFF Q8.8 gain (0x0100=1x, 0x1400=20x)\r\n");
        return;
    }

    Xil_Out32(CUR_DAC_GAIN_REG, gain & 0xFFFFu);
    send_str("OK CURG gain_q8_8=");
    send_hex(Xil_In32(CUR_DAC_GAIN_REG) & 0xFFFFu);
    send_str("\r\n");
}

#if HAS_BRAM_DATAPLANE
/* CURP off                       -> stop the current player (run=0)
 * CURP <cps> <last_index> <amp>  -> fill cur_wave[0..last_index] with a
 *   zero-mean Q16.16 triangle of amplitude +/-amp, then run the player at that
 *   rate.  The held sample becomes i_external on every neuron, so routing
 *   NSRC <ch> current mirrors the injected waveform out a DAC. */
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

/* CURW <cps> <count> [hold]  -> receive <count> little-endian Q16.16 samples
 *   over UART into cur_wave[0..count-1], set last_index = count-1, and run the
 *   player at <cps> cycles/sample.  Unlike CURP (an on-board triangle), this
 *   loads an ARBITRARY host-built waveform (sine / step / constant / ...).  The
 *   optional hold/once token plays 0..last_index once, then holds last_index
 *   instead of wrapping.  The held sample becomes i_external on every neuron, so
 *   routing NSRC <ch> current mirrors it to a DAC.  Effective loop frequency =
 *   50 MHz / (cps * count).  The BRAM holds full signed Q16.16; current sources
 *   are unipolar in use, so the host keeps samples >= 0. */
static void cmd_curw(void)
{
    char *p = &cmd[4];
    u32 cps, count, store, i;
    u32 hold_last = 0u;

    if (!parse_u32_arg(&p, &cps) || !parse_u32_arg(&p, &count)) {
        send_str("ERR CURW <cps> <count>, then <count> little-endian Q16.16 words\r\n");
        return;
    }
    if (count == 0u) {
        send_str("ERR CURW count must be >= 1\r\n");
        return;
    }
    /* Store at most CUR_WAVE_DEPTH samples, but ALWAYS receive every announced
     * word: the host transmits <count> words regardless, and any undrained
     * bytes would be parsed as UART commands (a stray CR/LF in sample data can
     * form a WRTE). */
    store = (count > CUR_WAVE_DEPTH) ? CUR_WAVE_DEPTH : count;

    while (*p == ' ' || *p == '\t')
        p++;
    if (*p != '\0') {
        if (token_eq_ci(p, "hold") || token_eq_ci(p, "once") ||
            token_eq_ci(p, "step")) {
            hold_last = 1u;
        } else if (token_eq_ci(p, "loop")) {
            hold_last = 0u;
        } else {
            send_str("ERR CURW mode must be hold/once/step or loop\r\n");
            return;
        }
    }

    send_str("CWRD count=");
    send_uint(count);
    send_str("\r\n");

    for (i = 0u; i < count; i++) {
        u32 w = recv_le32_blocking();
        if (i < store)
            Xil_Out32(CUR_WAVE_BRAM_BASE + i * 4u, w);
    }

    cur_player_restart_tog ^= 1u;
    Xil_Out32(CUR_PLAYER_CTRL_REG,
              ((cps & 0xFFFFu) << CUR_PLAYER_CPS_SHIFT) |
              (((store - 1u) & 0x3FFu) << CUR_PLAYER_LAST_SHIFT) |
              (hold_last ? CUR_PLAYER_HOLD_LAST : 0u) |
              CUR_PLAYER_RUN |
              (cur_player_restart_tog ? CUR_PLAYER_RESTART : 0u));

    send_str("OK CURW cps=");
    send_uint(cps);
    send_str(" count=");
    send_uint(store);
    send_str(" hold=");
    send_uint(hold_last);
    send_str("\r\n");
}

/* CURS <cps> <zero_count> <high_count> <amp_q16> [hold|loop]
 * Convenience wrapper for a programmable step using the existing cur_wave BRAM:
 * write zero_count zero samples, then high_count amp samples, then run the
 * player.  Default is hold-last, so it makes a single 0 -> amp step and stays
 * high.  "loop" repeats the programmed pattern. */
static void cmd_curs(void)
{
    char *p = &cmd[4];
    u32 cps, zero_count, high_count, amp, count, i;
    u32 hold_last = 1u;

    if (!parse_u32_arg(&p, &cps) || !parse_u32_arg(&p, &zero_count) ||
        !parse_u32_arg(&p, &high_count) || !parse_u32_arg(&p, &amp)) {
        send_str("ERR CURS <cps> <zero_count> <high_count> <amp_q16> [hold|loop]\r\n");
        return;
    }

    while (*p == ' ' || *p == '\t')
        p++;
    if (*p != '\0') {
        if (token_eq_ci(p, "hold") || token_eq_ci(p, "once") ||
            token_eq_ci(p, "step")) {
            hold_last = 1u;
        } else if (token_eq_ci(p, "loop")) {
            hold_last = 0u;
        } else {
            send_str("ERR CURS mode must be hold/once/step or loop\r\n");
            return;
        }
    }

    if (zero_count > CUR_WAVE_DEPTH || high_count > CUR_WAVE_DEPTH ||
        zero_count + high_count == 0u ||
        zero_count + high_count > CUR_WAVE_DEPTH) {
        send_str("ERR CURS zero_count + high_count must be 1..1024\r\n");
        return;
    }

    count = zero_count + high_count;
    for (i = 0u; i < zero_count; i++)
        Xil_Out32(CUR_WAVE_BRAM_BASE + i * 4u, 0u);
    for (; i < count; i++)
        Xil_Out32(CUR_WAVE_BRAM_BASE + i * 4u, amp);

    cur_player_restart_tog ^= 1u;
    Xil_Out32(CUR_PLAYER_CTRL_REG,
              ((cps & 0xFFFFu) << CUR_PLAYER_CPS_SHIFT) |
              (((count - 1u) & 0x3FFu) << CUR_PLAYER_LAST_SHIFT) |
              (hold_last ? CUR_PLAYER_HOLD_LAST : 0u) |
              CUR_PLAYER_RUN |
              (cur_player_restart_tog ? CUR_PLAYER_RESTART : 0u));

    send_str("OK CURS cps=");
    send_uint(cps);
    send_str(" zero=");
    send_uint(zero_count);
    send_str(" high=");
    send_uint(high_count);
    send_str(" hold=");
    send_uint(hold_last);
    send_str(" amp=");
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

#define SPIKE_TEXT_MAX_SAMPLES 32u

/* Write signed s16 samples as the spike-pulse shape: two samples per AXI word,
 * zero-pad the final 64-bit DAC beat, and set nbeats = ceil(count/4). */
static void spike_shape_write(const s16 *samples, u32 count)
{
#if HAS_BRAM_DATAPLANE
    u32 i, nb, total_words, word;

    if (count > SPIKE_MAX_SAMPLES)
        count = SPIKE_MAX_SAMPLES;
    nb = (count + 3u) >> 2;                 /* ceil(count/4) beats */
    if (nb == 0u) nb = 1u;
    if (nb > SPIKE_MAX_BEATS) nb = SPIKE_MAX_BEATS;
    total_words = nb * 2u;                  /* 2 AXI u32 words per 64-bit beat */

    for (i = 0; i < total_words; i++) {
        u32 sidx = i * 2u;
        word = 0u;
        if (sidx < count)
            word |= (u32)(u16)samples[sidx];
        if ((sidx + 1u) < count)
            word |= (u32)(u16)samples[sidx + 1u] << 16;
        Xil_Out32(SPIKE_SHAPE_BRAM_BASE + i * 4u, word);
    }

    Xil_Out32(SPIKE_NBEATS_REG, nb);
#else
    (void)samples;
    (void)count;
#endif
}

static u32 spike_shape_recv_binary(u32 rx_count)
{
#if HAS_BRAM_DATAPLANE
    u32 store_count = rx_count;
    u32 nb, total_words, i, word_index, word = 0u;

    if (store_count > SPIKE_MAX_SAMPLES)
        store_count = SPIKE_MAX_SAMPLES;

    nb = (store_count + 3u) >> 2;
    if (nb == 0u) nb = 1u;
    if (nb > SPIKE_MAX_BEATS) nb = SPIKE_MAX_BEATS;
    total_words = nb * 2u;

    for (i = 0; i < rx_count; i++) {
        u16 sample = recv_le16_blocking();
        if (i < store_count) {
            if (i & 1u) {
                word |= (u32)sample << 16;
                Xil_Out32(SPIKE_SHAPE_BRAM_BASE + (i >> 1) * 4u, word);
                word = 0u;
            } else {
                word = (u32)sample;
            }
        }
    }
    if (store_count & 1u)
        Xil_Out32(SPIKE_SHAPE_BRAM_BASE + (store_count >> 1) * 4u, word);

    for (word_index = (store_count + 1u) >> 1; word_index < total_words; word_index++)
        Xil_Out32(SPIKE_SHAPE_BRAM_BASE + word_index * 4u, 0u);

    Xil_Out32(SPIKE_NBEATS_REG, nb);
    return store_count;
#else
    u32 i;
    for (i = 0; i < rx_count; i++)
        (void)recv_le16_blocking();
    return 0u;
#endif
}

static void spike_shape_init_default(void)
{
    /* Default: INVERTED trapezoid -- 30 ns flat top at negative full-scale
     * (0 -> -32767) with 5-sample ramps, 40 samples total at 1 GS/s.  Ramp
     * points use the same (i+1)/(ramp+2) spacing as the host's trapezoid
     * builder so the two stay in step. */
    s16 shape[40];
    int i;

    for (i = 0; i < 5; i++) {
        s16 v = (s16)(-(32767 * (i + 1)) / 7);

        shape[i] = v;
        shape[39 - i] = v;
    }
    for (i = 5; i < 35; i++) {
        shape[i] = (s16)-32767;
    }
    spike_shape_write(shape, 40u);
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

/* PULS default                  -> reload the default (inverted 30 ns trapezoid)
 * PULS <s0> <s1> ... <sN>        -> set a short pulse from text (<=32 samples)
 * PULS bin <count> + LE s16 data -> set a long pulse (<=4096 samples)
 *   Samples are full-range signed s16; nbeats auto = ceil(N/4). */
static void cmd_puls(void)
{
    char *p = &cmd[4];
    s16 samples[SPIKE_TEXT_MAX_SAMPLES];
    u32 count = 0u;
    s32 v;

    while (*p == ' ' || *p == '\t')
        p++;
    if (token_eq_ci(p, "default")) {
        spike_shape_init_default();
        send_str("PULS default (inverted 30 ns trapezoid, 40 samples)\r\n");
        return;
    }
    if (token_eq_ci(p, "bin") || token_eq_ci(p, "binary")) {
        u32 rx_count, stored;
        advance_token(&p);
        if (!parse_u32_arg(&p, &rx_count) || rx_count == 0u) {
            send_str("ERR PULS bin <count>, then <count> little-endian s16 samples\r\n");
            return;
        }
        send_str("PBRD count=");
        send_uint(rx_count);
        send_str("\r\n");
        stored = spike_shape_recv_binary(rx_count);
        send_str("PULS loaded ");
        send_uint(stored);
        send_str(" samples, nbeats=");
        send_uint((stored + 3u) >> 2);
        if (stored < rx_count)
            send_str(" (truncated)");
        send_str("\r\n");
        return;
    }
    while (count < SPIKE_TEXT_MAX_SAMPLES && parse_s32_arg(&p, &v))
        samples[count++] = (s16)v;
    if (count == 0u) {
        send_str("ERR PULS [default | bin <count> | s0 s1 ... up to 32 signed samples]\r\n");
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
    u32 store;
    u32 i;

    if (channel >= DAC_PROGRAM_CHANNELS) {
        send_str("ERR PROG channel must be 0..3\r\n");
        return;
    }

    if (words == 0u) {
        words = DAC_PROGRAM_WORDS_PER_CHANNEL;
    }
    if ((words & 1u) != 0u) {
        send_str("ERR PROG word count must be even for 64-bit DAC frames\r\n");
        return;
    }
    /* Store at most a full channel BRAM, but ALWAYS receive every announced
     * word: the host transmits <words> words regardless, and any undrained
     * bytes would be parsed as UART commands. */
    store = (words > DAC_PROGRAM_WORDS_PER_CHANNEL)
                ? DAC_PROGRAM_WORDS_PER_CHANNEL : words;

    send_str("PGRD ch=");
    send_uint(channel);
    send_str(" words=");
    send_uint(words);
    send_str("\r\n");

    for (i = 0; i < words; i++) {
        u32 w = recv_le32_blocking();
        if (i < store) {
            Xil_Out32(dac_program_bram_base[channel] + i * 4u, w);
        }
    }

    send_str("OK PROG ch=");
    send_uint(channel);
    send_str(" words=");
    send_uint(store);
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

/* Emit the FE10CAFE sync then stream the captured BRAM (ADC0=ch0/ch1 then
 * ADC1=ch2/ch3, little-endian).  Shared by cmd_capture and cmd_capture_triggered. */
static void stream_capture_bram(u32 frames)
{
    u32 frame;
    u32 word;

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

static void cmd_capture(u32 frames, int use_dac_program)
{
    u32 status = 0;

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
    stream_capture_bram(frames);
}

/* Trigger-synchronized one-shot capture: arm the ADC capture, then restart the
 * current source to sample 0.  The player's sample-0 (cycle_start) pulse fires
 * the armed capture in hardware, so every burst begins at the identical
 * injection phase -- ideal for averaging many identical bursts.  Requires the
 * current source to have been configured (CURS/CURP/CURW) first. */
static void cmd_capture_triggered(u32 frames)
{
    u32 status = 0;
    u32 ctrl;

    if (frames == 0u || frames > ADC_CAPTURE_FRAMES) {
        frames = ADC_CAPTURE_FRAMES;
    }

    /* Pause the player BEFORE arming: with a LOOPING waveform a free-running
     * wrap can fire the armed capture inside the arm delays (same race fixed
     * in cmd_burst_trig). With run=0 the synchronized restart below is the
     * only possible trigger. */
    ctrl = Xil_In32(CUR_PLAYER_CTRL_REG);
    Xil_Out32(CUR_PLAYER_CTRL_REG, ctrl & ~CUR_PLAYER_RUN);
    short_delay();                      /* let run=0 cross into clk_50 */

    /* select "arm on current-injection start" mode */
    Xil_Out32(RW_REG3, Xil_In32(RW_REG3) | RW3_CAPTURE_TRIG_MODE);
    short_delay();

    /* arm: the RW3[3] edge latches the armed flag (DAC loopback kept running) */
    trigger_capture(1);
    short_delay();

    /* restart the current source to sample 0 with run=1 in one write; its
     * cycle_start fires the armed capture at the exact injection-window start
     * (one-shot: replays 0..last once) */
    cur_player_restart_tog ^= 1u;
    ctrl = (ctrl & ~CUR_PLAYER_RESTART) | CUR_PLAYER_RUN |
           (cur_player_restart_tog ? CUR_PLAYER_RESTART : 0u);
    Xil_Out32(CUR_PLAYER_CTRL_REG, ctrl);

    /* The capture's done bit stays STALE from the previous capture until the
     * trigger actually fires, and sample 0 only lands ~(cps+2) clk_50 cycles
     * after the restart -- with a large cps that is up to ~1.3 ms, long after
     * wait_capture_done's first poll, which would return a torn early success.
     * Spin past the trigger moment first (each AXI read >> 20 ns). */
    {
        u32 cps = (ctrl >> CUR_PLAYER_CPS_SHIFT) & 0xFFFFu;
        u32 i;

        for (i = 0u; i < cps + 16u; i++) {
            (void)Xil_In32(RW_REG3);
        }
    }

    if (!wait_capture_done(&status)) {
        Xil_Out32(RW_REG3, Xil_In32(RW_REG3) & ~RW3_CAPTURE_TRIG_MODE);
        send_str("ERR capture timeout; ");
        send_hex(status);
        send_str("\r\n");
        return;
    }
    /* leave trigger mode so a plain PCAP behaves normally afterwards */
    Xil_Out32(RW_REG3, Xil_In32(RW_REG3) & ~RW3_CAPTURE_TRIG_MODE);
    stream_capture_bram(frames);
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

/* Arm S2MM for a one-shot burst of `bytes` into a contiguous DDR window.
 *
 * The capture is one continuous tlast-delimited packet. It is usually larger
 * than the 64 KB PL FIFO, so it must drain continuously while the ADC fills --
 * and a lone self-sized descriptor strands S2MM in that regime: when the final
 * beat makes buffer-full and tlast land on the same beat of the TAIL descriptor
 * (no successor), completion resolves nondeterministically and the channel
 * hangs (idle never sets, no error) on a per-chip coin flip.
 *
 * Fix: chain BURST_CHUNK_BYTES descriptors exactly like the proven cyclic
 * stream ring. Every leading 64 KB chunk completes on buffer-full and hands off
 * to its successor (the normal multi-descriptor packet-spanning path, lossless
 * in the stream ring), and a slightly oversized TAIL descriptor catches the
 * final tlast as RXEOF before it fills -- so the transfer always terminates on
 * end-of-packet, never on the tail-buffer-full tie. Reuses the stream
 * descriptor region (burst and streaming are mutually exclusive). */
static void dma_arm_s2mm(u32 chip, u32 dest_addr, u32 bytes)
{
    u32 base = adc_dma_base[chip];
    u32 desc_base = strm_desc_base[chip];
    u32 leading = (bytes > BURST_CHUNK_BYTES)
                      ? ((bytes - 1u) / BURST_CHUNK_BYTES) : 0u;
    u32 tail = desc_base + leading * STRM_DESC_STRIDE;
    /* Tail completion strategy (ILA-confirmed): on a continuous >64 KB drain the
     * S2MM holds tready=1 and completes the TAIL on BUFFER-FULL, not on tlast --
     * any slack leaves it waiting forever for bytes that never arrive. So size a
     * chain tail to the EXACT remaining bytes (fills -> buffer-full -> done; also
     * a deterministic backstop if the final tlast/EOF is intermittently missed).
     * A lone descriptor (<=64 KB) still needs slack: it completes via tlast/RXEOF
     * and an exact size hits the buffer-full/tlast tie. */
    u32 tail_len = bytes - leading * BURST_CHUNK_BYTES
                 + ((leading == 0u) ? DMA_S2MM_LEN_SLACK : 0u);
    u32 i;

    dma_reset(chip);
    for (i = 0u; i < leading; i++) {
        u32 desc = desc_base + i * STRM_DESC_STRIDE;
        dma_write_desc(desc, desc + STRM_DESC_STRIDE,
                       dest_addr + i * BURST_CHUNK_BYTES, BURST_CHUNK_BYTES);
    }
    /* tail descriptor: remainder + slack, self-linked (it is the tail, so the
     * engine stops here once it is processed). */
    dma_write_desc(tail, tail, dest_addr + leading * BURST_CHUNK_BYTES, tail_len);

    Xil_Out32(base + DMA_S2MM_DMASR, DMA_DMASR_IRQ_MASK | DMA_DMASR_ERR_MASK);
    Xil_Out32(base + DMA_S2MM_CURDESC, desc_base);
    Xil_Out32(base + DMA_S2MM_CURDESC_MSB, 0u);
    Xil_Out32(base + DMA_S2MM_DMACR, DMA_DMACR_RS);
    Xil_Out32(base + DMA_S2MM_TAILDESC, tail);
    Xil_Out32(base + DMA_S2MM_TAILDESC_MSB, 0u);
}

/* Arm S2MM as a CYCLIC ring sized to `bytes` (rounded up to BURST_CHUNK_BYTES
 * chunks), exactly like the proven streaming ring. Cyclic mode has NO tail
 * descriptor to strand, so a continuous >64 KB drain never deadlocks -- the
 * capture engine's single tlast is just a chunk boundary the ring rolls past.
 * The caller polls the capture engine's `done`, lets the DMA flush, then halts
 * the ring (dma_reset); the data is already in DDR. Reuses the stream
 * descriptor region (burst and streaming are mutually exclusive). */
static void dma_arm_cyclic_burst(u32 chip, u32 dest_addr, u32 bytes)
{
    u32 base = adc_dma_base[chip];
    u32 desc_base = strm_desc_base[chip];
    u32 nchunks = (bytes + BURST_CHUNK_BYTES - 1u) / BURST_CHUNK_BYTES;
    u32 i;

    if (nchunks < 2u) {
        nchunks = 2u;                       /* cyclic ring needs >=2 descriptors */
    }
    dma_reset(chip);
    for (i = 0u; i < nchunks; i++) {
        u32 desc = desc_base + i * STRM_DESC_STRIDE;
        u32 next = desc_base + ((i + 1u) % nchunks) * STRM_DESC_STRIDE;
        dma_write_desc(desc, next, dest_addr + i * BURST_CHUNK_BYTES,
                       BURST_CHUNK_BYTES);
    }
    Xil_Out32(base + DMA_S2MM_DMASR, DMA_DMASR_IRQ_MASK | DMA_DMASR_ERR_MASK);
    Xil_Out32(base + DMA_S2MM_CURDESC, desc_base);
    Xil_Out32(base + DMA_S2MM_CURDESC_MSB, 0u);
    Xil_Out32(base + DMA_S2MM_DMACR, DMA_DMACR_RS | DMA_DMACR_CYCLIC);
    Xil_Out32(base + DMA_S2MM_TAILDESC, 0x50u);   /* sentinel: ring never halts */
    Xil_Out32(base + DMA_S2MM_TAILDESC_MSB, 0u);
}

/* Poll both capture engines' status (ADCS selector 8) until done. The status
 * word is {8'hBC, done, running, overflow, ...}: magic[31:24]=0xBC, done=bit23,
 * overflow=bit21. */
static int wait_burst_done(u32 *s0, u32 *s1)
{
    u32 timeout;

    for (timeout = 0; timeout < 20000000u; timeout++) {
        *s0 = read_adc_debug(0u, 8u);
        *s1 = read_adc_debug(1u, 8u);
        if (((*s0 >> 24) == 0xBCu) && ((*s1 >> 24) == 0xBCu) &&
            (((*s0 >> 23) & 1u) != 0u) && (((*s1 >> 23) & 1u) != 0u)) {
            return 1;
        }
    }
    return 0;
}

#if HAS_BRAM_DATAPLANE
/* Poll both capture engines until a NEW capture has begun (done bit LOW).
 * Needed for trigger-synchronized (RW3[7]) captures: the engine's `done` only
 * clears at the actual trigger (the player's cycle_start), so a stale done=1
 * from the previous capture would otherwise satisfy wait_burst_done before
 * this capture even fires.  Race-safe: once started, done stays low for the
 * whole capture (>= tens of us), far longer than one poll iteration. */
static int wait_burst_started(u32 *s0, u32 *s1)
{
    u32 timeout;

    for (timeout = 0; timeout < 20000000u; timeout++) {
        *s0 = read_adc_debug(0u, 8u);
        *s1 = read_adc_debug(1u, 8u);
        if (((*s0 >> 24) == 0xBCu) && ((*s1 >> 24) == 0xBCu) &&
            (((*s0 >> 23) & 1u) == 0u) && (((*s1 >> 23) & 1u) == 0u)) {
            return 1;
        }
    }
    return 0;
}
#endif /* HAS_BRAM_DATAPLANE */

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

static void set_adc_capture_beats(u32 beats)
{
    Xil_Out32(RW_REG6, beats);
    /* RW6 crosses into the ADC beat clock with a plain vector CDC.  Let the new
     * count settle before pulsing RW3[3], otherwise a just-cleared zero can be
     * sampled and the burst engine will wait forever with no TLAST. */
    short_delay();
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

    set_adc_capture_beats(frames);          /* 16 B/frame = one 128-bit beat */
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

static int ranges_overlap(u32 a, u32 bytes_a, u32 b, u32 bytes_b)
{
    unsigned long long a0 = (unsigned long long)a;
    unsigned long long a1 = a0 + (unsigned long long)bytes_a;
    unsigned long long b0 = (unsigned long long)b;
    unsigned long long b1 = b0 + (unsigned long long)bytes_b;

    return (a0 < b1) && (b0 < a1);
}

static int burst_map_is_valid(u32 base0, u32 base1)
{
    /* BCAP actually WRITES bytes + BURST_FLUSH_GUARD per chip (the guard is
     * captured but never read out), so every check below must use the full
     * capture footprint, not just the read-out ceiling. */
    const u32 cap = BURST_MAX_BYTES + BURST_FLUSH_GUARD;
    /* The DMAC scratch buffers, SG descriptor rings, and the stream/burst
     * mailbox all live in 0x10000000..0x1003FFFF; a capture landing there
     * corrupts its own descriptor chain mid-transfer. */
    const u32 rsvd_base  = ADC_DMA0_DDR_BASE;
    const u32 rsvd_bytes = (STRM_MAILBOX + 0x100u) - ADC_DMA0_DDR_BASE;
    u32 i;

    if (((base0 | base1) & 0x0Fu) != 0u) {
        send_str("ERR BMAP bases must be 16-byte aligned\r\n");
        return 0;
    }
    if (base0 > (0x80000000u - cap) ||
        base1 > (0x80000000u - cap)) {
        send_str("ERR BMAP bases + capture + flush guard must fit in DDR_LOW below 0x80000000\r\n");
        return 0;
    }
    if (ranges_overlap(base0, cap, base1, cap)) {
        send_str("ERR BMAP chip windows overlap (incl flush guard)\r\n");
        return 0;
    }
    if (ranges_overlap(base0, cap, rsvd_base, rsvd_bytes) ||
        ranges_overlap(base1, cap, rsvd_base, rsvd_bytes)) {
        send_str("ERR BMAP overlaps DMA descriptor/mailbox region\r\n");
        return 0;
    }
    for (i = 0; i < ADC_DMA_CHIPS; i++) {
        if (ranges_overlap(base0, cap,
                           strm_ring_base[i], STRM_RING_BYTES) ||
            ranges_overlap(base1, cap,
                           strm_ring_base[i], STRM_RING_BYTES)) {
            send_str("ERR BMAP overlaps stream ring\r\n");
            return 0;
        }
    }
    return 1;
}

/* BMAP [base0 base1] -- debug/bring-up hook for DMA destination windows.
 * With no args, prints the active map used by BCAP/BRDO. */
static void cmd_burst_map(char *args)
{
    char *p = args;
    u32 base0;
    u32 base1;

    if (parse_u32_arg(&p, &base0)) {
        if (!parse_u32_arg(&p, &base1)) {
            send_str("ERR BMAP expects base0 base1, or no args\r\n");
            return;
        }
        if (!burst_map_is_valid(base0, base1)) {
            return;
        }
        burst_ddr_base[0] = base0;
        burst_ddr_base[1] = base1;
    }

    send_str("BMAP base0=");
    send_hex(burst_ddr_base[0]);
    send_str(" base1=");
    send_hex(burst_ddr_base[1]);
    send_str(" max_bytes=");
    send_uint(BURST_MAX_BYTES);
    send_str(" stream0=");
    send_hex(strm_ring_base[0]);
    send_str(" stream1=");
    send_hex(strm_ring_base[1]);
    send_str("\r\n");
}

/* BCAP [N[k|m]] -- full-rate, un-decimated, sample-aligned burst capture of all
 * 4 ADC channels into two low-DDR regions. N defaults to MB ('m' optional,
 * back-compatible with BCAP <MB>); a 'k' suffix means KB for small grabs
 * (e.g. BCAP 64k = 64 KB/chip = 16384 samples/ch). Default 16 MB/chip. */
static void cmd_burst(char *args)
{
    char *p = args;
    u32 cnt = BURST_MAX_BYTES >> 20;
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

    if (stream_active) {
        stream_stop();                       /* burst capture owns both DMAs */
    }

    /* Always use the proven CYCLIC ring (no tail descriptor to strand S2MM).
     * Capture BURST_FLUSH_GUARD extra bytes beyond the read-out window so the
     * last WANTED chunk completes on buffer-full (flushed) rather than on the
     * burst's final tlast -- a tlast-terminated last chunk leaves its tail in
     * the DMA unflushed and dma_reset discards it, leaving a STALE capture tail
     * (e.g. old data persisting in the later half). The guard is captured but
     * never read out. */
    u32 cap_bytes = bytes + BURST_FLUSH_GUARD;
    u32 cyclic = 1u;
    set_adc_capture_beats(cap_bytes >> 4);  /* wanted + guard beats */
    dma_arm_cyclic_burst(0u, burst_ddr_base[0], cap_bytes);
    dma_arm_cyclic_burst(1u, burst_ddr_base[1], cap_bytes);

    /* publish region info for the A53 readout. readout_req (0x10) stays
     * monotonic across captures -- the A53 drains on any change -- so we do not
     * reset it here. */
    Xil_Out32(STRM_MAILBOX + 0x04u, bytes);
    Xil_Out32(STRM_MAILBOX + 0x08u, burst_ddr_base[0]);
    Xil_Out32(STRM_MAILBOX + 0x0Cu, burst_ddr_base[1]);
    Xil_Out32(STRM_MAILBOX + 0x18u, beats);
    Xil_Out32(STRM_MAILBOX + 0x00u, BURST_MAGIC);

    pulse_adc_capture();                    /* fire both chips together */

    if (cyclic) {
        u32 d;
        u32 ok = wait_burst_done(&s0, &s1) ? 1u : 0u;
        /* let each DMA flush its last in-flight burst into DDR, then halt the
         * cyclic ring (the data is already written). */
        for (d = 0u; d < 8000u; d++) {
            (void)dma_status(0u);
        }
        dma_reset(0u);
        dma_reset(1u);
        if (!ok) {
            send_str("ERR BCAP timeout (engine) ");
            print_named_hex("st0", s0);
            send_str(" ");
            print_named_hex("st1", s1);
            send_str("\r\n");
            return;
        }
        if ((((s0 >> 21) | (s1 >> 21)) & 1u) != 0u) {
            send_str("WARN BCAP overflow -- capture lossy ");
            print_named_hex("st0", s0);
            send_str(" ");
            print_named_hex("st1", s1);
            send_str("\r\n");
        }
    } else if (!wait_dma_done(&s0, &s1)) {
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
    send_hex(burst_ddr_base[0]);
    send_str(" base1=");
    send_hex(burst_ddr_base[1]);
    send_str(" ");
    print_named_hex("dma0", s0);
    send_str(" ");
    print_named_hex("dma1", s1);
    send_str("\r\n");
}

#if HAS_BRAM_DATAPLANE
/* BCPT <N[k|m]> [reps] -- multisample trigger-synchronized burst capture.
 *
 * Repeats `reps` full-rate DMA captures of N bytes/chip, each fired in
 * HARDWARE by the current player's sample-0 pulse (RW3[7] arm mode + the
 * player restart, exactly like PCAPT) so every repetition starts at the
 * identical current-injection phase.  Repetitions land back-to-back in the
 * BCAP DDR regions at a fixed stride of (N + BURST_FLUSH_GUARD) bytes and are
 * drained by ONE BRDO -- no host round-trips between repetitions, so the
 * inter-rep dead time is only the DMA re-arm (~10s of us).
 *
 * Requires the current source to be configured and running first
 * (CURS/CURP/CURW); the host slices each rep out of the readout at the
 * reported stride and averages. */
static void cmd_burst_trig(char *args)
{
    char *p = args;
    u32 cnt = 64u;                          /* default 64 KB/chip per rep */
    u32 unit = 0x400u;                      /* default unit: KB */
    u32 reps = 16u;
    u32 bytes, stride, total, r;
    u32 s0 = 0, s1 = 0;
    u32 overflow = 0;

    while (*p == ' ') {
        p++;
    }
    if (parse_u32_arg(&p, &cnt)) {
        if (*p == 'k' || *p == 'K') {
            unit = 0x400u;
            p++;
        } else if (*p == 'm' || *p == 'M') {
            unit = 0x100000u;
            p++;
        }
        parse_u32_arg(&p, &reps);
    }
    if (cnt == 0u) {
        cnt = 64u;
        unit = 0x400u;
    }
    if (reps == 0u) {
        reps = 1u;
    }
    bytes = cnt * unit;
    if (bytes > BURST_MAX_BYTES) {
        bytes = BURST_MAX_BYTES;
    }
    bytes &= ~0x0Fu;                        /* whole 16 B beats */
    if (bytes == 0u) {
        bytes = 0x10u;
    }
    stride = bytes + BURST_FLUSH_GUARD;     /* guard is captured, never read */
    /* all reps (incl. the last rep's guard) must fit one chip's DDR region */
    {
        u32 reps_max = BURST_REGION_SPAN / stride;

        if (reps_max == 0u) {
            send_str("ERR BCPT rep size too large for DDR region\r\n");
            return;
        }
        if (reps > reps_max) {
            reps = reps_max;
        }
    }
    total = reps * stride;
    burst_bytes = total;

    if (stream_active) {
        stream_stop();                      /* burst capture owns both DMAs */
    }

    if ((Xil_In32(CUR_PLAYER_CTRL_REG) & CUR_PLAYER_RUN) == 0u) {
        send_str("ERR BCPT current player not running; CURS/CURP/CURW first\r\n");
        return;
    }

    set_adc_capture_beats(stride >> 4);     /* wanted + guard beats per rep */

    /* arm-on-injection mode: the RW3[3] pulse only ARMS; the player's
     * cycle_start (CDC'd into the ADC clock) fires the capture. */
    Xil_Out32(RW_REG3, Xil_In32(RW_REG3) | RW3_CAPTURE_TRIG_MODE);
    short_delay();

    for (r = 0u; r < reps; r++) {
        u32 ctrl;
        u32 ok;

        dma_arm_cyclic_burst(0u, burst_ddr_base[0] + r * stride, stride);
        dma_arm_cyclic_burst(1u, burst_ddr_base[1] + r * stride, stride);

        /* Pause the player BEFORE arming: with a LOOPING waveform a free-
         * running wrap right after the arm can fire (and, for short windows,
         * COMPLETE) the capture inside the arm/restart delays -- the done-bit
         * handshake below then misses the whole rep and times out. With run=0
         * no cycle_start can occur until the restart below, so the trigger is
         * always the synchronized injection-window start. */
        ctrl = Xil_In32(CUR_PLAYER_CTRL_REG);
        Xil_Out32(CUR_PLAYER_CTRL_REG, ctrl & ~CUR_PLAYER_RUN);
        short_delay();                      /* let run=0 cross into clk_50 */

        pulse_adc_capture();                /* RW3[3] edge: arm (keep DAC bits) */
        short_delay();

        /* restart the player to sample 0 with run=1 in one write; its
         * cycle_start fires this rep at the exact injection-window start
         * (also replays one-shot waveforms) */
        cur_player_restart_tog ^= 1u;
        ctrl = (ctrl & ~CUR_PLAYER_RESTART) | CUR_PLAYER_RUN |
               (cur_player_restart_tog ? CUR_PLAYER_RESTART : 0u);
        Xil_Out32(CUR_PLAYER_CTRL_REG, ctrl);

        /* the engines' done bits are stale from the previous rep until the
         * player's cycle_start actually fires this capture -- wait for the
         * capture to BEGIN before waiting for it to finish. */
        if (!wait_burst_started(&s0, &s1)) {
            Xil_Out32(RW_REG3, Xil_In32(RW_REG3) & ~RW3_CAPTURE_TRIG_MODE);
            dma_reset(0u);
            dma_reset(1u);
            send_str("ERR BCPT no trigger (player looping/one-shot replay?) rep=");
            send_uint(r);
            send_str(" ");
            print_named_hex("st0", s0);
            send_str(" ");
            print_named_hex("st1", s1);
            send_str("\r\n");
            return;
        }

        ok = wait_burst_done(&s0, &s1) ? 1u : 0u;
        /* let each DMA flush its last in-flight burst into DDR, then halt the
         * cyclic ring (the data is already written). */
        {
            u32 d;
            for (d = 0u; d < 8000u; d++) {
                (void)dma_status(0u);
            }
        }
        dma_reset(0u);
        dma_reset(1u);
        if (!ok) {
            Xil_Out32(RW_REG3, Xil_In32(RW_REG3) & ~RW3_CAPTURE_TRIG_MODE);
            send_str("ERR BCPT timeout (engine) rep=");
            send_uint(r);
            send_str(" ");
            print_named_hex("st0", s0);
            send_str(" ");
            print_named_hex("st1", s1);
            send_str("\r\n");
            return;
        }
        overflow |= ((s0 >> 21) | (s1 >> 21)) & 1u;
    }

    /* leave trigger mode so a plain BCAP/PCAP behaves normally afterwards */
    Xil_Out32(RW_REG3, Xil_In32(RW_REG3) & ~RW3_CAPTURE_TRIG_MODE);

    /* publish the WHOLE strided region for one A53 UDP drain; the host slices
     * `bytes` out of every `stride` and discards each rep's guard tail. */
    Xil_Out32(STRM_MAILBOX + 0x04u, total);
    Xil_Out32(STRM_MAILBOX + 0x08u, burst_ddr_base[0]);
    Xil_Out32(STRM_MAILBOX + 0x0Cu, burst_ddr_base[1]);
    Xil_Out32(STRM_MAILBOX + 0x18u, total >> 4);
    Xil_Out32(STRM_MAILBOX + 0x00u, BURST_MAGIC);

    if (overflow) {
        send_str("WARN BCPT overflow -- capture lossy\r\n");
    }
    send_str("OK BCPT reps=");
    send_uint(reps);
    send_str(" bytes_per_rep=");
    send_uint(bytes);
    send_str(" stride=");
    send_uint(stride);
    send_str(" total_per_chip=");
    send_uint(total);
    send_str(" base0=");
    send_hex(burst_ddr_base[0]);
    send_str(" base1=");
    send_hex(burst_ddr_base[1]);
    send_str("\r\n");
}
#endif /* HAS_BRAM_DATAPLANE */

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
    send_str("  NSRC [ch|all] src  DAC crossbar (reg17): off,dds,bram[0-3],spike[0-3],mon[0-3],current,tag,0..15\r\n");
    send_str("  DDSI default|step  DDS phase increment reg19[23:0]; 0/default uses HDL 0x19999A\r\n");
    send_str("  CURG [default|gain_q8_8]  pure current DAC-view gain only; 0x0100=1x, 0x1400=20x\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("  CURP off | <cps> <last> <amp_q16>  current player: triangle into i_external; f=50MHz/(cps*(last+1))\r\n");
    send_str("  CURW <cps> <count> [hold]  current player: load host LE Q16.16 samples; optional hold plays once then holds last\r\n");
    send_str("  CURS <cps> <zero> <high> <amp_q16> [hold|loop]  current step via cur_wave BRAM/player\r\n");
    send_str("  PULS default | bin <count> | <s0..sN>  spike pulse: binary up to 4096 signed s16 samples; text up to 32\r\n");
#endif
    send_str("  NEUR ch param value  set IZH Q16.16 param on ch=0..3 or all (writes config-bank BRAM)\r\n");
    send_str("                 params: a,b,c,d,i/current,iconst/bias (per-neuron); dt,period (global); reset,default\r\n");
    send_str("  NEUR [ch|all] profile name  profiles: regular/rs, bursting/ib, chattering/ch, fast/fs, lts, tc, resonator/rz, rebound/rb\r\n");
    send_str("  NEUR profiles       list built-in neuron profiles\r\n");
    send_str("  RDRO n           read RO register 0..7\r\n");
    send_str("  RDRW n           read register-file index 0..47\r\n");
    send_str("  WRTE n value     write register-file index 0..47; use 0x prefix for hex masks\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("  PROG ch [n]      upload even n little-endian u32 words to DAC channel ch=0..3\r\n");
    send_str("  DPWR ch start n  write n little-endian u32 words to DAC BRAM 32-bit word addresses\r\n");
    send_str("  DPRD ch [start] [n] read back DAC program BRAM u32 words\r\n");
    send_str("  CAPS             print ADC BRAM capture status\r\n");
    send_str("  CAPT [frames]    capture 256-bit adc_ch0..3 frames; stream 8 u32 words/frame\r\n");
    send_str("  PCAP [frames]    restart DAC BRAM program, then capture ADC frames\r\n");
    send_str("  PCAPT [frames]   arm+restart current source; capture synced to injection start\r\n");
#else
    send_str("  PROG/CAPS/CAPT/PCAP unavailable; rebuild with --with-bram-dataplane\r\n");
#endif
#if HAS_PS_DDR_DMA
    send_str("  DMAC [frames]    arm ADC0/ADC1 S2MM DMA to PS DDR, then pulse ADC capture\r\n");
    send_str("  BCAP [MB]        full-rate un-decimated burst capture of all 4 ADC ch (default/max 16 MB/chip)\r\n");
    send_str("  BCPT [N[k|m] [reps]] multisample burst: reps trigger-synced captures of N/chip, strided in DDR (default 64k x16)\r\n");
    send_str("  BRDO             ask the A53 to read the last BCAP regions out over UDP\r\n");
    send_str("  BMAP [b0 b1]     show/set BCAP DDR bases (debug)\r\n");
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
    send_str("REG17 DAC crossbar: 4 bits/DAC: 0=off,1=DDS,2-5=BRAM0-3,6-9=spike0-3,10-13=mon0-3,14=tag,15=current (NSRC)\r\n");
    send_str("    IZH debug via RW1=7: conv_sel 5=ch0 dt, 6=ch0 last spike interval, 7=ch0 update period\r\n");
#if HAS_BRAM_DATAPLANE
    send_str("    [3] ADC BRAM capture/DAC program restart pulse\r\n");
    send_str("    [6] DAC program BRAM mode enable; PCAP sets this, CAPT clears it\r\n");
    send_str("    [31:8] DAC BRAM loop frame count; 0 loops full 4096-frame BRAM\r\n");
#endif
    send_str("REG19 DDS phase increment: [23:0], 0 uses hardware default 0x19999A\r\n");
    send_str("REG20 current DAC gain: [15:0] Q8.8, applies only to source=current DAC mirror, not neuron input\r\n");
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
    send_str(" dds_inc=");
    send_hex(Xil_In32(DDS_PHASE_INC_REG) & 0x00FFFFFFu);
    send_str(" cur_gain=");
    send_hex(Xil_In32(CUR_DAC_GAIN_REG) & 0xFFFFu);
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
    Xil_Out32(DDS_PHASE_INC_REG, 0u);
    Xil_Out32(CUR_DAC_GAIN_REG, CUR_DAC_GAIN_ONE);
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
    } else if (strncmp(cmd, "DDSI", 4) == 0) {
        cmd_ddsi();
    } else if (strncmp(cmd, "CURG", 4) == 0) {
        cmd_curg();
#if HAS_BRAM_DATAPLANE
    } else if (strncmp(cmd, "CURP", 4) == 0) {
        cmd_curp();
    } else if (strncmp(cmd, "CURW", 4) == 0) {
        cmd_curw();
    } else if (strncmp(cmd, "CURS", 4) == 0) {
        cmd_curs();
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
        if (!parse_u32_arg(&p, &idx) || idx >= 48u) {
            send_str("ERR RDRW expects register-file index 0..47\r\n");
            return;
        }
        if (idx < 8u)
            print_reg("RW", idx, Xil_In32(rw_addr(idx)));
        else
            print_reg_idx("REG", idx, Xil_In32(regf_addr(idx)));
    } else if (strncmp(cmd, "WRTE", 4) == 0) {
        char *p = &cmd[5];
        u32 idx;
        u32 val;
        if (!parse_u32_arg(&p, &idx) || idx >= 48u) {
            send_str("ERR WRTE expects register-file index 0..47 and value\r\n");
            return;
        }
        if (!parse_u32_arg(&p, &val)) {
            send_str("ERR WRTE expects register-file index 0..47 and value\r\n");
            return;
        }
        Xil_Out32(regf_addr(idx), val);
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
    } else if (strncmp(cmd, "PCAPT", 5) == 0) {
        /* trigger-synchronized one-shot capture (arm + restart current source) */
        char *p = &cmd[5];
        u32 frames = ADC_CAPTURE_FRAMES;
        parse_u32_arg(&p, &frames);
        cmd_capture_triggered(frames);
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
#if HAS_BRAM_DATAPLANE
    } else if (strncmp(cmd, "BCPT", 4) == 0) {
        cmd_burst_trig(&cmd[4]);
#endif
    } else if (strncmp(cmd, "BRDO", 4) == 0) {
        cmd_burst_readout(&cmd[4]);
    } else if (strncmp(cmd, "BMAP", 4) == 0) {
        cmd_burst_map(&cmd[4]);
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
    spike_shape_init_default();   /* boot default spike-pulse shape = inverted 30 ns trapezoid */
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
