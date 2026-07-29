/*
 * Linux replacement for sw/ps_eth_stream/src/main.c.
 *
 * Linux owns GEM3 and the network stack.  This process owns only the DAQ UDP
 * protocol and the MicroBlaze/DDR mailbox contract.  The DAQ DMA address space
 * is excluded from Linux with mem=240M and is mapped uncached through /dev/mem.
 */

#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define DAQ_CMD_PORT          5006u
#define DAQ_DEFAULT_DST_PORT  5005u

#define ADC_DMA0_DDR_BASE     0x10000000u
#define ADC_DMA1_DDR_BASE     0x10020000u
#define ADC_DMA_FRAME_BYTES   16u
#define ADC_DMA_FRAMES        4096u

#define DAQ_PACKET_MAGIC      0x44415144u
#define STRM_PACKET_MAGIC     0x53514144u
#define DAQ_PACKET_VERSION    1u
#define DAQ_HEADER_BYTES      32u
#define DAQ_PAYLOAD_WORDS     320u

#define DAQ_MAILBOX_BASE      0x0F000000u
#define STRM_MAILBOX          0x1003FF00u
#define STRM_MAGIC_RUNNING    0x53545201u
#define BURST_MAGIC           0x42435054u

#define STRM_CHIPS            2u
#define STRM_PAYLOAD_BYTES    1408u
#define STRM_PKTS_PER_PASS    8u
#define STRM_MAX_LAG          (2u * 1024u * 1024u)

#define DAQ_RESERVED_FIRST    0x0F000000u
#define DAQ_RESERVED_END      0x80000000u
#define PAGE_BYTES            4096u

#define DAQ_MB_MAIN_ENTERED   0xDA000001u
#define DAQ_MB_NETIF_UP       0xDA000005u
#define DAQ_MB_UDP_READY      0xDA000006u
#define DAQ_MB_LOOP_RUNNING   0xDA0000FFu

#define MBOX_PROGRESS         0u
#define MBOX_HEARTBEAT        1u
#define MBOX_RX_CMDS          2u
#define MBOX_TX_PKTS          3u

struct phys_map {
    void *mapping;
    size_t mapping_bytes;
    uint8_t *ptr;
};

struct endpoint {
    struct sockaddr_in addr;
    int valid;
};

static int mem_fd = -1;
static int udp_fd = -1;
static volatile sig_atomic_t keep_running = 1;
static volatile uint32_t *debug_mbox;
static volatile uint32_t *daq_mbox;
static struct phys_map debug_mbox_map;
static struct phys_map daq_mbox_map;

static struct endpoint stream_endpoint;
static int stream_running;
static uint32_t stream_read_total[STRM_CHIPS];
static uint32_t stream_sequence[STRM_CHIPS];
static uint32_t stream_drops[STRM_CHIPS];
static uint32_t stream_ring_base[STRM_CHIPS];
static uint32_t stream_ring_bytes;
static uint32_t stream_decimation;
static struct phys_map stream_ring_map[STRM_CHIPS];

static struct endpoint burst_endpoint;
static uint32_t burst_request_last;
static uint32_t burst_bytes;
static uint32_t burst_base[STRM_CHIPS];
static uint32_t burst_chip;
static uint32_t burst_offset;
static int burst_active;
static struct phys_map burst_map[STRM_CHIPS];

static uint32_t legacy_sequence;

static void signal_handler(int signo)
{
    (void)signo;
    keep_running = 0;
}

static uint64_t monotonic_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static int valid_reserved_range(uint32_t address, uint32_t bytes)
{
    uint64_t end = (uint64_t)address + bytes;
    return bytes != 0u && address >= DAQ_RESERVED_FIRST &&
           end <= DAQ_RESERVED_END && end > address;
}

static void unmap_phys(struct phys_map *map)
{
    if (map->mapping != NULL && map->mapping != MAP_FAILED) {
        munmap(map->mapping, map->mapping_bytes);
    }
    memset(map, 0, sizeof(*map));
}

