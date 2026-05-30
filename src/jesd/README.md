# LiteJESD DAC TX Block

`litejesd_dac_tx.v` is generated from the vendored LiteJESD204B source in
`third_party/litejesd204b`.

Generated configuration:

- JESD204B TX only
- 8B/10B line coding
- `L = 8`
- `M = 8`
- `N = 16`
- `N' = 16`
- `S = 2`
- `F = 4`
- `K = 32`
- scrambling enabled
- subclass-1 style `SYSREF` input

The module exports one 32-bit converter input per converter:

```verilog
converter0 ... converter7
```

Each converter word carries two 16-bit DAC samples:

```verilog
converterN[15:0]  = first sample
converterN[31:16] = second sample
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
It instantiates `litejesd_dac_tx`, drives one selectable converter with a
triangle wave, drives the remaining converters at midscale, and exports:

```verilog
gth_txdata[255:0]    // {lane7, ..., lane0}, 32 bits per lane
gth_txcharisk[31:0]  // {lane7, ..., lane0}, 4 bits per lane
```

For a corrected GT Wizard instance, connect `gth_txdata` to the wizard TX user
data input. Connect `gth_txcharisk` to the 8B/10B `TXCTRL2` / charisk path,
with `TXCTRL0` and `TXCTRL1` tied low unless the generated wrapper requires
those ports for another control function. Keep `TX8B10BEN` asserted and
`TX8B10BBYPASS` deasserted.

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
