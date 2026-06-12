// ZynqMP PS Ethernet readout for DAQ_LAUNCH ADC DMA buffers.
//
// This application runs on psu_cortexa53_0. It does not arm the PL DMA or
// touch MicroBlaze control registers. MicroBlaze still performs capture into
// PS DDR; this app only reads the fixed DDR buffers and sends them over UDP.

#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include "sleep.h"
#include "xil_cache.h"
#include "xil_exception.h"
#include "xil_printf.h"
#include "xil_types.h"
#include "xparameters.h"
#include "xscugic.h"
#include "xtime_l.h"

#include "lwip/init.h"
#include "lwip/etharp.h"
#include "lwip/ip_addr.h"
#include "lwip/netif.h"
#include "lwip/pbuf.h"
#include "lwip/udp.h"
#include "netif/xadapter.h"

#ifndef PLATFORM_EMAC_BASEADDR
# ifdef XPAR_XEMACPS_3_BASEADDR
#  define PLATFORM_EMAC_BASEADDR XPAR_XEMACPS_3_BASEADDR
# elif defined(XPAR_XEMACPS_0_BASEADDR)
#  define PLATFORM_EMAC_BASEADDR XPAR_XEMACPS_0_BASEADDR
# else
#  error "No XEMACPS base address found in xparameters.h"
# endif
#endif

#define INTC_DEVICE_ID      XPAR_SCUGIC_0_DEVICE_ID

#define DAQ_LOCAL_IP0       192
#define DAQ_LOCAL_IP1       168
#define DAQ_LOCAL_IP2       2
#define DAQ_LOCAL_IP3       10

#define DAQ_NETMASK0        255
#define DAQ_NETMASK1        255
#define DAQ_NETMASK2        255
#define DAQ_NETMASK3        0

#define DAQ_GATEWAY0        192
#define DAQ_GATEWAY1        168
#define DAQ_GATEWAY2        2
#define DAQ_GATEWAY3        1

#define DAQ_CMD_PORT        5006
#define DAQ_DEFAULT_DST_PORT 5005

#define ADC_DMA0_DDR_BASE   0x10000000u
#define ADC_DMA1_DDR_BASE   0x10020000u
#define ADC_DMA_FRAME_BYTES 16u
#define ADC_DMA_FRAMES      4096u

#define DAQ_PACKET_MAGIC    0x44415144u /* "DAQD", little-endian on wire */
#define DAQ_PACKET_VERSION  1u
#define DAQ_HEADER_BYTES    32u
#define DAQ_PAYLOAD_WORDS   320u

// Debug mailbox in PS DDR, outside the app ELF window (0x30000000..0x3FFFFFFF)
// and below the ADC DMA buffers at 0x10000000. Data accesses to low DDR are
// fine on this board; only instruction fetch from low DDR wedges (see
// build_ps_eth_stream.tcl). Read it from XSCT while the app runs, e.g.:
//   targets -set -filter {name =~ "*PSU*"}; mrd 0x0F000000 8
#define DAQ_MAILBOX_BASE    0x0F000000u

#define MBOX_PROGRESS       0u /* DAQ_MB_* progress or error code */
#define MBOX_HEARTBEAT      1u /* increments ~4 Hz while the main loop is alive */
#define MBOX_RX_CMDS        2u /* UDP commands received */
#define MBOX_TX_PKTS        3u /* UDP packets sent */
#define MBOX_WORDS          8u

/* Continuous streaming: the MicroBlaze runs the PL decimator + cyclic SG DMA
 * into per-chip DDR rings and publishes the ring write offsets in its own
 * mailbox (inside the MB HP2 window). This app drains the rings over UDP. */
#define STRM_MAILBOX        0x1003FF00u
#define STRM_MAGIC_RUNNING  0x53545201u
#define STRM_CHIPS          2u
#define STRM_PAYLOAD_BYTES  1408u
#define STRM_PKTS_PER_PASS  8u  /* small bursts pace the wire; big bursts
                                   overrun the host's UDP socket buffer */
