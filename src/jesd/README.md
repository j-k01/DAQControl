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
It instantiates `litejesd_dac_tx` and builds four 64-bit source words. There
are two primary DAC mapping hypotheses exposed at runtime:

- `sample_map=0`: native LMF841, where LiteJESD logical lanes are adjacent
  high/low byte pairs for converters A/B/C/D. Scope testing with BRAM tones
  showed DAC0 and DAC2 are correct in this native order, while DAC1 and DAC3
  require a per-16-bit-sample byte swap before LiteJESD. The normal path now
  applies that odd-converter byte swap in HDL so DDS, BRAM, and IZH sources use
  the same working orientation.
- `sample_map=3`: Sundance core-lane preimage, where
  `dac39j84_physical_mapper` reproduces Sundance's `dac1_data_o` /
  `dac2_data_o` byte placement and then preimages that placement into
  LiteJESD logical lanes for the active DAC-side `0x3021/0x7654` crossbar plus
  TX lane mode 3.

The Sundance preimage is a diagnostic hypothesis to verify on the scope, not a
claim that the DAC39J84 itself requires non-adjacent logical byte pairs. The
legacy remap paths remain available only as diagnostics. The shell
exports:

```verilog
gth_txdata[255:0]    // {lane7, ..., lane0}, 32 bits per lane
gth_txcharisk[31:0]  // {lane7, ..., lane0}, 4 bits per lane
```

`dac39j84_physical_mapper` has a runtime source-order selector so the cabled
DAC outputs can disambiguate physical channel labels without another
implementation run. Source order `0` uses Sundance's internal
`dac2_ch2/dac2_ch1/dac1_ch2/dac1_ch1` order. Source order `1` uses the user
guide's physical `OUT_A/OUT_B/OUT_C/OUT_D` order. Source orders `2` and `3`
swap one Sundance pair at a time for diagnostics. `map_mode[3:2]=0` is the
Sundance-core-lane preimage after the DAC-side `0x3021/0x7654` crossbar and
TX lane mode 3. `map_mode[3:2]=1` emits Sundance core-lane bytes directly for
identity-lane diagnostics, `2` keeps the old upper-lane reverse check, and `3`
flips byte orientation as a last-resort diagnostic.

For the current DAC39J84 initialization, the firmware default uses
`sample_map=0`, TX lane mode 3, and `conv_sel=7`. TX lane mode 3 is the inverse
map implied by Sundance's `init8411_dac_remapped`
`config95/config96 = 0x3021/0x7654` setting. In this mode, the FPGA emits
native logical LiteJESD converter streams, byte-swapping the odd converter
streams as described above, and the top-level mux routes logical lanes to
physical GTH lanes as `[3,0,2,1,4,5,6,7]`. This keeps physical-lane correction
after LiteJESD, at the same abstraction level as the GTH lanes.
Connect `gth_txcharisk` in the same lane order as `gth_txdata` to the 8B/10B
`TXCTRL2` path, with `TXCTRL0` and `TXCTRL1` tied low unless the generated
wrapper requires those ports for another control function. Keep `TX8B10BEN`
asserted and `TX8B10BBYPASS` deasserted.

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
