#include "pico/stdlib.h"
#include "pico/multicore.h"

#include <stdio.h>
#include <string.h>

/*
 * The jumper groups are used as a software SPI mode-0 link:
 *
 *   master GP6  SCK  -> GP10 slave SCK
 *   master GP7  MOSI -> GP11 slave MOSI
 *   master GP8  MISO <- GP12 slave MISO
 *   master GP9  CS   -> GP13 slave CS
 *
 * These are deliberate GPIO assignments. The RP2350 hardware SPI pin mux
 * does not map the two four-pin groups to SPI0 and SPI1 in this arrangement.
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

static const uint8_t master_tx[] = {
    0x00, 0xff, 0xa5, 0x5a, 0x3c, 0xc3, 0x96, 0x69,
};
static const uint8_t slave_tx[] = {
    0x7e, 0x81, 0x12, 0x34, 0xde, 0xad, 0xbe, 0xef,
};
static uint8_t slave_rx[sizeof(master_tx)];
volatile bool pico_spi_complete;
volatile bool pico_spi_pass;

#define SLAVE_READY 0x53504952u
#define SLAVE_PASS  0x53504950u
#define SLAVE_FAIL  0x53504946u

static bool fifo_pop_until(uint32_t *value, uint32_t timeout_ms) {
    absolute_time_t deadline = make_timeout_time_ms(timeout_ms);

    while (!multicore_fifo_rvalid()) {
        if (time_reached(deadline)) {
            return false;
        }
        tight_loop_contents();
    }
    *value = multicore_fifo_pop_blocking();
    return true;
}

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

    multicore_fifo_push_blocking(SLAVE_READY);
    (void)multicore_fifo_pop_blocking();

    uint64_t deadline = time_us_64() + 1000000;
    bool ok = wait_gpio(SLAVE_CS, false, deadline);

    for (size_t byte_index = 0; ok && byte_index < sizeof(master_tx);
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
    if (ok) {
        ok = memcmp(slave_rx, master_tx, sizeof(master_tx)) == 0;
    }
    multicore_fifo_push_blocking(ok ? SLAVE_PASS : SLAVE_FAIL);

    while (true) {
        tight_loop_contents();
    }
}

static bool run_software_spi_loopback(void) {
    uint8_t master_rx[sizeof(slave_tx)] = {0};
    uint32_t fifo_value;

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

    multicore_launch_core1(spi_slave_core);
    if (!fifo_pop_until(&fifo_value, 1000) || fifo_value != SLAVE_READY) {
        return false;
    }

    multicore_fifo_push_blocking(1);
    sleep_us(20);
    gpio_put(MASTER_CS, 0);
    sleep_us(20);

    for (size_t byte_index = 0; byte_index < sizeof(master_tx);
         ++byte_index) {
        uint8_t received = 0;
        for (int bit = 7; bit >= 0; --bit) {
            gpio_put(MASTER_MOSI, (master_tx[byte_index] >> bit) & 1u);
            sleep_us(5);
            gpio_put(MASTER_SCK, 1);
            sleep_us(5);
            received = (uint8_t)((received << 1) |
                                 (gpio_get(MASTER_MISO) ? 1u : 0u));
            gpio_put(MASTER_SCK, 0);
            sleep_us(5);
        }
        master_rx[byte_index] = received;
    }

    gpio_put(MASTER_CS, 1);
    if (!fifo_pop_until(&fifo_value, 2000)) {
        return false;
    }
    return fifo_value == SLAVE_PASS &&
           memcmp(master_rx, slave_tx, sizeof(slave_tx)) == 0;
}

int main(void) {
    const uint led_pin = PICO_DEFAULT_LED_PIN;

    gpio_init(led_pin);
    gpio_set_dir(led_pin, GPIO_OUT);
    gpio_put(led_pin, 1);

    pico_spi_pass = run_software_spi_loopback();
    pico_spi_complete = true;
    stdio_init_all();

    printf("PICO2_USB_SPI_%s tx_bytes=%u rx_bytes=%u\r\n",
           pico_spi_pass ? "PASS" : "FAIL",
           (unsigned)sizeof(master_tx),
           (unsigned)sizeof(slave_tx));
    stdio_flush();
    absolute_time_t next_report = get_absolute_time();

    while (true) {
        gpio_put(led_pin, 1);
        sleep_ms(pico_spi_pass ? 250 : 75);
        gpio_put(led_pin, 0);
        sleep_ms(pico_spi_pass ? 250 : 75);

        if (absolute_time_diff_us(get_absolute_time(), next_report) <= 0) {
            printf("PICO2_USB_SPI_%s tx_bytes=%u rx_bytes=%u\r\n",
                   pico_spi_pass ? "PASS" : "FAIL",
                   (unsigned)sizeof(master_tx),
                   (unsigned)sizeof(slave_tx));
            stdio_flush();
            next_report = make_timeout_time_ms(1000);
        }
    }
}
