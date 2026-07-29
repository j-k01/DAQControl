/*
 * Unified PC-to-Pico USB CDC and SPI bridge.
 *
 * Ingress 1: UDP 5007 using PSPI/PSPR or raw PCDC/PCDR packets.
 * Ingress 2: MicroBlaze requests in reserved DDR at 0x1003FE00.
 * Egress:    USB CDC ASCII RPC to the Pico 2 on /dev/ttyACM0.
 *
 * Linux remains the only owner of the ZynqMP USB controller. The existing DAQ
 * UDP service and its mailbox at 0x1003FF00 are intentionally untouched.
 */

#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define PICO_UDP_PORT           5007u
#define PICO_PROTOCOL_VERSION   1u
#define PICO_MAX_BYTES          128u
#define PICO_PACKET_HEADER      16u
#define PICO_MAILBOX_BASE       0x1003FE00u
#define PICO_MAILBOX_BYTES      256u
#define PICO_MAILBOX_DATA       0x20u
#define PICO_MAILBOX_MAGIC      0x50425247u
#define PAGE_BYTES              4096u
#define PICO_MODE_MASK          0xFF000000u
#define PICO_MODE_SPI           0x00000000u
#define PICO_MODE_CDC_WRITE     0x01000000u
#define PICO_MODE_CDC_READ      0x02000000u
#define PICO_MODE_CDC_FLUSH     0x03000000u
#define PICO_CDC_TIMEOUT_MAX_MS 60000u

#define PCDC_PROBE              0u
#define PCDC_WRITE              1u
#define PCDC_READ               2u
#define PCDC_FLUSH              3u

#define MB_MAGIC                0x00u
#define MB_REQUEST              0x04u
#define MB_DONE                 0x08u
#define MB_STATUS               0x0Cu
#define MB_FLAGS                0x10u
#define MB_HALF_PERIOD_US       0x14u
#define MB_TX_LENGTH            0x18u
#define MB_RX_LENGTH            0x1Cu

enum bridge_status {
    BRIDGE_OK = 0,
    BRIDGE_BAD_REQUEST = 1,
    BRIDGE_USB_UNAVAILABLE = 2,
    BRIDGE_USB_IO_ERROR = 3,
    BRIDGE_PICO_ERROR = 4,
    BRIDGE_BUSY = 5,
};

static volatile sig_atomic_t keep_running = 1;
static int mem_fd = -1;
static int udp_fd = -1;
static int pico_fd = -1;
static void *mailbox_mapping;
static size_t mailbox_mapping_bytes;
static volatile uint8_t *mailbox;
static uint32_t mailbox_last_request;

static void signal_handler(int signo)
{
    (void)signo;
    keep_running = 0;
}

static uint64_t monotonic_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u +
           (uint64_t)ts.tv_nsec / 1000000u;
}

static uint16_t get_u16_le(const uint8_t *p)
{
    return (uint16_t)p[0] | (uint16_t)((uint16_t)p[1] << 8);
}

