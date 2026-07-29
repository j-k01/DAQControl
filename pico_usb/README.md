# ZCU102 J96 to Raspberry Pi Pico 2

This is the tested, button-free proof that the ZCU102 processing system can
control a Pico 2 as a USB host through J96. One command:

1. restores the tracked DAQ bitstream;
2. starts the project FSBL and PMU firmware;
3. enters U-Boot through a matching ARM Trusted Firmware (BL31) handoff;
4. boots a minimal Linux USB-host environment entirely from DDR;
5. requests RP2350 BOOTSEL through the Pico CDC 1200-baud reset mechanism;
6. copies a RAM-only Pico application through USB mass storage;
7. runs a full-duplex software-SPI test through the four jumper wires; and
8. requires the exact `PICO2_USB_SPI_PASS` USB product identity.

The successful hardware run started from the older Pico CDC firmware, entered
BOOTSEL over USB without touching the button, loaded the application, and
observed `PICO2_USB_SPI_PASS`.

Nothing is written to Pico flash. The kernel, initramfs, boot firmware, and Pico
application are all loaded into RAM. Power-cycling the Pico restores its prior
flash firmware. Running the normal DAQ programming flow restores the ZCU102 A53
software.

## Hardware

- Configure J96 for USB host power and connect it to the Pico 2 USB connector.
- Connect the loopback wires:

| Master | Slave | Function |
|---|---|---|
| GP6 | GP10 | SCK |
| GP7 | GP11 | MOSI |
| GP8 | GP12 | MISO |
| GP9 | GP13 | CS |

The RP2350 hardware pin mux does not map those exact pin groups to SPI0 and
SPI1. The test therefore implements SPI mode 0 in software on the Pico's two
cores. Both sides transmit different eight-byte patterns and verify every
received byte.

BOOTSEL does not need to be pressed. The minimal Linux host uses the standard
CDC 1200-baud reset path. The loaded test application also includes both the
1200-baud and Raspberry Pi vendor reset interfaces for later USB-only reloads.

## Run

From the repository root:

```powershell
python pico_usb\load_and_test.py --port COM9
```

Defaults target `jkincaid@capitolpeak.ece.ucdavis.edu` and the SSH key
`~/.ssh/capitolpeak_auto`. Use `--remote` or `--identity` to override them.
The local Python environment needs `pyserial`.

The command programs the ZCU102 over its JTAG connection on capitolpeak and
prints the PS UART transcript locally. It exits successfully only after this
message:

```text
PICO-HOST: PASS - USB update, Pico execution, and SPI loopback verified
```

Enumeration alone is not treated as success.

## Contents

- `load_and_test.py` is the one-line JTAG/UART orchestrator.
- `pico2_test/` is the RAM-only Pico SDK application.
- `linux/` records the minimal Linux updater logic and build provenance.
- `uboot/picoboot.c` is the earlier bare-metal diagnostic command retained for
  reference; the tested normal path uses Linux's standard USB stack.
- `prebuilt/` contains the exact tested binaries and SHA-256 manifest.