#define STRM_MAX_LAG        (2u * 1024u * 1024u)  /* on backlog, resync to this
                                   fixed lag behind the writer (~64 ms) so the
                                   stream stays fresh instead of half a ring old */
#define STRM_PACKET_MAGIC   0x53514144u /* "DAQS", little-endian on wire */

static volatile u32 strm_running = 0;
static ip_addr_t strm_addr;
static u16 strm_port = DAQ_DEFAULT_DST_PORT;
static u32 strm_read_off[STRM_CHIPS];
static u32 strm_seq[STRM_CHIPS];
static u32 strm_drops[STRM_CHIPS];
static u32 strm_ring_base[STRM_CHIPS];
static u32 strm_ring_bytes;
static u32 strm_chunk_bytes;
static u32 strm_decim;

// Bring-up bisect aid: when DAQ_HALT_STAGE matches the low byte of a
// progress code, spin forever right after writing it. The system staying
// healthy (core haltable, mailbox readable) proves everything up to that
// stage is innocent. Set to -1 for normal operation.
#ifndef DAQ_HALT_STAGE
#define DAQ_HALT_STAGE      (-1)
#endif

#define DAQ_BARRIER(stage) \
    do { \
        if ((DAQ_HALT_STAGE) == (stage)) { \
            for (;;) { \
            } \
        } \
    } while (0)

#define DAQ_MB_MAIN_ENTERED 0xDA000001u
#define DAQ_MB_GIC_READY    0xDA000002u
#define DAQ_MB_LWIP_INIT    0xDA000003u
#define DAQ_MB_EMAC_ADDED   0xDA000004u
#define DAQ_MB_NETIF_UP     0xDA000005u
#define DAQ_MB_UDP_READY    0xDA000006u
#define DAQ_MB_LOOP_RUNNING 0xDA0000FFu
#define DAQ_MB_ERR_EMAC_ADD 0xDAE00001u
#define DAQ_MB_ERR_UDP_NEW  0xDAE00002u
#define DAQ_MB_ERR_UDP_BIND 0xDAE00003u

static struct netif server_netif;
static struct udp_pcb *cmd_pcb;
static struct udp_pcb *tx_pcb;

static volatile u32 pending_send = 0;
static ip_addr_t pending_addr;
static u16 pending_port = DAQ_DEFAULT_DST_PORT;
static u32 pending_chip_mask = 0x3u;
static u32 pending_frames = ADC_DMA_FRAMES;
static u32 sequence = 0;

static void mailbox_write(u32 index, u32 value)
{
    volatile u32 *mbox = (volatile u32 *)(UINTPTR)DAQ_MAILBOX_BASE;

    mbox[index] = value;
    Xil_DCacheFlushRange((UINTPTR)&mbox[index], sizeof(u32));
}

static u32 mailbox_read(u32 index)
{
    volatile u32 *mbox = (volatile u32 *)(UINTPTR)DAQ_MAILBOX_BASE;

    return mbox[index];
}

static void mailbox_increment(u32 index)
{
    mailbox_write(index, mailbox_read(index) + 1u);
}

// The lwIP emacps adapter is interrupt driven even in RAW API mode: the GEM
// ISR moves RX frames into a queue that xemacif_input() drains. xemac_add()
// registers and enables the GEM IRQ in the GIC distributor, but initializing
// the GIC and unmasking IRQs at the CPU is the application's job (see the
// lwIP echo server template's platform_setup_interrupts). Without this the
// board never sees ARP or UDP, even with PHY link up.
static void platform_setup_interrupts(void)
{
    Xil_ExceptionInit();
    XScuGic_DeviceInitialize(INTC_DEVICE_ID);

    Xil_ExceptionRegisterHandler(XIL_EXCEPTION_ID_IRQ_INT,
            (Xil_ExceptionHandler)XScuGic_DeviceInterruptHandler,
            (void *)(UINTPTR)INTC_DEVICE_ID);
}