static uint32_t get_u32_le(const uint8_t *p)
{
    return (uint32_t)p[0] |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
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

static uint32_t mailbox_read32(uint32_t offset)
{
    volatile uint32_t *word =
        (volatile uint32_t *)(void *)(mailbox + offset);
    uint32_t value;

    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    value = *word;
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    return value;
}

static void mailbox_write32(uint32_t offset, uint32_t value)
{
    volatile uint32_t *word =
        (volatile uint32_t *)(void *)(mailbox + offset);

    *word = value;
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}

/*
 * /dev/mem exposes this reserved DDR page with device-like access semantics
 * on the tested ZynqMP kernel.  libc memcpy may use unaligned pair loads or
 * stores for non-power-of-two lengths, which raises SIGBUS on such mappings.
 * Volatile byte accesses are valid for every payload length.
 */
static void mailbox_read_bytes(
    uint32_t offset, uint8_t *destination, size_t length
) {
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    for (size_t i = 0; i < length; ++i) {
        destination[i] = mailbox[offset + i];
    }
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}

static void mailbox_write_bytes(
    uint32_t offset, const uint8_t *source, size_t length
) {
    for (size_t i = 0; i < length; ++i) {
        mailbox[offset + i] = source[i];
    }
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
}

static int map_mailbox(void)
{
    uint32_t aligned = PICO_MAILBOX_BASE & ~(PAGE_BYTES - 1u);
    uint32_t offset = PICO_MAILBOX_BASE - aligned;
    size_t span = offset + PICO_MAILBOX_BYTES;

    span = (span + PAGE_BYTES - 1u) & ~(size_t)(PAGE_BYTES - 1u);
    mem_fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (mem_fd < 0) {
        perror("PICO-BRIDGE: open /dev/mem");
        return -1;
    }
    mailbox_mapping = mmap(NULL, span, PROT_READ | PROT_WRITE, MAP_SHARED,
                           mem_fd, (off_t)aligned);
    if (mailbox_mapping == MAP_FAILED) {
        perror("PICO-BRIDGE: mmap mailbox");
        mailbox_mapping = NULL;
        return -1;
    }
    mailbox_mapping_bytes = span;
    mailbox = (volatile uint8_t *)mailbox_mapping + offset;

    for (size_t i = 0; i < PICO_MAILBOX_BYTES; ++i) {
        mailbox[i] = 0u;
    }
    mailbox_write32(MB_MAGIC, PICO_MAILBOX_MAGIC);
    mailbox_last_request = 0u;
    return 0;
}

static int setup_udp(void)
{
    struct sockaddr_in local = {0};

    udp_fd = socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK, 0);
    if (udp_fd < 0) {
        perror("PICO-BRIDGE: socket");
        return -1;
    }
    local.sin_family = AF_INET;
    local.sin_addr.s_addr = htonl(INADDR_ANY);
    local.sin_port = htons(PICO_UDP_PORT);
    if (bind(udp_fd, (struct sockaddr *)&local, sizeof(local)) != 0) {
        perror("PICO-BRIDGE: bind UDP 5007");
        return -1;
    }
    return 0;
}

static void close_pico(void)
{
    if (pico_fd >= 0) {
        close(pico_fd);
        pico_fd = -1;
    }
}

static int open_pico(void)
{
    struct termios tio;

    if (pico_fd >= 0) {
        return 0;
    }
    pico_fd = open("/dev/ttyACM0", O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (pico_fd < 0) {
        return -1;
    }
    if (tcgetattr(pico_fd, &tio) != 0) {
        close_pico();
        return -1;
    }
    cfmakeraw(&tio);
    cfsetispeed(&tio, B115200);
    cfsetospeed(&tio, B115200);
    tio.c_cflag |= CLOCAL | CREAD;
    if (tcsetattr(pico_fd, TCSANOW, &tio) != 0) {
        close_pico();
        return -1;
    }
    tcflush(pico_fd, TCIOFLUSH);
    return 0;
}

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static int write_all(int fd, const uint8_t *data, size_t length)
{
    size_t offset = 0;
    uint64_t deadline = monotonic_ms() + 1000u;

    while (offset < length && monotonic_ms() < deadline) {
        ssize_t written = write(fd, data + offset, length - offset);
        if (written > 0) {
            offset += (size_t)written;
        } else if (written < 0 && errno != EAGAIN && errno != EWOULDBLOCK &&
                   errno != EINTR) {
            return -1;
        } else {
            usleep(1000u);
        }
    }
    return offset == length ? 0 : -1;
}

static enum bridge_status pico_cdc_write(
    const uint8_t *data, uint16_t length
) {
    if (length == 0u || length > PICO_MAX_BYTES) {
        return BRIDGE_BAD_REQUEST;
    }
    if (open_pico() != 0) {
        return BRIDGE_USB_UNAVAILABLE;
    }
    if (write_all(pico_fd, data, length) != 0) {
        close_pico();
        return BRIDGE_USB_IO_ERROR;
    }
    return BRIDGE_OK;
}

static enum bridge_status pico_cdc_read(
    uint16_t capacity,
    uint16_t timeout_ms,
    uint8_t *data,
    uint16_t *length
) {
    uint64_t deadline;

    *length = 0u;
    if (capacity == 0u || capacity > PICO_MAX_BYTES ||
        timeout_ms > PICO_CDC_TIMEOUT_MAX_MS) {
        return BRIDGE_BAD_REQUEST;
    }
    if (open_pico() != 0) {
        return BRIDGE_USB_UNAVAILABLE;
    }

    deadline = monotonic_ms() + timeout_ms;
    do {
        struct pollfd pfd = {.fd = pico_fd, .events = POLLIN};
        uint64_t now = monotonic_ms();
        int wait_ms = 0;
        int ready;

        if (timeout_ms != 0u && now < deadline) {
            uint64_t remaining = deadline - now;
            wait_ms = remaining > 50u ? 50 : (int)remaining;
        }
        ready = poll(&pfd, 1, wait_ms);
        if (ready < 0 && errno != EINTR) {
            close_pico();
            return BRIDGE_USB_IO_ERROR;
        }
        if (ready > 0 && (pfd.revents & (POLLERR | POLLHUP | POLLNVAL))) {
            close_pico();
            return BRIDGE_USB_IO_ERROR;
        }
        if (ready > 0 && (pfd.revents & POLLIN)) {
            ssize_t count = read(pico_fd, data, capacity);
            if (count > 0) {
                *length = (uint16_t)count;
                return BRIDGE_OK;
            }
            if (count == 0) {
                close_pico();
                return BRIDGE_USB_IO_ERROR;
            }
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                close_pico();
                return BRIDGE_USB_IO_ERROR;
            }
        }
    } while (timeout_ms != 0u && monotonic_ms() < deadline);
    return BRIDGE_OK;
}

