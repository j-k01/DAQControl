# LiteJESD DAC TX Block

`litejesd_dac_tx.v` is generated from the vendored LiteJESD204B source in
`third_party/litejesd204b`.

Generated configuration:

- JESD204B TX only
- 8B/10B line coding
- `L = 8`
- `M = 4`
- `N = 16`
- `N' = 16`
- `S = 1`
- `F = 1`
- `K = 32`
- `HD = 1`
- scrambling enabled
- subclass-1 style `SYSREF` input

The module exports one 64-bit converter input per DAC converter:

```verilog
converter0 ... converter3
```

Each converter word carries four 16-bit DAC samples:

```verilog
converterN[15:0]  = sample 0
converterN[31:16] = sample 1
converterN[47:32] = sample 2
converterN[63:48] = sample 3
```

The block outputs one raw 8B/10B TX datapath per JESD lane:

```verilog
tx_data0 ... tx_data7  // 32-bit GTH TXDATA
tx_ctrl0 ... tx_ctrl7  // 4-bit GTH TXCHARISK/control
```

The GTH Wizard must therefore expose a 32-bit TX user-data path plus a 4-bit
TX control/charisk path per lane. At 10 Gbps with 8B/10B and 32-bit user
data, the TX user clock is 250 MHz.

`daq_litejesd_dac_tx_path.v` is the hardware-facing shell for the launch test.
It instantiates `litejesd_dac_tx`, builds four 64-bit source words, and can
route those source words through `dac39j84_physical_mapper`. In that normal
mode, the source words are physical DAC outputs 0..3, not raw LiteJESD
converter buses. The mapper then splits the sample bytes across LiteJESD
converter inputs to match the Sundance/DAC39J84 byte-lane adapter. The shell
exports:

```verilog
gth_txdata[255:0]    // {lane7, ..., lane0}, 32 bits per lane
gth_txcharisk[31:0]  // {lane7, ..., lane0}, 4 bits per lane
```

`dac39j84_physical_mapper` has a runtime pair-map selector so the cabled DAC
outputs can disambiguate physical channel order without another implementation
run. Pair map `0` is the Sundance adapter placement, `1` swaps output labels
within the pair, `2` uses native LiteJESD converter-pair ordering, and `3`
keeps the Sundance lane placement but flips byte orientation.

For the current DAC39J84 initialization, connect `gth_txdata` to the wizard TX
user data input without an FPGA-side lane permutation. The DAC startup sequence
uses Sundance's `init8411_dac_remapped` path and programs DAC39J84
`config95/config96` to `0x3021/0x7654`, so the physical FMC/HPC0 lane mapping
is corrected inside the DAC SerDes-to-JESD crossbar. Applying an additional
FPGA-side TX lane remap double-corrects the mapping and corrupts the DAC sample
stream while the JESD link can still appear up. Connect `gth_txcharisk` in the
same identity lane order to the 8B/10B `TXCTRL2` path, with `TXCTRL0` and
`TXCTRL1` tied low unless the generated wrapper requires those ports for
another control function. Keep `TX8B10BEN` asserted and `TX8B10BBYPASS`
deasserted.

`dac39j84_init.v` configures the DAC automatically after the HMC7044 clock
startup is complete. It uses the Sundance `init8411_dac_remapped` register
order, 500 kHz SPI, 10 ms spacing between writes, and the 1 s alarm-clear
delay from the BSP. Register `0x4D` is deliberately programmed as `0x0300`
instead of Sundance's remapped-table `0x9300`; TI defines that register's
upper byte as `M-1`, and the 8411 mode requires `M=4`.

Runtime selectors exposed through `RW1[4:0]`:

```text
0x6: LiteJESD status
0x7: DAC waveform debug word
0xE: DAC39J84 init status
0xF: DAC39J84 last SPI write
0x10: ADS54J60 init/readback status
0x11: ADC1 analog-bank readback summary
0x12: ADC1 JESD-digital readback summary
0x13: ADC1 JESD-analog readback summary
0x14: ADC2 analog-bank readback summary
0x15: ADC2 JESD-digital readback summary
0x16: ADC2 JESD-analog readback summary
0x17: ADS54J60 last SPI write
0x18: ADS54J60 last SPI read
```

Regenerate with:

```powershell
python -m pip install migen litex
python scripts/gen_litejesd_dac_tx.py
```

If Migen/LiteX are installed into a separate directory, point the generator at
that directory:

```powershell
$env:LITEJESD_PYDEPS = "C:/tmp/litejesd_pydeps"
python scripts/gen_litejesd_dac_tx.py
```
