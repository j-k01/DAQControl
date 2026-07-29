#include "pico/unique_id.h"
#include "pico/usb_reset.h"
#include "tusb.h"

#include <stdbool.h>

extern volatile bool pico_spi_complete;
extern volatile bool pico_spi_pass;

enum {
    ITF_CDC = 0,
    ITF_RESET = 2,
    ITF_COUNT = 3,
};

enum {
    STR_LANG = 0,
    STR_MANUFACTURER,
    STR_PRODUCT,
    STR_SERIAL,
    STR_CDC,
};

#define CONFIG_TOTAL_LEN \
    (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_RPI_RESET_DESC_LEN)

static const tusb_desc_device_t device_descriptor = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = 0x2e8a,
    .idProduct = 0x0009,
    .bcdDevice = 0x0100,
    .iManufacturer = STR_MANUFACTURER,
    .iProduct = STR_PRODUCT,
    .iSerialNumber = STR_SERIAL,
    .bNumConfigurations = 1,
};

static const uint8_t configuration_descriptor[] = {
    TUD_CONFIG_DESCRIPTOR(
        1, ITF_COUNT, 0, CONFIG_TOTAL_LEN, 0, 250),
    TUD_CDC_DESCRIPTOR(
        ITF_CDC, STR_CDC, 0x81, 8, 0x02, 0x82, 64),
    TUD_RPI_RESET_DESCRIPTOR(ITF_RESET, 0),
};

static char serial_string[PICO_UNIQUE_BOARD_ID_SIZE_BYTES * 2 + 1];

const uint8_t *tud_descriptor_device_cb(void) {
    return (const uint8_t *)&device_descriptor;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return configuration_descriptor;
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    static uint16_t descriptor[32];
    const char *text;
    uint8_t length;

    (void)langid;
    if (index == STR_LANG) {
        descriptor[1] = 0x0409;
        length = 1;
    } else {
        switch (index) {
        case STR_MANUFACTURER:
            text = "Raspberry Pi";
            break;
        case STR_PRODUCT:
            text = !pico_spi_complete
                       ? "PICO2_USB_SPI_PENDING"
                       : (pico_spi_pass ? "PICO2_USB_SPI_PASS"
                                        : "PICO2_USB_SPI_FAIL");
            break;
        case STR_SERIAL:
            if (!serial_string[0]) {
                pico_get_unique_board_id_string(
                    serial_string, sizeof(serial_string));
            }
            text = serial_string;
            break;
        case STR_CDC:
            text = "Board CDC";
            break;
        default:
            return NULL;
        }

        for (length = 0;
             length < 31 && text[length];
             ++length) {
            descriptor[length + 1] = text[length];
        }
    }

    descriptor[0] =
        (uint16_t)((TUSB_DESC_STRING << 8) | (2 * length + 2));
    return descriptor;
}