static void put_u16_le(u8 *p, u16 value)
{
    p[0] = (u8)(value & 0xFFu);
    p[1] = (u8)((value >> 8) & 0xFFu);
}

static void put_u32_le(u8 *p, u32 value)
{
    p[0] = (u8)(value & 0xFFu);
    p[1] = (u8)((value >> 8) & 0xFFu);
    p[2] = (u8)((value >> 16) & 0xFFu);
    p[3] = (u8)((value >> 24) & 0xFFu);
}

static u32 chip_base(u32 chip)
{
    return (chip == 0u) ? ADC_DMA0_DDR_BASE : ADC_DMA1_DDR_BASE;
}

static void send_status_packet(const ip_addr_t *addr, u16 port, const char *text)
{
    struct pbuf *p;
    size_t len = strlen(text);

    p = pbuf_alloc(PBUF_TRANSPORT, (u16)len, PBUF_RAM);
    if (p == NULL) {
        return;
    }

    memcpy(p->payload, text, len);
    udp_sendto(tx_pcb, p, addr, port);
    pbuf_free(p);
    mailbox_increment(MBOX_TX_PKTS);
}

static void send_data_packet(u32 chip, u32 word_offset, u32 word_count,
                             const ip_addr_t *addr, u16 port, u32 flags)
{
    const u32 base = chip_base(chip);
    const u32 byte_count = word_count * 4u;
    struct pbuf *p;
    u8 *payload;

    p = pbuf_alloc(PBUF_TRANSPORT, (u16)(DAQ_HEADER_BYTES + byte_count), PBUF_RAM);
    if (p == NULL) {
        xil_printf("WARN: pbuf_alloc failed\r\n");
        return;
    }

    payload = (u8 *)p->payload;
    put_u32_le(payload + 0, DAQ_PACKET_MAGIC);
    put_u16_le(payload + 4, DAQ_PACKET_VERSION);
    put_u16_le(payload + 6, DAQ_HEADER_BYTES);
    put_u32_le(payload + 8, sequence);
    put_u32_le(payload + 12, chip);
    put_u32_le(payload + 16, word_offset);
    put_u32_le(payload + 20, word_count);
    put_u32_le(payload + 24, byte_count);
    put_u32_le(payload + 28, flags);

    memcpy(payload + DAQ_HEADER_BYTES,
           (const void *)(UINTPTR)(base + word_offset * 4u),
           byte_count);

    udp_sendto(tx_pcb, p, addr, port);
    pbuf_free(p);
    mailbox_increment(MBOX_TX_PKTS);
}

static void stream_chip(u32 chip, u32 frames, const ip_addr_t *addr, u16 port)
{
    const u32 words_total = frames * (ADC_DMA_FRAME_BYTES / 4u);
    u32 word_offset = 0;

    Xil_DCacheInvalidateRange((UINTPTR)chip_base(chip), frames * ADC_DMA_FRAME_BYTES);

    while (word_offset < words_total) {
        u32 words = words_total - word_offset;
        u32 flags = 0u;

        if (words > DAQ_PAYLOAD_WORDS) {
            words = DAQ_PAYLOAD_WORDS;
        } else {
            flags |= 1u; /* last packet for this chip */
        }

        send_data_packet(chip, word_offset, words, addr, port, flags);
        word_offset += words;

        /* Service RX/ARP between TX bursts. */
        xemacif_input(&server_netif);
    }
}

static void handle_pending_send(void)
{
    u32 frames = pending_frames;
    u32 mask = pending_chip_mask;
    ip_addr_t addr = pending_addr;
    u16 port = pending_port;

    pending_send = 0;

    if (frames == 0u || frames > ADC_DMA_FRAMES) {
        frames = ADC_DMA_FRAMES;
    }
    if ((mask & 0x3u) == 0u) {
        mask = 0x3u;
    }

    xil_printf("UDP readout: mask=0x%08lx frames=%lu\r\n",
               (unsigned long)mask, (unsigned long)frames);

    send_status_packet(&addr, port, "DAQ_BEGIN\n");
    if ((mask & 0x1u) != 0u) {
        stream_chip(0u, frames, &addr, port);
    }
    if ((mask & 0x2u) != 0u) {
        stream_chip(1u, frames, &addr, port);
    }
    send_status_packet(&addr, port, "DAQ_END\n");
    sequence++;
}

