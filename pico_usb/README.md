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
7. preserves the firmware already installed in Pico flash;
8. starts the Pico CDC bridge on UDP and the MicroBlaze mailbox path; and
9. verifies DAQ Ethernet `PING`/`PONG` plus the production Pico
   `HANDSHAKE`/`UID:PICO-002` protocol.

Routine FPGA loading never enters Pico BOOTSEL and never writes Pico flash.
Program `pico2_daq_pico002.uf2` once from the PC (or with an explicit recovery
procedure), then reconnect the Pico to ZCU102 J96. The Pico firmware persists
across FPGA reloads and board power cycles.

The ZCU102 kernel, initramfs, and boot firmware are loaded into DDR only.
Power-cycling the ZCU102 removes that runtime, so rerun the loader after a
board power cycle.

## Hardware

- Configure J96 for USB host power and connect it to the Pico 2 USB connector.
The following jumper wiring is only for the explicit SPI-loopback diagnostic;
it is not used by the production PyDAQ/DAC connection:

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

For production, connect the Pico's normal SPI0 DAC pins and chip-selects to the
AD5370 boards. Programming the FPGA changes neither those Pico pins nor the
firmware stored in Pico flash.

## Run

From the repository root:

Do not run program_board.ps1 first; this command replaces it for a Pico-enabled session.

```powershell
uv run python pico_usb\load_and_test.py --local-jtag --port COM9
```

The directly connected PC Ethernet adapter must have `192.168.2.1/24`;
`--local-ip` and `--board-ip` override the defaults. The loader leaves the
normal MicroBlaze console on COM10 and Linux on COM9.

With --local-jtag, the loader discovers the newest local Xilinx XSDB/XSCT
installation and uses the JTAG cable attached to the target PC. Pass --xsdb
only when automatic discovery is insufficient. Omit --local-jtag only when
the JTAG cable is intentionally attached to a remote host; that mode defaults
to jkincaid@capitolpeak.ece.ucdavis.edu and accepts --remote/--identity.
The local Python environment needs pyserial.

The command programs the ZCU102 over its selected JTAG connection, prints the
PS UART transcript locally, and leaves Pico flash untouched. It
exits successfully only after Ethernet and the production Pico handshake pass:

```text
PASS: MicroBlaze is running, Linux DAQ Ethernet answered PING, and the preserved PICO-002 firmware completed its CDC handshake.
```

Enumeration alone is not treated as success.
After the unified runtime is loaded, the same non-destructive bridge check used
by the GUI is available without PyDAQ or heater writes:

~~~powershell
uv run python scripts\test_pico_bridge.py
~~~

Port 5006 is the independent ADC/DAQ service. A successful capture or PING on
5006 does not imply that the Pico CDC bridge on port 5007 is running.

## Use an existing Pico controller

The normal interface is a pyserial-shaped transport. Existing controller code
keeps its Pico messages and read/write logic; change only the imported serial
namespace:

```python
# Before, with the Pico connected directly to the PC:
# import serial

# After, with the Pico connected to ZCU102 J96:
import fpga_pico_serial as serial

with serial.Serial(EXISTING_PICO_PORT, EXISTING_BAUD, timeout=1) as pico:
    pico.write(b"the existing Pico message\n")
    reply = pico.readline()
```

Run the controller with the repository root on Python's import path, then use
`import fpga_pico_serial as serial`. `Serial.write`, `read`, `readline`,
`read_until`, `read_all`, `flush`, `reset_input_buffer`, context management,
and `in_waiting` are implemented. Message bytes are forwarded unchanged.
The old `port` and `baudrate` arguments are accepted for source compatibility;
the FPGA UART fallback independently uses COM10 at 115200. Override that with
`fpga_uart_port="COMx"` or the `FPGA_PICO_UART_PORT` environment variable when
Windows assigns the MicroBlaze console a different name.

The default `transport="auto"` probes board UDP port 5007 once when the object
is opened and selects Ethernet when it responds; otherwise it opens the
MicroBlaze console on COM10. The selected path is available as
`pico.transport`. Pass `transport="ethernet"` or `"uart"` to force one.
Selecting once at open prevents an ambiguous Ethernet timeout from duplicating
a write over UART.