static enum bridge_status pico_cdc_flush(void)
{
    if (open_pico() != 0) {
        return BRIDGE_USB_UNAVAILABLE;
    }
    if (tcflush(pico_fd, TCIFLUSH) != 0) {
        close_pico();
        return BRIDGE_USB_IO_ERROR;
    }
    return BRIDGE_OK;
}

static enum bridge_status pico_transfer(
    uint8_t flags,
    uint16_t half_period_us,
    const uint8_t *tx,
    uint16_t tx_length,
    uint8_t *rx,
    uint16_t *rx_length
) {
    static const char digits[] = "0123456789ABCDEF";
    char command[2u * PICO_MAX_BYTES + 48u];
    char response[2u * PICO_MAX_BYTES + 64u];
    size_t command_length;
    size_t response_length = 0;
    uint64_t deadline;

    if (tx_length == 0u || tx_length > PICO_MAX_BYTES ||
        half_period_us == 0u || half_period_us > 100u) {
        return BRIDGE_BAD_REQUEST;
    }
    if (open_pico() != 0) {
        return BRIDGE_USB_UNAVAILABLE;
    }

    command_length = (size_t)snprintf(
        command, sizeof(command), "SPI %u %u ",
        (unsigned)flags, (unsigned)half_period_us);
    for (uint16_t i = 0; i < tx_length; ++i) {
        command[command_length++] = digits[tx[i] >> 4];
        command[command_length++] = digits[tx[i] & 0x0Fu];
    }
    command[command_length++] = '\n';

    tcflush(pico_fd, TCIFLUSH);
    if (write_all(pico_fd, (const uint8_t *)command, command_length) != 0) {
        close_pico();
        return BRIDGE_USB_IO_ERROR;
    }

    deadline = monotonic_ms() + 3000u;
    while (monotonic_ms() < deadline) {
        struct pollfd pfd = {.fd = pico_fd, .events = POLLIN};
        int ready = poll(&pfd, 1, 50);
        if (ready < 0 && errno != EINTR) {
            close_pico();
            return BRIDGE_USB_IO_ERROR;
        }
        if (ready > 0 && (pfd.revents & (POLLERR | POLLHUP | POLLNVAL))) {
            close_pico();
            return BRIDGE_USB_IO_ERROR;
        }
        if (ready > 0 && (pfd.revents & POLLIN)) {
            ssize_t count = read(
                pico_fd,
                response + response_length,
                sizeof(response) - response_length - 1u);
            if (count > 0) {
                response_length += (size_t)count;
                response[response_length] = '\0';
                for (;;) {
                    char *newline = memchr(response, '\n', response_length);
                    size_t line_length;
                    if (newline == NULL) {
                        break;
                    }
                    line_length = (size_t)(newline - response);
                    if (line_length > 0u &&
                        response[line_length - 1u] == '\r') {
                        line_length--;
                    }
                    if (line_length >= 7u &&
                        memcmp(response, "SPI_OK ", 7u) == 0) {
                        size_t hex_length = line_length - 7u;
                        if (hex_length != (size_t)tx_length * 2u) {
                            return BRIDGE_PICO_ERROR;
                        }
                        for (uint16_t i = 0; i < tx_length; ++i) {
                            int high = hex_nibble(response[7u + 2u * i]);
                            int low = hex_nibble(response[8u + 2u * i]);
                            if (high < 0 || low < 0) {
                                return BRIDGE_PICO_ERROR;
                            }
                            rx[i] = (uint8_t)((high << 4) | low);
                        }
                        *rx_length = tx_length;
                        return BRIDGE_OK;
                    }
                    if (line_length >= 7u &&
                        memcmp(response, "SPI_ERR", 7u) == 0) {
                        return BRIDGE_PICO_ERROR;
                    }
                    memmove(response, newline + 1,
                            response_length - ((size_t)(newline - response) + 1u));
                    response_length -= (size_t)(newline - response) + 1u;
                    response[response_length] = '\0';
                }
                if (response_length + 1u >= sizeof(response)) {
                    return BRIDGE_PICO_ERROR;
                }
            } else if (count == 0) {
                close_pico();
                return BRIDGE_USB_IO_ERROR;
            } else if (errno != EAGAIN && errno != EWOULDBLOCK &&
                       errno != EINTR) {
                close_pico();
                return BRIDGE_USB_IO_ERROR;
            }
        }
    }
    return BRIDGE_PICO_ERROR;
}

