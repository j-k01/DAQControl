# DAQ_LAUNCH

Minimal VC709 launch design for the FMC-ADC500-CD / SE120 DAQ card.

This design intentionally does not instantiate JESD204 or GTH data channels.
It proves the board-health layer first:

- VC709 fabric heartbeat.
- FMC present and power-good sideband.
- DAQ `CLK_FMC`, `SYSREF_FMC`, `GBTCLK0`, and `GBTCLK1` activity counters.
- HMC7044 reset and manual SPI pins.
- DAC39J84 reset, TX enable, alarm, sync, and manual SPI pins.
- MicroBlaze/UART register access using the same style as the NAPSAC reference design.

## LED Map

| LED | Meaning |
| --- | --- |
| 0 | VC709 200 MHz fabric heartbeat |
| 1 | FMC present |
| 2 | FMC power-good from mezzanine |
| 3 | `CLK_FMC` activity seen |
| 4 | `SYSREF_FMC` activity seen |
| 5 | `GBTCLK0` or `GBTCLK1` activity seen via `IBUFDS_GTE2` `ODIV2` |
| 6 | DAC alarm not asserted |
| 7 | Error summary: missing/power-bad FMC or DAC alarm |

## UART Commands

UART is 250000 baud, 8N1.

```text
HELP
STAT
RDRO n
RDRW n
WRTE n value
```

`RO0` is packed status. `RO1` is the latest one-second `CLK_FMC` count.
`RO2` is the latest one-second `SYSREF_FMC` count. `RO3` is selected by
`RW1[1:0]`: `0=GBTCLK0`, `1=GBTCLK1`, `2=raw pins`, `3=build ID`.

## RW0 Control Bits

| Bit | Function |
| --- | --- |
| 0 | `FMC_C2M_PG_LS` value when bit 31 enables override |
| 1 | HMC7044 reset |
| 2 | DAC reset_n |
| 3 | DAC TX enable |
| 4 | ADC1 reset |
| 5 | ADC2 reset |
| 16 | DAC CS_n, active when bit 30 enables manual SPI |
| 17 | DAC SCLK, active when bit 30 enables manual SPI |
| 18 | DAC SDIN, active when bit 30 enables manual SPI |
| 19 | HMC CS_n, active when bit 30 enables manual SPI |
| 20 | HMC SCLK, active when bit 30 enables manual SPI |
| 21 | HMC SDIO output value |
| 22 | HMC SDIO output-enable |
| 30 | Manual SPI enable |
| 31 | `FMC_C2M_PG_LS` override enable; default is forced high |

## Build

From a Vivado 2023.1 Tcl shell:

```tcl
cd D:/DAVIS/Research/HighSpeedDAQ/DAQ_LAUNCH
source create_project.tcl -tclargs --fresh-ip
source build.tcl
```

Then build the MicroBlaze firmware from Vitis/XSDK Tcl after the XSA exists:

```tcl
source build_sw.tcl
```

Re-run implementation with the firmware baked into the bitstream:

```tcl
source build.tcl -tclargs --bake
```