static int map_phys(uint32_t address, uint32_t bytes, struct phys_map *map)
{
    uint32_t aligned;
    uint32_t offset;
    size_t span;
    void *mapping;

    unmap_phys(map);
    if (!valid_reserved_range(address, bytes)) {
        fprintf(stderr, "DAQ-ETH: invalid physical range 0x%08" PRIx32
                        " + 0x%08" PRIx32 "\n", address, bytes);
        return -1;
    }

    aligned = address & ~(PAGE_BYTES - 1u);
    offset = address - aligned;
    span = (size_t)offset + bytes;
    span = (span + PAGE_BYTES - 1u) & ~(size_t)(PAGE_BYTES - 1u);
    mapping = mmap(NULL, span, PROT_READ | PROT_WRITE, MAP_SHARED, mem_fd,
                   (off_t)aligned);
    if (mapping == MAP_FAILED) {
        fprintf(stderr, "DAQ-ETH: mmap 0x%08" PRIx32 " failed: %s\n",
                address, strerror(errno));
        memset(map, 0, sizeof(*map));
        return -1;
    }

    map->mapping = mapping;
    map->mapping_bytes = span;
    map->ptr = (uint8_t *)mapping + offset;
    return 0;
}

static inline uint32_t daq_mbox_read(uint32_t offset)
{
    uint32_t value;
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    value = daq_mbox[offset / 4u];
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    return value;
}

static inline void daq_mbox_write(uint32_t offset, uint32_t value)
{
    daq_mbox[offset / 4u] = value;
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}

static inline void debug_write(uint32_t index, uint32_t value)
{
    debug_mbox[index] = value;
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}

static inline void debug_increment(uint32_t index)
{
    debug_write(index, debug_mbox[index] + 1u);
}

static void put_u16_le(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put_u32_le(uint8_t *p, uint32_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16);
    p[3] = (uint8_t)(value >> 24);
}

static int send_packet(const struct endpoint *endpoint, const void *data,
                       size_t bytes)
{
    ssize_t sent;
    if (!endpoint->valid) {
        return -1;
    }
    sent = sendto(udp_fd, data, bytes, 0,
                  (const struct sockaddr *)&endpoint->addr,
                  sizeof(endpoint->addr));
    if (sent == (ssize_t)bytes) {
        debug_increment(MBOX_TX_PKTS);
        return 0;
    }
    return -1;
}

static void send_status(const struct endpoint *endpoint, const char *text)
{
    (void)send_packet(endpoint, text, strlen(text));
}

static void endpoint_from_peer(struct endpoint *endpoint,
                               const struct sockaddr_in *peer)
{
    endpoint->addr = *peer;
    endpoint->valid = 1;
}

static void release_stream_maps(void)
{
    unsigned chip;
    for (chip = 0; chip < STRM_CHIPS; ++chip) {
        unmap_phys(&stream_ring_map[chip]);
    }
}

static int stream_begin(const struct endpoint *endpoint)
{
    unsigned chip;

    if (daq_mbox_read(0x00u) != STRM_MAGIC_RUNNING) {
        return -1;
    }

    stream_decimation = daq_mbox_read(0x04u);
    stream_ring_bytes = daq_mbox_read(0x08u);
    stream_ring_base[0] = daq_mbox_read(0x10u);
    stream_ring_base[1] = daq_mbox_read(0x18u);

    if (stream_ring_bytes == 0u ||
        (stream_ring_bytes & (stream_ring_bytes - 1u)) != 0u) {
        fprintf(stderr, "DAQ-ETH: invalid stream ring size 0x%08" PRIx32 "\n",
                stream_ring_bytes);
        return -1;
    }

    release_stream_maps();
    for (chip = 0; chip < STRM_CHIPS; ++chip) {
        if (map_phys(stream_ring_base[chip], stream_ring_bytes,
                     &stream_ring_map[chip]) != 0) {
            release_stream_maps();
            return -1;
        }
        stream_read_total[chip] = daq_mbox_read(0x14u + chip * 8u);
        stream_sequence[chip] = 0u;
        stream_drops[chip] = 0u;
    }

    stream_endpoint = *endpoint;
    stream_running = 1;
    return 0;
}

static void stream_stop(void)
{
    stream_running = 0;
    release_stream_maps();
}