static void service_udp(void)
{
    uint8_t request[PICO_PACKET_HEADER + PICO_MAX_BYTES];
    uint8_t response[PICO_PACKET_HEADER + PICO_MAX_BYTES];
    struct sockaddr_in peer;
    socklen_t peer_length = sizeof(peer);
    ssize_t received;
    enum bridge_status status = BRIDGE_BAD_REQUEST;
    uint8_t flags = 0u;
    uint16_t half_period_us = 0u;
    uint16_t tx_length = 0u;
    uint16_t rx_length = 0u;
    uint32_t sequence = 0u;

    received = recvfrom(udp_fd, request, sizeof(request), 0,
                        (struct sockaddr *)&peer, &peer_length);
    if (received < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
            perror("PICO-BRIDGE: recvfrom");
        }
        return;
    }

    if (received >= (ssize_t)PICO_PACKET_HEADER &&
        memcmp(request, "PSPI", 4u) == 0 &&
        request[4] == PICO_PROTOCOL_VERSION) {
        flags = request[5];
        half_period_us = get_u16_le(request + 6u);
        sequence = get_u32_le(request + 8u);
        tx_length = get_u16_le(request + 12u);
        if (tx_length != 0u && tx_length <= PICO_MAX_BYTES &&
            received == (ssize_t)(PICO_PACKET_HEADER + tx_length)) {
            status = pico_transfer(
                flags, half_period_us, request + PICO_PACKET_HEADER,
                tx_length, response + PICO_PACKET_HEADER, &rx_length);
        }
        memcpy(response, "PSPR", 4u);
    } else if (received >= (ssize_t)PICO_PACKET_HEADER &&
               memcmp(request, "PCDC", 4u) == 0 &&
               request[4] == PICO_PROTOCOL_VERSION) {
        uint8_t operation = request[5];
        uint16_t timeout_ms = get_u16_le(request + 6u);

        sequence = get_u32_le(request + 8u);
        tx_length = get_u16_le(request + 12u);
        if (operation == PCDC_PROBE &&
            tx_length == 0u &&
            received == (ssize_t)PICO_PACKET_HEADER) {
            status = BRIDGE_OK;
        } else if (operation == PCDC_WRITE &&
            tx_length != 0u && tx_length <= PICO_MAX_BYTES &&
            received == (ssize_t)(PICO_PACKET_HEADER + tx_length)) {
            status = pico_cdc_write(
                request + PICO_PACKET_HEADER, tx_length);
        } else if (operation == PCDC_READ &&
                   tx_length != 0u && tx_length <= PICO_MAX_BYTES &&
                   received == (ssize_t)PICO_PACKET_HEADER) {
            status = pico_cdc_read(
                tx_length, timeout_ms, response + PICO_PACKET_HEADER,
                &rx_length);
        } else if (operation == PCDC_FLUSH &&
                   tx_length == 0u &&
                   received == (ssize_t)PICO_PACKET_HEADER) {
            status = pico_cdc_flush();
        }
        memcpy(response, "PCDR", 4u);
    } else {
        memcpy(response, "PSPR", 4u);
    }

    response[4] = PICO_PROTOCOL_VERSION;
    response[5] = (uint8_t)status;
    put_u16_le(response + 6u, rx_length);
    put_u32_le(response + 8u, sequence);
    put_u32_le(response + 12u, 0u);
    (void)sendto(udp_fd, response, PICO_PACKET_HEADER + rx_length, 0,
                 (const struct sockaddr *)&peer, peer_length);
}