static u32 strm_mbox_read(u32 offset)
{
    Xil_DCacheInvalidateRange((UINTPTR)STRM_MAILBOX, 64u);
    return *(volatile u32 *)(UINTPTR)(STRM_MAILBOX + offset);
}

static int strm_begin(const ip_addr_t *addr, u16 port)
{
    u32 chip;

    if (strm_mbox_read(0x00u) != STRM_MAGIC_RUNNING) {
        return -1;
    }
    strm_decim = strm_mbox_read(0x04u);
    strm_ring_bytes = strm_mbox_read(0x08u);
    strm_chunk_bytes = strm_mbox_read(0x0Cu);
    strm_ring_base[0] = strm_mbox_read(0x10u);
    strm_ring_base[1] = strm_mbox_read(0x18u);
    for (chip = 0; chip < STRM_CHIPS; chip++) {
        /* Start the monotonic read counter at the writer: stream only new
         * data (gap == 0 initially). */
        strm_read_off[chip] = strm_mbox_read(0x14u + chip * 8u);
        strm_seq[chip] = 0;
        strm_drops[chip] = 0;
    }
    strm_addr = *addr;
    strm_port = port;
    strm_running = 1;
    return 0;
}

static void strm_send_packet(u32 chip, u32 offset, u32 bytes)
{
    struct pbuf *p;
    u8 *payload;
    UINTPTR src = (UINTPTR)(strm_ring_base[chip] + offset);

    p = pbuf_alloc(PBUF_TRANSPORT, (u16)(DAQ_HEADER_BYTES + bytes), PBUF_RAM);
    if (p == NULL) {
        return;
    }

    Xil_DCacheInvalidateRange(src & ~63u,
                              ((bytes + (u32)(src & 63u) + 63u) & ~63u));

    payload = (u8 *)p->payload;
    put_u32_le(payload + 0, STRM_PACKET_MAGIC);
    put_u16_le(payload + 4, 1u);
    put_u16_le(payload + 6, DAQ_HEADER_BYTES);
    put_u32_le(payload + 8, strm_seq[chip]);
    put_u32_le(payload + 12, chip);
    put_u32_le(payload + 16, offset);
    put_u32_le(payload + 20, bytes);
    put_u32_le(payload + 24, strm_drops[chip]);
    put_u32_le(payload + 28, strm_decim);
    memcpy(payload + DAQ_HEADER_BYTES, (const void *)src, bytes);

    udp_sendto(tx_pcb, p, &strm_addr, strm_port);
    pbuf_free(p);
    strm_seq[chip]++;
    mailbox_increment(MBOX_TX_PKTS);
}