static void stream_send(unsigned chip, uint32_t ring_offset, uint32_t bytes)
{
    uint8_t packet[DAQ_HEADER_BYTES + STRM_PAYLOAD_BYTES];

    put_u32_le(packet + 0, STRM_PACKET_MAGIC);
    put_u16_le(packet + 4, DAQ_PACKET_VERSION);
    put_u16_le(packet + 6, DAQ_HEADER_BYTES);
    put_u32_le(packet + 8, stream_sequence[chip]);
    put_u32_le(packet + 12, chip);
    put_u32_le(packet + 16, ring_offset);
    put_u32_le(packet + 20, bytes);
    put_u32_le(packet + 24, stream_drops[chip]);
    put_u32_le(packet + 28, stream_decimation);
    memcpy(packet + DAQ_HEADER_BYTES,
           stream_ring_map[chip].ptr + ring_offset, bytes);
    if (send_packet(&stream_endpoint, packet, DAQ_HEADER_BYTES + bytes) == 0) {
        stream_sequence[chip]++;
    }
}

static void stream_service(void)
{
    unsigned chip;

    if (!stream_running) {
        return;
    }
    if (daq_mbox_read(0x00u) != STRM_MAGIC_RUNNING) {
        stream_stop();
        return;
    }

    for (chip = 0; chip < STRM_CHIPS; ++chip) {
        uint32_t write_total = daq_mbox_read(0x14u + chip * 8u);
        unsigned packets = 0;

        while (packets < STRM_PKTS_PER_PASS) {
            uint32_t gap = write_total - stream_read_total[chip];
            uint32_t bytes;
            uint32_t ring_offset;

            if (gap == 0u) {
                break;
            }
            if (gap > STRM_MAX_LAG) {
                uint32_t resume = write_total - STRM_MAX_LAG;
                stream_drops[chip] += gap - STRM_MAX_LAG;
                stream_read_total[chip] = resume;
                gap = STRM_MAX_LAG;
            }

            bytes = gap > STRM_PAYLOAD_BYTES ? STRM_PAYLOAD_BYTES : gap;
            ring_offset = stream_read_total[chip] & (stream_ring_bytes - 1u);
            if (ring_offset + bytes > stream_ring_bytes) {
                bytes = stream_ring_bytes - ring_offset;
            }
            stream_send(chip, ring_offset, bytes);
            stream_read_total[chip] += bytes;
            packets++;
        }
    }
}

static void release_burst_maps(void)
{
    unsigned chip;
    for (chip = 0; chip < STRM_CHIPS; ++chip) {
        unmap_phys(&burst_map[chip]);
    }
}

static int burst_prepare(uint32_t request)
{
    unsigned chip;

    burst_request_last = request;
    burst_bytes = daq_mbox_read(0x04u);
    burst_base[0] = daq_mbox_read(0x08u);
    burst_base[1] = daq_mbox_read(0x0Cu);
    if (burst_bytes == 0u) {
        return -1;
    }

    release_burst_maps();
    for (chip = 0; chip < STRM_CHIPS; ++chip) {
        if (map_phys(burst_base[chip], burst_bytes, &burst_map[chip]) != 0) {
            release_burst_maps();
            return -1;
        }
    }
    burst_chip = 0u;
    burst_offset = 0u;
    burst_active = 1;
    return 0;
}

static void burst_send(unsigned chip, uint32_t offset, uint32_t bytes)
{
    uint8_t packet[DAQ_HEADER_BYTES + STRM_PAYLOAD_BYTES];

    put_u32_le(packet + 0, STRM_PACKET_MAGIC);
    put_u16_le(packet + 4, DAQ_PACKET_VERSION);
    put_u16_le(packet + 6, DAQ_HEADER_BYTES);
    put_u32_le(packet + 8, offset / STRM_PAYLOAD_BYTES);
    put_u32_le(packet + 12, chip);
    put_u32_le(packet + 16, offset);
    put_u32_le(packet + 20, bytes);
    put_u32_le(packet + 24, burst_request_last);
    put_u32_le(packet + 28, 1u);
    memcpy(packet + DAQ_HEADER_BYTES, burst_map[chip].ptr + offset, bytes);
    (void)send_packet(&burst_endpoint, packet, DAQ_HEADER_BYTES + bytes);
}

