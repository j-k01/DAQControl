# Prebuilt unified USB-host and DAQ Ethernet artifacts

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
- `pico2_daq_pico002.uf2` (persistent production firmware; not auto-loaded)

The DAQ bitstream, MicroBlaze application, and generated PS initialization are
the repository's existing `prebuilt/top.bit`, `prebuilt/firmware.elf`, and
`prebuilt/psu_init.tcl`. They remain in that parent directory but are covered
by this directory's manifest and verified before programming.

`bl31.elf` is Xilinx Trusted Firmware 2.10/2024.1 built with
`PRELOADED_BL33_BASE=0x30000000` to match this U-Boot image. `Image` and
`system.dtb` are from Xilinx Linux 6.6 tag
`xlnx_rebase_v6.6_LTS_2024.1`. The initramfs is based on Alpine 3.24.1 and
contains the Linux DAQ Ethernet service, DDR diagnostic utility, and the
dual-ingress Pico CDC/SPI bridge. The production UF2 is included only for an
explicit Pico firmware update and is not copied during normal startup. The
bridge accepts requests directly on UDP port 5007 or through the MicroBlaze
UART command and its separate DDR mailbox. Both paths support generic Pico
USB CDC byte forwarding; SPI RPC remains available as a hardware diagnostic.

No artifact in this directory is persistently programmed by the loader.