static void strm_service(void)
{
    u32 chip;

    if (!strm_running) {
        return;
    }
    if (strm_mbox_read(0x00u) != STRM_MAGIC_RUNNING) {
        strm_running = 0;
        return;
    }

    for (chip = 0; chip < STRM_CHIPS; chip++) {
        /* write_tot and strm_read_off are MONOTONIC byte counters (they wrap
         * only at 2^32). Their difference is a true, unambiguous distance --
         * no "behind vs lapped" confusion like wrapped ring offsets had. */
        u32 write_tot = strm_mbox_read(0x14u + chip * 8u);
        u32 pkts = 0;

        while (pkts < STRM_PKTS_PER_PASS) {
            u32 gap = write_tot - strm_read_off[chip];
            u32 bytes;
            u32 ring_off;

            if (gap == 0u) {
                break;
            }
            /* Fell behind the writer (e.g. a publish stall during a UART
             * command): jump to a fixed small lag behind the head so the
             * stream stays FRESH instead of draining ~1 s of stale ring.
             * This is what makes a source switch appear immediately. */
            if (gap > STRM_MAX_LAG) {
                u32 resume = write_tot - STRM_MAX_LAG;
                strm_drops[chip] += gap - STRM_MAX_LAG;
                strm_read_off[chip] = resume;
                gap = STRM_MAX_LAG;
            }

            bytes = gap;
            if (bytes > STRM_PAYLOAD_BYTES) {
                bytes = STRM_PAYLOAD_BYTES;
            }
            ring_off = strm_read_off[chip] & (strm_ring_bytes - 1u);
            if (ring_off + bytes > strm_ring_bytes) {
                bytes = strm_ring_bytes - ring_off;   /* don't span the wrap */
            }

            strm_send_packet(chip, ring_off, bytes);
            strm_read_off[chip] += bytes;             /* monotonic */
            pkts++;
        }
    }
}

static void parse_command(const char *cmd, const ip_addr_t *addr, u16 port)
{
    u32 chip = 2u; /* 2 means both chips. */
    u32 frames = ADC_DMA_FRAMES;
    char *endp;

    if (strncmp(cmd, "PING", 4) == 0) {
        send_status_packet(addr, port, "PONG\n");
        return;
    }

    if (strncmp(cmd, "STRM", 4) == 0) {
        if (strm_begin(addr, port) != 0) {
            send_status_packet(addr, port,
                "ERR stream not armed; run 'STRM <decim>' on the MicroBlaze UART first\n");
        } else {
            send_status_packet(addr, port, "STRM_BEGIN\n");
        }
        return;
    }

    if (strncmp(cmd, "STOP", 4) == 0) {
        strm_running = 0;
        send_status_packet(addr, port, "STRM_END\n");
        return;
    }

    if (strncmp(cmd, "SEND", 4) != 0) {
        send_status_packet(addr, port, "ERR expected PING or SEND [chip] [frames]\n");
        return;
    }

    chip = (u32)strtoul(cmd + 4, &endp, 0);
    if (endp != (cmd + 4)) {
        frames = (u32)strtoul(endp, NULL, 0);
    } else {
        chip = 2u;
    }

    pending_addr = *addr;
    pending_port = port;
    pending_frames = frames;
    if (chip == 0u) {
        pending_chip_mask = 0x1u;
    } else if (chip == 1u) {
        pending_chip_mask = 0x2u;
    } else {
        pending_chip_mask = 0x3u;
    }
    pending_send = 1u;
}

static void udp_recv_callback(void *arg, struct udp_pcb *pcb, struct pbuf *p,
                              const ip_addr_t *addr, u16 port)
{
    char cmd[64];
    u16 copy_len;

    (void)arg;
    (void)pcb;

    if (p == NULL) {
        return;
    }

    copy_len = p->tot_len;
    if (copy_len >= sizeof(cmd)) {
        copy_len = sizeof(cmd) - 1u;
    }
    pbuf_copy_partial(p, cmd, copy_len, 0);
    cmd[copy_len] = '\0';
    pbuf_free(p);

    mailbox_increment(MBOX_RX_CMDS);
    parse_command(cmd, addr, port);
}

