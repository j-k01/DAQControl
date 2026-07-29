# Minimal unified Linux USB and DAQ Ethernet runtime

The tested image was built on capitolpeak with:

- Xilinx Linux tag `xlnx_rebase_v6.6_LTS_2024.1`
  (`3af4295e00efdced3e8c6973606a7de55f6bf7dc`);
- Xilinx ARM Trusted Firmware tag `xlnx_rebase_v2.10_2024.1`
  (`4f82b6134e7b43722616c855e5016d42a3ea26d2`);
- the Vitis 2024.1 AArch64 Linux toolchain; and
- Alpine minirootfs 3.24.1 for AArch64.

The Xilinx ZynqMP defconfig was used with the following facilities forced
built-in:

```text
CONFIG_BLK_DEV_INITRD=y
CONFIG_DEVTMPFS=y
CONFIG_DEVTMPFS_MOUNT=y
CONFIG_USB_XHCI_HCD=y
CONFIG_USB_XHCI_PLATFORM=y
CONFIG_USB_DWC3=y
CONFIG_USB_DWC3_XILINX=y
CONFIG_USB_ACM=y
CONFIG_USB_STORAGE=y
CONFIG_VFAT_FS=y
```

`init` is the initramfs PID 1 program. It assigns `192.168.2.10/24` to the
ZCU102 GEM interface and starts `daq-eth-service`, then performs the Pico USB
update. Ethernet failure is reported but does not prevent USB/Pico recovery.
The updater resets a running Pico CDC application at 1200 baud, mounts the
RP2350 BOOTSEL volume, copies the RAM-only UF2, and checks the resulting USB
product string.

`daq_eth_service.c` is a static userspace replacement for the prior bare-metal
A53 network loop. Linux owns GEM and the IP stack while the service preserves
the established UDP port 5006 command set and DAQS packet format. It maps the
existing capture buffers and the MicroBlaze mailbox through `/dev/mem`; the PL
and MicroBlaze control design are unchanged.

Linux is booted with `mem=240M`, limiting its allocator to
`0x00000000..0x0EFFFFFF`. The existing DAQ mailbox and DMA buffers at
`0x0F000000` and above therefore remain reserved for the PL, MicroBlaze, and
readout service. `daq_mem.c` builds the small `daq-mem` console utility used by
the deterministic integration test.

The two service programs are built as static AArch64 binaries with the Xilinx
Vitis toolchain:

```sh
aarch64-linux-gnu-gcc -O3 -static -Wall -Wextra -Werror \
  -o daq-eth-service daq_eth_service.c
aarch64-linux-gnu-gcc -O2 -static -Wall -Wextra -Werror \
  -o daq-mem daq_mem.c
```

No part of this flow performs persistent ZCU102 or Pico writes.
