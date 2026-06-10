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
#include "xil_printf.h"
#include "xil_types.h"
#include "xparameters.h"

#include "lwip/init.h"
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

#define DAQ_LOCAL_IP0       192
#define DAQ_LOCAL_IP1       168
#define DAQ_LOCAL_IP2       1
#define DAQ_LOCAL_IP3       10

#define DAQ_NETMASK0        255
#define DAQ_NETMASK1        255
#define DAQ_NETMASK2        255
#define DAQ_NETMASK3        0

#define DAQ_GATEWAY0        192
#define DAQ_GATEWAY1        168
#define DAQ_GATEWAY2        1
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

static struct netif server_netif;
static struct udp_pcb *cmd_pcb;
static struct udp_pcb *tx_pcb;

static volatile u32 pending_send = 0;
static ip_addr_t pending_addr;
static u16 pending_port = DAQ_DEFAULT_DST_PORT;
static u32 pending_chip_mask = 0x3u;
static u32 pending_frames = ADC_DMA_FRAMES;
static u32 sequence = 0;

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

static void parse_command(const char *cmd, const ip_addr_t *addr, u16 port)
{
    u32 chip = 2u; /* 2 means both chips. */
    u32 frames = ADC_DMA_FRAMES;
    char *endp;

    if (strncmp(cmd, "PING", 4) == 0) {
        send_status_packet(addr, port, "PONG\n");
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

    lwip_init();

    IP4_ADDR(&ipaddr, DAQ_LOCAL_IP0, DAQ_LOCAL_IP1, DAQ_LOCAL_IP2, DAQ_LOCAL_IP3);
    IP4_ADDR(&netmask, DAQ_NETMASK0, DAQ_NETMASK1, DAQ_NETMASK2, DAQ_NETMASK3);
    IP4_ADDR(&gateway, DAQ_GATEWAY0, DAQ_GATEWAY1, DAQ_GATEWAY2, DAQ_GATEWAY3);

    if (!xemac_add(&server_netif, &ipaddr, &netmask, &gateway, mac,
                   PLATFORM_EMAC_BASEADDR)) {
        xil_printf("ERROR: xemac_add failed\r\n");
        return -1;
    }

    netif_set_default(&server_netif);
    netif_set_up(&server_netif);

    tx_pcb = udp_new();
    cmd_pcb = udp_new();
    if (tx_pcb == NULL || cmd_pcb == NULL) {
        xil_printf("ERROR: udp_new failed\r\n");
        return -1;
    }
    if (udp_bind(cmd_pcb, IP_ADDR_ANY, DAQ_CMD_PORT) != ERR_OK) {
        xil_printf("ERROR: udp_bind failed\r\n");
        return -1;
    }
    udp_recv(cmd_pcb, udp_recv_callback, NULL);

    xil_printf("DAQ PS Ethernet streamer ready at %d.%d.%d.%d:%d\r\n",
               DAQ_LOCAL_IP0, DAQ_LOCAL_IP1, DAQ_LOCAL_IP2, DAQ_LOCAL_IP3,
               DAQ_CMD_PORT);
    return 0;
}

int main(void)
{
    xil_printf("\r\nDAQ_LAUNCH PS Ethernet readout\r\n");

    if (network_init() != 0) {
        xil_printf("Network init failed; stopping.\r\n");
        while (1) {
            sleep(1);
        }
    }

    while (1) {
        xemacif_input(&server_netif);

        if (pending_send != 0u) {
            handle_pending_send();
        }
    }
}