Both paths terminate in one Linux service, so Linux remains the only owner of
the ZynqMP USB controller. Ethernet link failure does not prevent the COM10
path from forwarding USB CDC bytes.

### Use PyDAQ without modifying it

The Pico must already be programmed with its normal `pico2_daq` firmware. The
adapter below transports that firmware's existing serial protocol through the
FPGA; it does not flash or replace Pico firmware.

Install the FPGA transport before importing a configuration module that calls
`pydaq.ser.find_boards()` or `config_detected_devices()`:

```python
from pydaq_fpga_transport import install

install(
    board_ip="192.168.2.10",
    local_ip="192.168.2.1",
    transport="ethernet",
)

from crossbar_config import netlist
```

`pydaq_fpga_transport.py` presents one virtual `FPGA-PICO` port only inside
`pydaq.ser`. PyDAQ's normal `HANDSHAKE`, `UID`, board-definition, `Netlist`, and
AD5370 write behavior is unchanged. Process-wide pyserial is not patched, so
the DAQ GUI can continue using its ordinary FPGA UART connection. Ethernet is
forced in the example because the GUI may already own COM10.

The configuration's `BoardManager` UID must match the firmware running on the
Pico. The included `scripts/mzi_pydaq_config.py` expects `PICO-002` and exposes
all 54 crossbar heater nets as `h_<row>_<column>`.

The GUI's **Optical Weight** tab uses this adapter to sweep one heater while the FPGA
generates the periodic DAC waveform and performs trigger-aligned Ethernet ADC
captures. It aligns each repetition in software, plots forward and reverse
weight curves, reports extinction, and saves raw captures plus measurements as
a compressed NPZ file.

Current PyDAQ requires Python 3.11 or newer. This repository is a `uv` project;
its `pyproject.toml` includes both the GUI dependencies and PyDAQ's Git source:

```powershell
cd C:\path\to\DAQControl
uv sync
uv run python scripts\dac_scope_qt.py --port COM10
```

No PyDAQ source changes are required. The Pico remains programmed with its
normal firmware; the FPGA bridge only transports the same serial protocol.

## Low-level SPI diagnostic

The explicit diagnostic Pico firmware uses the four jumper wires as a self-checking SPI
loopback and returns each transmitted byte XOR `0xA5`. The client verifies
that result. Add `--external` to drive GP6 (SCK), GP7 (MOSI), GP8 (MISO), and
GP9 (CS) as an ordinary SPI mode-0 master without the internal loopback
slave. Transfers are 1–128 bytes and `--half-period-us` selects 1–100 us.

The direct MicroBlaze console form is:

```text
PSPI 00ffa55a 5
PSPI 00ffa55a 5 external
```

The corresponding diagnostic client is:

```powershell
python scripts\pico_spi_bridge.py --transport both --tx 00ffa55a
```

To exercise the complete BRST/BRDO/DAQS path without depending on the external
ADC clocks, run:

```powershell
python scripts\test_unified_linux_readout.py
```

That test writes known words into the reserved DAQ DDR buffers, uses the real
MicroBlaze burst-ready command, and requires byte-exact chip 0 and chip 1 UDP
payloads.

## Ethernet recovery

Use the single-line recovery entry point:

```powershell
recover_ethernet.cmd
```

Its bounded sequence is: verify/configure the selected host NIC, refresh that
NIC if needed, restart only `daq-eth-service` through the Linux COM9 shell, and
run the complete unified loader only if Linux itself is unavailable. It never
loads the retired bare-metal A53 Ethernet ELF, so recovery preserves Pico USB,
the MicroBlaze firmware, and XBar state whenever a service-only restart is
sufficient.

Read-only diagnosis remains available as:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\diagnose_board_ethernet.ps1
```

## Contents

- `load_and_test.py` is the one-line JTAG/UART orchestrator.
- `pico2_test/` is the RAM-only Pico SDK application.
- `linux/` contains the minimal Linux updater, UDP readout service, and DDR
  diagnostic utility.
- `uboot/picoboot.c` is the earlier bare-metal diagnostic command retained for
  reference; the tested normal path uses Linux's standard USB stack.
- `prebuilt/` contains the exact tested binaries and SHA-256 manifest.
