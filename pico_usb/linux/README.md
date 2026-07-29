# Minimal Linux USB updater

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

`init` is the initramfs PID 1 program. It uses no network and performs no
persistent ZCU102 or Pico writes. It resets a running Pico CDC application at
1200 baud, mounts the RP2350 BOOTSEL volume, copies the RAM-only UF2, and checks
the resulting USB product string.
