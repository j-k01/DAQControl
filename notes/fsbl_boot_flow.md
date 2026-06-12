# FSBL / BOOT.BIN deployment flow (planned)

## Why

Two flows can bring this board up; they fail differently and serve different
purposes:

| | JTAG + psu_init (current dev flow) | FSBL BOOT.BIN (deployment) |
|---|---|---|
| DDR init | **static** values from the Vivado board preset | FSBL built for board zcu102 **reads the SODIMM SPD over I2C every boot** (`xfsbl_ddr_init.c`, `XFsbl_DdrComputeDimmParameters`) and programs the controller to match the actual module |
| PMU firmware | not loaded | loaded from BOOT.BIN (proper power management) |
| Needs capitolpeak (JTAG host) | yes, every power cycle | no — board boots standalone from SD/QSPI |
| Iteration speed | seconds per A53 app reload | rebuild + repackage BOOT.BIN per change |

The static-vs-SPD difference is not academic: rev 1.1-era ZCU102 kits
(0432055-05 onward) ship a 1Rx16 SODIMM (Micron MTA4ATF51264HZ-2G6E1) while
the Vivado board preset (board files 3.3 and 3.4 both) still encodes the
original x8 module. The mismatch drives a bank-group bit the module lacks and
aliases DDR 16 KB apart (Xilinx AR 71961; reproduce/verify with
`ddr_alias_probe.tcl`). FSBL flows never see this because of the SPD read;
psu_init flows hit it head-on — it cost us the entire 2026-06-10/11 Ethernet
bring-up. Our XSAs now carry x16 overrides (`create_project.tcl`,
`create_zcu102_ps_boot_xsa.tcl`), which is correct for this board and
harmless under FSBL.

**Recommendation: keep JTAG + psu_init for development; use a BOOT.BIN with
the stock SPD-reading FSBL for deployment.**

## What a full SD/QSPI boot needs

1. **MicroBlaze firmware must live in the bitstream** — FSBL cannot load a
   MicroBlaze ELF. Build the gateware with `--bake` (`build.tcl --bake`
   merges `sw/workspace/firmware/Debug/firmware.elf` into the BRAM init of
   `top.bit` via `updatemem`).
2. **BOOT.BIN contents** (in order):
   - `zynqmp_fsbl.elf` — build from the PS XSA with the zcu102 board flag so
     the SPD path is compiled in (Vitis does this automatically when the XSA
     reports the zcu102 board part).
   - `pmufw.elf` — generated alongside the platform
     (`sw/workspace/hw_platform/zynqmp_pmufw/pmufw.elf`).
   - `top.bit` (the `--bake`d one).
   - `ps_eth_stream.elf` (A53 app, linked at 0x30000000).
3. `make_qspi_boot.tcl` / `program_qspi_boot.tcl` already exist for the
   packaging/flash steps; `create_zcu102_ps_boot_xsa.tcl` generates a
   PS-only XSA for the FSBL build.
4. Boot mode switches: SD1 = 0101 / QSPI32 = 0010 (currently 0000 = JTAG).
   Set back to JTAG for development sessions.

## Caveats

- The A53 app expects the MicroBlaze to have armed streaming (`STRM <D>` over
  COM10) before `STRM` over UDP does anything; a deployment image may want
  the MB firmware to auto-start streaming with a default D after JESD comes
  up.
- After any `sw/src/main.c` change, a deployed board needs a re-`--bake`d
  bitstream, not just a new ELF.
- JTAG flow remains the source of truth for bring-up debugging: it exposes
  exactly the static-config path that the FSBL papers over.
