#include "pico/multicore.h"
#include "pico/stdlib.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Software SPI mode-0 wiring:
 *
 *   master GP6  SCK  -> GP10 slave SCK
 *   master GP7  MOSI -> GP11 slave MOSI
 *   master GP8  MISO <- GP12 slave MISO
 *   master GP9  CS   -> GP13 slave CS
 *
 * USB RPC can also run the master without the internal slave verification,
 * allowing GP6..GP9 to control an external SPI target after the loopback
 * jumpers are removed.
 */
enum {
    MASTER_SCK = 6,
    MASTER_MOSI = 7,
    MASTER_MISO = 8,
    MASTER_CS = 9,
    SLAVE_SCK = 10,
    SLAVE_MOSI = 11,
    SLAVE_MISO = 12,
    SLAVE_CS = 13,
};

enum {
    SPI_FLAG_LOOPBACK_VERIFY = 1u,
    SPI_MAX_BYTES = 128u,
    SPI_LINE_BYTES = 2u * SPI_MAX_BYTES + 48u,
};

enum slave_state {
    SLAVE_IDLE = 0,
    SLAVE_REQUEST,
    SLAVE_READY,
    SLAVE_DONE,
    SLAVE_FAILED,
};

static volatile enum slave_state slave_state;
static volatile uint32_t slave_length;
static volatile uint32_t slave_half_period_us;
static volatile uint8_t slave_tx[SPI_MAX_BYTES];
static volatile uint8_t slave_rx[SPI_MAX_BYTES];

volatile bool pico_spi_complete;
volatile bool pico_spi_pass;

static bool wait_gpio(uint pin, bool level, uint64_t deadline_us) {
    while (gpio_get(pin) != level) {
        if (time_us_64() >= deadline_us) {
            return false;
        }
        tight_loop_contents();
    }
    return true;
}

static void spi_slave_core(void) {
    gpio_init(SLAVE_SCK);
    gpio_set_dir(SLAVE_SCK, GPIO_IN);
    gpio_init(SLAVE_MOSI);
    gpio_set_dir(SLAVE_MOSI, GPIO_IN);
    gpio_init(SLAVE_MISO);
    gpio_set_dir(SLAVE_MISO, GPIO_OUT);
    gpio_put(SLAVE_MISO, 0);
    gpio_init(SLAVE_CS);
    gpio_set_dir(SLAVE_CS, GPIO_IN);

    while (true) {
        uint32_t length;
        uint32_t half_period;
        uint64_t deadline;
        bool ok;

        if (slave_state != SLAVE_REQUEST) {
            tight_loop_contents();
            continue;
        }
        length = slave_length;
        half_period = slave_half_period_us;
        __compiler_memory_barrier();
        slave_state = SLAVE_READY;

        deadline = time_us_64() +
                   100000u + (uint64_t)length * half_period * 32u;
        ok = wait_gpio(SLAVE_CS, false, deadline);
        for (uint32_t byte_index = 0;
             ok && byte_index < length;
             ++byte_index) {
            uint8_t received = 0;
            uint8_t response = slave_tx[byte_index];

            for (int bit = 7; bit >= 0; --bit) {
                gpio_put(SLAVE_MISO, (response >> bit) & 1u);
                ok = wait_gpio(SLAVE_SCK, true, deadline);
                if (!ok) {
                    break;
                }
                received = (uint8_t)((received << 1) |
                                     (gpio_get(SLAVE_MOSI) ? 1u : 0u));
                ok = wait_gpio(SLAVE_SCK, false, deadline);
                if (!ok) {
                    break;
                }
            }
            slave_rx[byte_index] = received;
        }
        if (ok) {
            ok = wait_gpio(SLAVE_CS, true, deadline);
        }
        __compiler_memory_barrier();
        slave_state = ok ? SLAVE_DONE : SLAVE_FAILED;
    }
}

static void spi_master_init(void) {
    gpio_init(MASTER_SCK);
    gpio_set_dir(MASTER_SCK, GPIO_OUT);
    gpio_put(MASTER_SCK, 0);
    gpio_init(MASTER_MOSI);
    gpio_set_dir(MASTER_MOSI, GPIO_OUT);
    gpio_put(MASTER_MOSI, 0);
    gpio_init(MASTER_MISO);
    gpio_set_dir(MASTER_MISO, GPIO_IN);
    gpio_init(MASTER_CS);
    gpio_set_dir(MASTER_CS, GPIO_OUT);
    gpio_put(MASTER_CS, 1);
}

static bool wait_slave_state(enum slave_state expected, uint32_t timeout_ms) {
    absolute_time_t deadline = make_timeout_time_ms(timeout_ms);
    while (slave_state != expected) {
        if (slave_state == SLAVE_FAILED || time_reached(deadline)) {
            return false;
        }
        tight_loop_contents();
    }
    return true;
}

