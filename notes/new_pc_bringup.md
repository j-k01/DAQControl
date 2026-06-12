# New PC bring-up: clone, load the board, DAC0 pulse train + ADC IN1 capture

Everything needed to load the board lives in the repo: the tracked bitstream
`project/DAQ_LAUNCH.runs/impl_1/top.bit` and the tracked prebuilt MicroBlaze
firmware `prebuilt/firmware.elf`. No Vivado/Vitis build is required unless you
change HDL or firmware.

## Prerequisites

- Vivado 2024.1 **and** Vitis 2024.1 (`program_and_load.tcl` uses Vivado for
  the bitstream and XSCT for the ELF). On Windows, enable the cable-driver
  install option so USB-JTAG works.
- Silicon Labs CP2108 VCP driver for the ZCU102 USB-UART.
- Python 3.9+ with: `python -m pip install pyserial numpy matplotlib`
- Hardware: ZCU102 (boot mode switches = JTAG) + FMC-ADC500 on HPC0/J5,
  USB-JTAG and USB-UART cabled to this PC, DAC0 SSMC output cabled to ADC IN1
  for loopback captures.

## One-time

```powershell
git clone https://github.com/j-k01/DAQControl.git
cd DAQControl
git checkout zcu102-hpc1-launch
```

## After every board power-cycle

The PL bitstream and MicroBlaze BRAM are volatile. Reload both in one JTAG
pass (run from a "Vivado 2024.1 Tcl Shell" or any shell where `vivado.bat`
and `xsct.bat` are on PATH):

```powershell
vivado.bat -mode batch -source program_and_load.tcl
```

- Falls back to `prebuilt/firmware.elf` automatically when no Vitis workspace
  exists (fresh clone).
- The "debug probes file not found" warning is benign; `hw/DAQ_LAUNCH.ltx` is
  only needed for ILA debugging and comes from a local `build.tcl` run.

## Find the UART port

```powershell
powershell -ExecutionPolicy Bypass -File scripts/list_zcu102_uart_ports.ps1
```

Use the CP2108 interface whose hardware ID contains `MI_02` (channel 3 /
`MI_03` is the MSP430 system controller, not this design). Sanity check:

```powershell
python scripts/uart_cmds.py --port COMx STAT
```

`STAT` should decode `litejesd_active litejesd_ready` and `no_dac_alarm`.

## DAC0 trapezoid pulse train + ADC IN1 capture (one command)

```powershell
python scripts/trap_dac0_adc_in1_uart.py --port COMx
```

Defaults program the 35 ns spike train (7 ns IZH-profile trapezoid + 28 ns
gap, amplitude 0x6000) into the DAC0 BRAM with a seamless loop, select AC
coupling on IN1, PCAP-capture 4096 frames (16384 IN1 samples at 1 GS/s,
~16.4 us; the ADS54J60s run LMFS=4211 on 10G lanes, 4 samples per 250 MHz
JESD beat), write `captures/trap35ns_in1.{csv,png}` plus a summary, and
leave the DAC0 train free-running.

Knobs:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--period-ns` / `--pulse-width-ns` | 35 / 7 | any whole-sample combo at 1 GSPS |
| `--amplitude` / `--offset` | `0x6000` / `0x0400` | signed DAC counts; default baseline keeps the program purely positive |
| `--coupling ac\|dc` | `ac` | ADC IN1 input coupling (DC only with known-safe amplitude) |
| `--frames` | 4096 | capture length, 4 IN1 samples per frame |
| `--reinit-adc` | off | pulse ADS54J60 auto-init restart first |
| `--expect-build-id 0xDA01003C` | off | fail fast on gateware mismatch |

Pure sine + capture: `python scripts/sine_dac0_adc_in1_uart.py --port COMx --frequency-mhz 10`
(frequency quantized to a seamless BRAM loop; programmed value is printed).
DAC-only (no capture): `python scripts/program_dac0_trap_pulse_uart.py --port COMx`
Capture-only / other waveforms: `scripts/capture_plot_adc_uart.py` (see README).

Expect the captured train to ride around zero mean: both the DAC output and
the ADC input are AC-coupled on the card.

## If JTAG stays on a remote Linux host (current lab setup: capitolpeak)

Commit/push from the PC, then on the host:

```bash
cd ~/DAQControl && git pull --ff-only
~/bin/with_xilinx_2024_1 vivado -mode batch -source program_and_load.tcl
```

UART/Python scripts still run on whichever PC has the USB-UART.

## Rebuilding from source

See `README.md` ("Bitstream Build"): `create_project.tcl` -> `build.tcl`
(with `--with-bram-dataplane` for this dataplane) -> `xsct.bat build_sw.tcl`.
After a firmware rebuild, refresh `prebuilt/firmware.elf` from
`sw/workspace/firmware/Debug/firmware.elf` if you want fresh clones to keep
working without Vitis.