static int network_init(void)
{
    ip_addr_t ipaddr;
    ip_addr_t netmask;
    ip_addr_t gateway;
    unsigned char mac[6] = {0x00, 0x0A, 0x35, 0x00, 0x01, 0x10};

    Xil_ICacheEnable();
    Xil_DCacheEnable();

    platform_setup_interrupts();
    mailbox_write(MBOX_PROGRESS, DAQ_MB_GIC_READY);
    DAQ_BARRIER(2);

    lwip_init();
    mailbox_write(MBOX_PROGRESS, DAQ_MB_LWIP_INIT);
    DAQ_BARRIER(3);

    IP4_ADDR(&ipaddr, DAQ_LOCAL_IP0, DAQ_LOCAL_IP1, DAQ_LOCAL_IP2, DAQ_LOCAL_IP3);
    IP4_ADDR(&netmask, DAQ_NETMASK0, DAQ_NETMASK1, DAQ_NETMASK2, DAQ_NETMASK3);
    IP4_ADDR(&gateway, DAQ_GATEWAY0, DAQ_GATEWAY1, DAQ_GATEWAY2, DAQ_GATEWAY3);

    // xemac_add() also runs PHY autonegotiation; a hang between LWIP_INIT and
    // EMAC_ADDED in the mailbox means no PHY link.
    if (!xemac_add(&server_netif, &ipaddr, &netmask, &gateway, mac,
                   PLATFORM_EMAC_BASEADDR)) {
        xil_printf("ERROR: xemac_add failed\r\n");
        mailbox_write(MBOX_PROGRESS, DAQ_MB_ERR_EMAC_ADD);
        return -1;
    }
    mailbox_write(MBOX_PROGRESS, DAQ_MB_EMAC_ADDED);
    DAQ_BARRIER(4);

    netif_set_default(&server_netif);
    netif_set_up(&server_netif);

    Xil_ExceptionEnable();
    mailbox_write(MBOX_PROGRESS, DAQ_MB_NETIF_UP);
    DAQ_BARRIER(5);

    tx_pcb = udp_new();
    cmd_pcb = udp_new();
    if (tx_pcb == NULL || cmd_pcb == NULL) {
        xil_printf("ERROR: udp_new failed\r\n");
        mailbox_write(MBOX_PROGRESS, DAQ_MB_ERR_UDP_NEW);
        return -1;
    }
    if (udp_bind(cmd_pcb, IP_ADDR_ANY, DAQ_CMD_PORT) != ERR_OK) {
        xil_printf("ERROR: udp_bind failed\r\n");
        mailbox_write(MBOX_PROGRESS, DAQ_MB_ERR_UDP_BIND);
        return -1;
    }
    udp_recv(cmd_pcb, udp_recv_callback, NULL);
    mailbox_write(MBOX_PROGRESS, DAQ_MB_UDP_READY);

    xil_printf("DAQ PS Ethernet streamer ready at %d.%d.%d.%d:%d\r\n",
               DAQ_LOCAL_IP0, DAQ_LOCAL_IP1, DAQ_LOCAL_IP2, DAQ_LOCAL_IP3,
               DAQ_CMD_PORT);
    return 0;
}

int main(void)
{
    XTime tick_start;
    u32 arp_elapsed_ms = 0;
    u32 i;

    for (i = 0; i < MBOX_WORDS; i++) {
        mailbox_write(i, 0u);
    }
    mailbox_write(MBOX_PROGRESS, DAQ_MB_MAIN_ENTERED);
    DAQ_BARRIER(1); /* after mailbox/DDR only */

    xil_printf("\r\nDAQ_LAUNCH PS Ethernet readout\r\n");
    DAQ_BARRIER(7); /* after first PS UART0 access */

    if (network_init() != 0) {
        xil_printf("Network init failed; stopping.\r\n");
        while (1) {
            sleep(1);
        }
    }

    mailbox_write(MBOX_PROGRESS, DAQ_MB_LOOP_RUNNING);
    XTime_GetTime(&tick_start);

    while (1) {
        XTime now;

        xemacif_input(&server_netif);

        if (pending_send != 0u) {
            handle_pending_send();
        }

        strm_service();

        /* ~4 Hz housekeeping: mailbox heartbeat plus the lwIP ARP timer. */
        XTime_GetTime(&now);
        if ((now - tick_start) >= (COUNTS_PER_SECOND / 4u)) {
            tick_start = now;
            mailbox_increment(MBOX_HEARTBEAT);

            arp_elapsed_ms += 250u;
            if (arp_elapsed_ms >= ARP_TMR_INTERVAL) {
                arp_elapsed_ms = 0;
                etharp_tmr();
            }
        }
    }
}