static bool spi_transfer(
    const uint8_t *tx,
    uint8_t *rx,
    uint32_t length,
    uint32_t half_period_us,
    uint32_t flags
) {
    bool loopback = (flags & SPI_FLAG_LOOPBACK_VERIFY) != 0u;

    if (length == 0u || length > SPI_MAX_BYTES ||
        half_period_us == 0u || half_period_us > 100u) {
        return false;
    }

    if (loopback) {
        if (slave_state != SLAVE_IDLE && slave_state != SLAVE_DONE &&
            slave_state != SLAVE_FAILED) {
            return false;
        }
        for (uint32_t i = 0; i < length; ++i) {
            slave_tx[i] = (uint8_t)(tx[i] ^ 0xA5u);
            slave_rx[i] = 0u;
        }
        slave_length = length;
        slave_half_period_us = half_period_us;
        __compiler_memory_barrier();
        slave_state = SLAVE_REQUEST;
        if (!wait_slave_state(SLAVE_READY, 1000u)) {
            slave_state = SLAVE_IDLE;
            return false;
        }
    }

    sleep_us(2u * half_period_us);
    gpio_put(MASTER_CS, 0);
    sleep_us(2u * half_period_us);
    for (uint32_t byte_index = 0; byte_index < length; ++byte_index) {
        uint8_t received = 0;
        for (int bit = 7; bit >= 0; --bit) {
            gpio_put(MASTER_MOSI, (tx[byte_index] >> bit) & 1u);
            sleep_us(half_period_us);
            gpio_put(MASTER_SCK, 1);
            sleep_us(half_period_us);
            received = (uint8_t)((received << 1) |
                                 (gpio_get(MASTER_MISO) ? 1u : 0u));
            gpio_put(MASTER_SCK, 0);
        }
        rx[byte_index] = received;
    }
    gpio_put(MASTER_CS, 1);
    gpio_put(MASTER_MOSI, 0);

    if (loopback) {
        bool ok = wait_slave_state(SLAVE_DONE, 2000u);
        for (uint32_t i = 0; ok && i < length; ++i) {
            if (slave_rx[i] != tx[i] ||
                rx[i] != (uint8_t)(tx[i] ^ 0xA5u)) {
                ok = false;
            }
        }
        slave_state = SLAVE_IDLE;
        return ok;
    }
    return true;
}

static int hex_nibble(char c) {
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

static bool decode_hex(
    const char *text,
    uint8_t *data,
    uint32_t *length
) {
    uint32_t count = 0;
    while (text[0] && text[0] != ' ' && text[0] != '\r' &&
           text[0] != '\n') {
        int high;
        int low;
        if (!text[1] || count >= SPI_MAX_BYTES) {
            return false;
        }
        high = hex_nibble(text[0]);
        low = hex_nibble(text[1]);
        if (high < 0 || low < 0) {
            return false;
        }
        data[count++] = (uint8_t)((high << 4) | low);
        text += 2;
    }
    *length = count;
    return count != 0u;
}

static void print_hex(const uint8_t *data, uint32_t length) {
    static const char digits[] = "0123456789ABCDEF";
    for (uint32_t i = 0; i < length; ++i) {
        putchar(digits[data[i] >> 4]);
        putchar(digits[data[i] & 0x0Fu]);
    }
}

static void process_rpc_line(char *line) {
    uint8_t tx[SPI_MAX_BYTES];
    uint8_t rx[SPI_MAX_BYTES];
    uint32_t flags;
    uint32_t half_period_us;
    uint32_t length;
    char *p = line;
    char *end;

    if (strncmp(p, "SPI ", 4) != 0) {
        printf("SPI_ERR COMMAND\r\n");
        return;
    }
    p += 4;
    flags = (uint32_t)strtoul(p, &end, 0);
    if (end == p || *end != ' ') {
        printf("SPI_ERR FLAGS\r\n");
        return;
    }
    p = end + 1;
    half_period_us = (uint32_t)strtoul(p, &end, 0);
    if (end == p || *end != ' ') {
        printf("SPI_ERR PERIOD\r\n");
        return;
    }
    p = end + 1;
    if (!decode_hex(p, tx, &length)) {
        printf("SPI_ERR DATA\r\n");
        return;
    }
    if (!spi_transfer(tx, rx, length, half_period_us, flags)) {
        printf("SPI_ERR TRANSFER\r\n");
        return;
    }
    printf("SPI_OK ");
    print_hex(rx, length);
    printf("\r\n");
}

static bool run_initial_loopback(void) {
    static const uint8_t tx[] = {
        0x00, 0xff, 0xa5, 0x5a, 0x3c, 0xc3, 0x96, 0x69,
    };
    uint8_t rx[sizeof(tx)];
    return spi_transfer(
        tx, rx, sizeof(tx), 5u, SPI_FLAG_LOOPBACK_VERIFY
    );
}

int main(void) {
    const uint led_pin = PICO_DEFAULT_LED_PIN;
    char line[SPI_LINE_BYTES];
    uint32_t line_length = 0;
    absolute_time_t next_led;
    bool led_on = true;

    gpio_init(led_pin);
    gpio_set_dir(led_pin, GPIO_OUT);
    gpio_put(led_pin, 1);
    spi_master_init();
    slave_state = SLAVE_IDLE;
    multicore_launch_core1(spi_slave_core);

    pico_spi_pass = run_initial_loopback();
    pico_spi_complete = true;
    stdio_init_all();
    printf("PICO2_USB_SPI_%s rpc=1 max_bytes=%u\r\n",
           pico_spi_pass ? "PASS" : "FAIL",
           (unsigned)SPI_MAX_BYTES);
    stdio_flush();
    next_led = make_timeout_time_ms(pico_spi_pass ? 250u : 75u);

    while (true) {
        int c = getchar_timeout_us(0);
        if (c != PICO_ERROR_TIMEOUT) {
            if (c == '\r' || c == '\n') {
                if (line_length != 0u) {
                    line[line_length] = '\0';
                    process_rpc_line(line);
                    stdio_flush();
                    line_length = 0u;
                }
            } else if (line_length + 1u < sizeof(line)) {
                line[line_length++] = (char)c;
            } else {
                line_length = 0u;
                printf("SPI_ERR LINE_TOO_LONG\r\n");
                stdio_flush();
            }
        }

        if (time_reached(next_led)) {
            led_on = !led_on;
            gpio_put(led_pin, led_on);
            next_led = make_timeout_time_ms(pico_spi_pass ? 250u : 75u);
        }
        tight_loop_contents();
    }
}