static void service_mailbox(void)
{
    uint8_t tx[PICO_MAX_BYTES];
    uint8_t rx[PICO_MAX_BYTES];
    uint32_t request = mailbox_read32(MB_REQUEST);
    uint32_t flags;
    uint32_t half_period_us;
    uint32_t tx_length;
    uint16_t rx_length = 0u;
    enum bridge_status status;

    if (request == mailbox_last_request) {
        return;
    }
    flags = mailbox_read32(MB_FLAGS);
    half_period_us = mailbox_read32(MB_HALF_PERIOD_US);
    tx_length = mailbox_read32(MB_TX_LENGTH);
    mailbox_write32(MB_STATUS, BRIDGE_BUSY);
    mailbox_write32(MB_RX_LENGTH, 0u);

    if ((flags & PICO_MODE_MASK) == PICO_MODE_SPI &&
        tx_length != 0u && tx_length <= PICO_MAX_BYTES &&
        half_period_us != 0u && half_period_us <= 100u) {
        mailbox_read_bytes(PICO_MAILBOX_DATA, tx, tx_length);
        status = pico_transfer(
            (uint8_t)flags, (uint16_t)half_period_us, tx,
            (uint16_t)tx_length, rx, &rx_length);
    } else if ((flags & PICO_MODE_MASK) == PICO_MODE_CDC_WRITE &&
               tx_length != 0u && tx_length <= PICO_MAX_BYTES) {
        mailbox_read_bytes(PICO_MAILBOX_DATA, tx, tx_length);
        status = pico_cdc_write(tx, (uint16_t)tx_length);
    } else if ((flags & PICO_MODE_MASK) == PICO_MODE_CDC_READ &&
               tx_length != 0u && tx_length <= PICO_MAX_BYTES &&
               half_period_us <= PICO_CDC_TIMEOUT_MAX_MS) {
        status = pico_cdc_read(
            (uint16_t)tx_length, (uint16_t)half_period_us, rx, &rx_length);
    } else if ((flags & PICO_MODE_MASK) == PICO_MODE_CDC_FLUSH &&
               tx_length == 0u) {
        status = pico_cdc_flush();
    } else {
        status = BRIDGE_BAD_REQUEST;
    }
    if (status == BRIDGE_OK) {
        mailbox_write_bytes(PICO_MAILBOX_DATA, rx, rx_length);
    }
    mailbox_write32(MB_RX_LENGTH, rx_length);
    mailbox_write32(MB_STATUS, (uint32_t)status);
    mailbox_write32(MB_DONE, request);
    mailbox_last_request = request;
}

static void cleanup(void)
{
    close_pico();
    if (udp_fd >= 0) {
        close(udp_fd);
    }
    if (mailbox_mapping != NULL) {
        munmap(mailbox_mapping, mailbox_mapping_bytes);
    }
    if (mem_fd >= 0) {
        close(mem_fd);
    }
}

int main(void)
{
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    if (map_mailbox() != 0 || setup_udp() != 0) {
        cleanup();
        return 1;
    }
    printf("PICO-BRIDGE: ready on UDP 0.0.0.0:%u and MB mailbox 0x%08X\n",
           PICO_UDP_PORT, PICO_MAILBOX_BASE);

    while (keep_running) {
        service_udp();
        service_mailbox();
        usleep(100u);
    }
    cleanup();
    return 0;
}
