# PS Ethernet Readout

This app runs on `psu_cortexa53_0` and reads the ADC DMA buffers from PS DDR:

- app DDR window: `0x01000000` through `0x0EFFFFFF`
- chip0 buffer: `0x10000000`
- chip1 buffer: `0x10020000`
- frame size per chip: 16 bytes
- nominal frame count: 4096

The MicroBlaze firmware still owns capture control. Run `DMAC 4096` over UART
first, then request Ethernet readout from the host.

## Build

```powershell
xsct build_ps_eth_stream.tcl
```

This creates:

```text
sw/ps_eth_workspace/ps_eth_stream/Debug/ps_eth_stream.elf
```

## Load

After the FPGA and MicroBlaze firmware are loaded:

```powershell
xsct load_ps_eth_stream.tcl
```

Use `--init-ps` only if the PS/DDR has not already been initialized:

```powershell
xsct load_ps_eth_stream.tcl --init-ps
```

The app uses static IP `192.168.2.10/24`, listens on UDP port `5006`, and sends
responses to the source UDP port of the request.

## Debug Visibility

`xil_printf` output goes to PS UART0 (`0xFF000000`). On the ZCU102 the
CP2108 quad UART maps Interface 0 to PS UART0; on the current local PC this
enumerates as `COM9` at 115200 (the MicroBlaze UART is Interface 2 = `COM10`).

The app also maintains a debug mailbox in PS DDR at `0x0F000000` (outside the
app ELF window ending at `0x0EFFFFFF`, below the ADC DMA buffers at
`0x10000000`). Read it from XSCT without disturbing the A53:

```tcl
connect
targets -set -filter {name =~ "*PSU*"}
mrd 0x0F000000 8
```

Mailbox layout (u32 words):

```text
[0] progress/error code   0xDA000001 main entered
                          0xDA000002 GIC + exceptions registered
                          0xDA000003 lwip_init done
                          0xDA000004 xemac_add OK (PHY autoneg done)
                          0xDA000005 netif up, IRQs unmasked
                          0xDA000006 UDP bound, app ready
                          0xDA0000FF main loop running
                          0xDAE00001 xemac_add failed
                          0xDAE00002 udp_new failed
                          0xDAE00003 udp_bind failed
[1] heartbeat             increments ~4 Hz while the main loop is alive
[2] UDP commands received
[3] UDP packets sent
```

A progress value stuck at `0xDA000003` means xemac_add is waiting on PHY
autonegotiation, i.e. no Ethernet link.

## Host Receive

Set the host Ethernet interface to the same subnet, for example
`192.168.2.1/24`, then run:

```powershell
python scripts\receive_ps_eth_stream.py --board-ip 192.168.2.10 --local-port 5005 --chip all --frames 4096
```

The app sends binary captures to:

```text
captures/eth/ps_eth_capture_chip0.bin
captures/eth/ps_eth_capture_chip1.bin
```

Each UDP data packet starts with a 32-byte little-endian header:

```text
u32 magic        0x44415144 ("DAQD")
u16 version      1
u16 header_bytes 32
u32 sequence
u32 chip         0 or 1
u32 word_offset  u32 offset in the chip buffer
u32 word_count
u32 byte_count
u32 flags        bit0 = last packet for this chip
```
