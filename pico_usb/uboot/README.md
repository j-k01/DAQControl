# U-Boot runtime

The prebuilt U-Boot is Xilinx 2024.01/2024.1 linked at `0x30000000`. It is
entered through `prebuilt/bl31.elf`, which provides the PSCI and ZynqMP
firmware services required by Linux.

`picoboot.c` is the earlier direct RP2350 diagnostic command. It remains in the
U-Boot image and source tree for low-level investigation, but it is not the
normal loader path. U-Boot 2024.1's xHCI implementation had endpoint and
mass-storage transfer limitations that do not occur with the standard Linux
USB stack. The verified workflow therefore boots the minimal Linux
initramfs and lets `cdc_acm`, `usb-storage`, and VFAT perform the reset and UF2
copy.
