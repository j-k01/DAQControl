# DRAFT (not filed): XilinxBoardStore issue — ZCU102 preset vs rev 1.1 SODIMM

Status: **draft only, never filed** (2026-06-11). Review and decide whether to
post it at <https://github.com/Xilinx/XilinxBoardStore/issues/new>.

## Why file at all

- Searched `Xilinx/XilinxBoardStore` issues/PRs: nobody has reported this.
  The only zcu102-matching hits are unrelated infra PRs (#784, #800, #805,
  #806).
- The failure mode is vicious: on rev 1.1 boards, JTAG/psu_init flows produce
  DDR that aliases 16 KB apart, so every ELF download silently self-corrupts
  and applications crash instantly with no tool-level error. It cost this
  project a full day of bisecting (see clean_writeup.txt); the next person
  gets no breadcrumbs because nothing anywhere says the preset is wrong for
  newer boards.

## Why an ISSUE and not a PR

Flipping the preset to x16 would be the same bug mirrored: it would break
rev 1.0 boards' JTAG flows exactly the way x8 breaks rev 1.1. The correct fix
is **per-revision presets** — and `board.xml` already declares revisions 1.0
and 1.1, so the structure exists. How to key presets to revisions is a
maintainer/schema decision, so the evidence belongs in an issue and the
design choice belongs to them.

## Why this is XilinxBoardStore's defect and not anyone else's

- ZCU102-Ethernet wiki repo: its documented PetaLinux/BOOT.BIN flow boots
  through the zcu102 FSBL, which ignores static DDR values and reads the
  SODIMM SPD every boot (`xfsbl_ddr_init.c`,
  `XFsbl_DdrComputeDimmParameters`) — correct on both modules. No patch
  needed there; its bare-metal side-flow merely inherits the preset defect
  like every zcu102 Vivado design does.
- AR 71961 and the 2018.3+ FSBL are Xilinx's mitigations for boot flows; the
  raw preset consumed by psu_init generation was simply never updated
  (verified identical x8 values in board file versions 3.3 and 3.4).

## Draft issue text

**Title:** ZCU102 preset PS DDR config doesn't match rev 1.1 boards' 1Rx16
SODIMM — JTAG/psu_init flows get aliased DDR

**Body:**

The zcu102 board files (3.3 and 3.4) carry a single `zynq_ultra_ps_e` preset
with the original x8 PS SODIMM geometry:

```
PSU__DDRC__DRAM_WIDTH      = 8 Bits
PSU__DDRC__BG_ADDR_COUNT   = 2
PSU__DDRC__DEVICE_CAPACITY = 4096 MBits
PSU__DDRC__ROW_ADDR_COUNT  = 15
```

However, ZCU102 kits labeled 0432055-05 onward (board rev 1.1, per AR 71961)
ship a **1Rx16** SODIMM (Micron MTA4ATF51264HZ-2G6E1, the part listed in
UG1182 v1.6). x16 DDR4 devices have 2 bank groups, not 4, so the preset
config drives a bank-group address bit the module doesn't implement.

**Effect:** boot flows through the FSBL are unaffected (`xfsbl_ddr_init.c`
reads the SODIMM SPD and reconfigures the DDRC). But any flow using the
**static psu_init generated from this preset** — XSCT/JTAG bring-up, Vitis
launch without "use FSBL flow for initialization" — gets DDR that aliases
addresses 16 KB apart on rev 1.1 boards. Every ELF download into DDR
silently self-corrupts (sections 16 KB apart overwrite each other),
producing instantly-crashing applications with no error from any tool. We
verified the aliasing with raw debugger writes after psu_init (a write to
`base+0x4000` lands on `base`), and confirmed that regenerating psu_init
with `DRAM_WIDTH=16 Bits, BG_ADDR_COUNT=1, DEVICE_CAPACITY=8192 MBits,
ROW_ADDR_COUNT=16` makes the same board fully functional.

**Request:** since `board.xml` already declares revisions 1.0 and 1.1,
please provide per-revision PS presets (x8 for 1.0, x16 for 1.1), or at
minimum document the mismatch in the preset/board files. As it stands, the
preset is silently wrong for every rev 1.1 board in JTAG/psu_init flows, and
the failure mode (corrupted ELF downloads, unhaltable cores) points users
everywhere except the memory config.

## Supporting evidence in this repo

- `ddr_alias_probe.tcl` — reproduces/verifies the aliasing on hardware.
- `clean_writeup.txt` — full bring-up post-mortem with the bisect.
- `create_project.tcl` / `create_zcu102_ps_boot_xsa.tcl` — the local x16
  overrides (our boards' fix).
- `notes/fsbl_boot_flow.md` — FSBL-vs-psu_init flow comparison.