static void burst_service(void)
{
    unsigned packets = 0;

    if (!burst_active) {
        uint32_t request;
        if (!burst_endpoint.valid || daq_mbox_read(0x00u) != BURST_MAGIC) {
            return;
        }
        request = daq_mbox_read(0x10u);
        if (request == burst_request_last) {
            return;
        }
        if (burst_prepare(request) != 0) {
            fprintf(stderr, "DAQ-ETH: rejecting invalid burst mailbox\n");
            return;
        }
    }

    while (burst_active && packets < STRM_PKTS_PER_PASS) {
        uint32_t remaining = burst_bytes - burst_offset;
        uint32_t bytes = remaining > STRM_PAYLOAD_BYTES
                           ? STRM_PAYLOAD_BYTES : remaining;
        burst_send(burst_chip, burst_offset, bytes);
        burst_offset += bytes;
        packets++;

        if (burst_offset >= burst_bytes) {
            if (burst_chip == 0u) {
                burst_chip = 1u;
                burst_offset = 0u;
            } else {
                burst_active = 0;
                release_burst_maps();
                daq_mbox_write(0x14u, burst_request_last);
            }
        }
    }
}

static uint32_t chip_base(unsigned chip)
{
    return chip == 0u ? ADC_DMA0_DDR_BASE : ADC_DMA1_DDR_BASE;
}

static void legacy_send_chip(unsigned chip, uint32_t frames,
                             const struct endpoint *endpoint)
{
    struct phys_map map = {0};
    uint32_t words_total = frames * (ADC_DMA_FRAME_BYTES / 4u);
    uint32_t word_offset = 0u;

    if (map_phys(chip_base(chip), frames * ADC_DMA_FRAME_BYTES, &map) != 0) {
        return;
    }
    while (word_offset < words_total) {
        uint8_t packet[DAQ_HEADER_BYTES + DAQ_PAYLOAD_WORDS * 4u];
        uint32_t words = words_total - word_offset;
        uint32_t flags = 0u;
        uint32_t bytes;

        if (words > DAQ_PAYLOAD_WORDS) {
            words = DAQ_PAYLOAD_WORDS;
        } else {
            flags = 1u;
        }
        bytes = words * 4u;
        put_u32_le(packet + 0, DAQ_PACKET_MAGIC);
        put_u16_le(packet + 4, DAQ_PACKET_VERSION);
        put_u16_le(packet + 6, DAQ_HEADER_BYTES);
        put_u32_le(packet + 8, legacy_sequence);
        put_u32_le(packet + 12, chip);
        put_u32_le(packet + 16, word_offset);
        put_u32_le(packet + 20, words);
        put_u32_le(packet + 24, bytes);
        put_u32_le(packet + 28, flags);
        memcpy(packet + DAQ_HEADER_BYTES, map.ptr + word_offset * 4u, bytes);
        (void)send_packet(endpoint, packet, DAQ_HEADER_BYTES + bytes);
        word_offset += words;
    }
    unmap_phys(&map);
}

static void handle_command(char *command, const struct sockaddr_in *peer)
{
    struct endpoint endpoint = {0};
    char *end;
    unsigned long chip;
    unsigned long frames = ADC_DMA_FRAMES;

    endpoint_from_peer(&endpoint, peer);
    if (strncmp(command, "PING", 4) == 0) {
        send_status(&endpoint, "PONG\n");
        return;
    }
    if (strncmp(command, "STRM", 4) == 0) {
        if (stream_begin(&endpoint) == 0) {
            send_status(&endpoint, "STRM_BEGIN\n");
        } else {
            send_status(&endpoint,
                "ERR stream not armed; run 'STRM <decim>' on the MicroBlaze UART first\n");
        }
        return;
    }
    if (strncmp(command, "STOP", 4) == 0) {
        stream_stop();
        send_status(&endpoint, "STRM_END\n");
        return;
    }
    if (strncmp(command, "BRST", 4) == 0) {
        burst_endpoint = endpoint;
        burst_active = 0;
        release_burst_maps();
        burst_request_last = daq_mbox_read(0x10u);
        send_status(&endpoint, "BRST_READY\n");
        return;
    }
    if (strncmp(command, "SEND", 4) != 0) {
        send_status(&endpoint, "ERR expected PING or SEND [chip] [frames]\n");
        return;
    }

    chip = strtoul(command + 4, &end, 0);
    if (end != command + 4) {
        frames = strtoul(end, NULL, 0);
    } else {
        chip = 2u;
    }
    if (frames == 0u || frames > ADC_DMA_FRAMES) {
        frames = ADC_DMA_FRAMES;
    }

    send_status(&endpoint, "DAQ_BEGIN\n");
    if (chip == 0u || chip > 1u) {
        legacy_send_chip(0u, (uint32_t)frames, &endpoint);
    }
    if (chip == 1u || chip > 1u) {
        legacy_send_chip(1u, (uint32_t)frames, &endpoint);
    }
    send_status(&endpoint, "DAQ_END\n");
    legacy_sequence++;
}

