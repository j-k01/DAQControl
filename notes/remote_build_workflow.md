# Remote Build Workflow

This note records the established capitolpeak build flow for future work.

## Capitolpeak Role

- `capitolpeak` is a build host only. It should pull from this repository.
- Do not use capitolpeak for live board/Ethernet experiments. The live board is
  on the local/demo-PC terminal unless the user says otherwise.
- Prefer local repo edits, tests, commits, and pushes first. Then have
  capitolpeak `git pull --ff-only` and build that exact commit.

## SSH

The key that has worked in this workspace is:

```powershell
ssh -o BatchMode=yes -o IdentitiesOnly=yes -i C:\Users\paral\.ssh\capitolpeak_auto jkincaid@capitolpeak.ece.ucdavis.edu hostname
```

If another key fails with `Permission denied (publickey,password)`, try this one
before inventing a new access path.

## Clean Pull Before Build

Capitolpeak often has tracked generated artifacts modified by prior builds, such
as:

- `project/DAQ_LAUNCH.runs/impl_1/top.bit`
- `hw/DAQ_LAUNCH.ltx`
- `reports/ip_status_after_create.rpt`
- `ip_repo/AXI4_register_file_1_0/component.xml`

If `git pull --ff-only` is blocked by these files, stash the tracked generated
outputs on capitolpeak. Do not edit source there.

```bash
cd /home/jkincaid/DAQControl
git stash push -m pre-build-generated-artifacts -- \
  project/DAQ_LAUNCH.runs/impl_1/top.bit \
  hw/DAQ_LAUNCH.ltx \
  reports/ip_status_after_create.rpt \
  ip_repo/AXI4_register_file_1_0/component.xml
git pull --ff-only origin merge-stream-neuron
```

## Rebuild Sequence

Do not bake the MicroBlaze firmware into the bitstream by default. Baking with
`build.tcl --bake` roughly doubles build time and is usually not relevant for
our workflow; the firmware can be programmed/loaded quickly as a final separate
step.

The normal sequence is:

1. Clean generated Vivado state.
2. Regenerate/build hardware and export XSA.
3. Build MicroBlaze firmware from that XSA.

Run this on capitolpeak:

```bash
cd /home/jkincaid/DAQControl
rm -rf project .Xil
/home/jkincaid/bin/with_xilinx_2024_1 vivado -mode batch -source rebuild.tcl -tclargs --with-ps-ddr-dma --jobs 8
/home/jkincaid/bin/with_xilinx_2024_1 xsct build_sw.tcl
```

Only run `build.tcl --bake` when the user explicitly requests a self-contained
bitstream with firmware BRAM INIT already populated.

Use a temporary remote shell script for background launches. Nested `ssh 'nohup
bash -lc "... && ..."'` quoting has already caused the wrong command to run
(`build.tcl` alone against a stale project).

## Completion Rules

- Check the log and process status before saying the build is done.
- If the user asked for pushed artifacts, retrieve or commit the new generated
  artifacts after the build:
  - `project/DAQ_LAUNCH.runs/impl_1/top.bit`
  - `hw/DAQ_LAUNCH.ltx`
  - `prebuilt/top.bit`, if refreshed
  - `prebuilt/firmware.elf`, if firmware changed
- Do not confuse "build launched" with "new bitstream available".
