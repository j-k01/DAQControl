# Prebuilt USB-host test artifacts

`load_and_test.py` verifies every file against `SHA256SUMS` before it changes
the board. The tested JTAG/RAM boot chain is:

- `zynqmp_fsbl.elf`
- `zynqmp_pmufw.elf`
- `bl31.elf`
- `u-boot`
- `u-boot.dtb`
- `Image`
- `system.dtb`
- `pico-initramfs.cpio.gz`
- `pico2_usb_spi_test.bin`
- `pico2_usb_spi_test.uf2`

The DAQ bitstream is the repository's existing `prebuilt/top.bit` and is also
covered by this directory's manifest.

`bl31.elf` is Xilinx Trusted Firmware 2.10/2024.1 built with
`PRELOADED_BL33_BASE=0x30000000` to match this U-Boot image. `Image` and
`system.dtb` are from Xilinx Linux 6.6 tag
`xlnx_rebase_v6.6_LTS_2024.1`. The initramfs is based on Alpine 3.24.1 and
contains only the USB updater, its runtime, and the RAM-only UF2.

No artifact in this directory is persistently programmed by the loader.
