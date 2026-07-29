# ZCU102 J96 to Raspberry Pi Pico 2

This is the tested unified runtime in which the ZCU102 processing system
controls a Pico 2 as a USB host through J96 while the existing DAQ
MicroBlaze/UART/XBar control and UDP Ethernet readout remain active. One
command:

1. restores the tracked DAQ bitstream;
2. starts the project FSBL and PMU firmware;
3. enters U-Boot through a matching ARM Trusted Firmware (BL31) handoff;
4. starts the existing DAQ MicroBlaze firmware;
5. boots a minimal Linux USB-host and DAQ Ethernet environment from DDR;
6. brings up `eth0` as `192.168.2.10/24` and serves the established UDP
   protocol on port 5006;
7. requests RP2350 BOOTSEL through the Pico CDC 1200-baud reset mechanism;
8. copies a RAM-only Pico application through USB mass storage;
9. runs a full-duplex software-SPI test through the four jumper wires; and
10. verifies both `PICO2_USB_SPI_PASS` and an Ethernet `PING`/`PONG`.

The successful hardware run started from the older Pico CDC firmware, entered
BOOTSEL over USB without touching the button, loaded the application, and
observed `PICO2_USB_SPI_PASS`.

Nothing is written to Pico flash or ZCU102 nonvolatile storage. The kernel,
initramfs, boot firmware, and Pico application are loaded into RAM.
Power-cycling the Pico restores its prior flash firmware. Power-cycling the
ZCU102 removes this runtime, so rerun the loader after a board power cycle.

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

The directly connected PC Ethernet adapter must have `192.168.2.1/24`;
`--local-ip` and `--board-ip` override the defaults. The loader leaves the
normal MicroBlaze console on COM10 and Linux on COM9.

Defaults target `jkincaid@capitolpeak.ece.ucdavis.edu` and the SSH key
`~/.ssh/capitolpeak_auto`. Use `--remote` or `--identity` to override them.
The local Python environment needs `pyserial`.

The command programs the ZCU102 over its JTAG connection on capitolpeak and
prints the PS UART transcript locally. It exits successfully only after Pico
USB/SPI verification and a real UDP response:

```text
PASS: MicroBlaze is running, Pico USB/SPI passed, and Linux DAQ Ethernet answered PING.
```

Enumeration alone is not treated as success.

To exercise the complete BRST/BRDO/DAQS path without depending on the external
ADC clocks, run:

```powershell
python scripts\test_unified_linux_readout.py
```

That test writes known words into the reserved DAQ DDR buffers, uses the real
MicroBlaze burst-ready command, and requires byte-exact chip 0 and chip 1 UDP
payloads.

## Contents

- `load_and_test.py` is the one-line JTAG/UART orchestrator.
- `pico2_test/` is the RAM-only Pico SDK application.
- `linux/` contains the minimal Linux updater, UDP readout service, and DDR
  diagnostic utility.
- `uboot/picoboot.c` is the earlier bare-metal diagnostic command retained for
  reference; the tested normal path uses Linux's standard USB stack.
- `prebuilt/` contains the exact tested binaries and SHA-256 manifest.
