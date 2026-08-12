# Production Pico firmware

`prebuilt/pico2_daq_pico002.uf2` is the persistent production firmware for the
Pico attached to ZCU102 J96. It was built from
`ngncs-neuromorphic/pico2_daq` commit
`bd449c17f251a2f150035f9896c3caa8f3ec2047` with only the board identity patch
in `pico2_daq_pico002.patch` applied. PyDAQ expects that identity to be
`PICO-002`.

The UF2 targets RP2350 address `0x10000000`, the external-flash XIP region. It
therefore persists across Pico and FPGA power cycles. Program it once from a
PC or during an explicit firmware-recovery procedure. Routine execution of
`load_and_test.py` does not request BOOTSEL, copy a UF2, or otherwise alter the
installed Pico firmware.

The FPGA-side transport is transparent: Linux owns the J96 USB host, the Pico
bridge forwards CDC bytes, and `pydaq_fpga_transport.py` exposes that stream to
an unmodified PyDAQ installation. The Pico continues to parse the upstream
PyDAQ protocol and drive its normal SPI0 DAC pins.

To reproduce the artifact, apply the patch to the pinned upstream revision and
build the `rpipico2` PlatformIO environment. The resulting file is
`.pio/build/rpipico2/firmware.uf2`; its SHA-256 digest is recorded in
`prebuilt/SHA256SUMS`.