static int setup(void)
{
    struct sockaddr_in local = {0};
    int send_buffer = 4 * 1024 * 1024;

    mem_fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (mem_fd < 0) {
        perror("DAQ-ETH: open /dev/mem");
        return -1;
    }
    if (map_phys(DAQ_MAILBOX_BASE, 64u, &debug_mbox_map) != 0 ||
        map_phys(STRM_MAILBOX, 64u, &daq_mbox_map) != 0) {
        return -1;
    }
    debug_mbox = (volatile uint32_t *)debug_mbox_map.ptr;
    daq_mbox = (volatile uint32_t *)daq_mbox_map.ptr;
    memset((void *)debug_mbox, 0, 8u * sizeof(uint32_t));
    debug_write(MBOX_PROGRESS, DAQ_MB_MAIN_ENTERED);

    udp_fd = socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK, 0);
    if (udp_fd < 0) {
        perror("DAQ-ETH: socket");
        return -1;
    }
    (void)setsockopt(udp_fd, SOL_SOCKET, SO_SNDBUF,
                     &send_buffer, sizeof(send_buffer));
    local.sin_family = AF_INET;
    local.sin_addr.s_addr = htonl(INADDR_ANY);
    local.sin_port = htons(DAQ_CMD_PORT);
    if (bind(udp_fd, (struct sockaddr *)&local, sizeof(local)) != 0) {
        perror("DAQ-ETH: bind UDP 5006");
        return -1;
    }
    debug_write(MBOX_PROGRESS, DAQ_MB_UDP_READY);
    return 0;
}

static void cleanup(void)
{
    stream_stop();
    release_burst_maps();
    unmap_phys(&daq_mbox_map);
    unmap_phys(&debug_mbox_map);
    if (udp_fd >= 0) {
        close(udp_fd);
    }
    if (mem_fd >= 0) {
        close(mem_fd);
    }
}

int main(void)
{
    uint64_t next_heartbeat;

    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    if (setup() != 0) {
        cleanup();
        return 1;
    }

    debug_write(MBOX_PROGRESS, DAQ_MB_NETIF_UP);
    debug_write(MBOX_PROGRESS, DAQ_MB_LOOP_RUNNING);
    printf("DAQ-ETH: Linux readout ready on UDP 0.0.0.0:%u\n", DAQ_CMD_PORT);
    next_heartbeat = monotonic_ms() + 250u;

    while (keep_running) {
        char command[64];
        struct sockaddr_in peer;
        socklen_t peer_bytes = sizeof(peer);
        ssize_t received;
        int did_work = 0;

        received = recvfrom(udp_fd, command, sizeof(command) - 1u, 0,
                            (struct sockaddr *)&peer, &peer_bytes);
        if (received > 0) {
            command[received] = '\0';
            debug_increment(MBOX_RX_CMDS);
            handle_command(command, &peer);
            did_work = 1;
        } else if (received < 0 && errno != EAGAIN && errno != EWOULDBLOCK &&
                   errno != EINTR) {
            perror("DAQ-ETH: recvfrom");
        }

        stream_service();
        burst_service();

        if (monotonic_ms() >= next_heartbeat) {
            debug_increment(MBOX_HEARTBEAT);
            next_heartbeat += 250u;
        }
        if (!did_work && !stream_running && !burst_active) {
            usleep(100u);
        }
    }

    cleanup();
    return 0;
}
