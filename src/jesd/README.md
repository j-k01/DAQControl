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
are two primary DAC mapping paths exposed at runtime:

- `sample_map=0`: native LMF841 diagnostic, where LiteJESD logical lanes are
  adjacent high/low byte pairs for converters A/B/C/D, with the previously
  observed odd-converter byte swap still available for A/B testing.
- `sample_map=3`: table-driven byte-lane preimage. Four independent source
  streams are assigned to internal candidates A-D, then each candidate's high
  and low bytes are placed into explicit LiteJESD logical byte lanes before the
  generated core repacks them into `converter0..3`.

The preimage is not a claim that the DAC39J84 itself requires non-adjacent
logical byte pairs. It is a way to reproduce the Sundance-like internal
core-lane placement while still keeping the hardware contract clean:
`src0[63:0]..src3[63:0]` are four chronological 16-bit samples per independent
DAC source. The legacy remap paths remain available only as diagnostics. The shell
exports:

```verilog
gth_txdata[255:0]    // {lane7, ..., lane0}, 32 bits per lane
gth_txcharisk[31:0]  // {lane7, ..., lane0}, 4 bits per lane
```

`dac39j84_physical_mapper` has a runtime source-order selector so the cabled
DAC outputs can disambiguate front-panel labels without another implementation
run. Source order `0` maps `src0..src3` directly to candidate outputs A-D,
source order `1` reverses them, and source orders `2`/`3` swap one candidate
pair at a time. `map_mode[3:2]=0` uses the expected lane-pair preimage:

```text
candidate A: high J3, low J0
candidate B: high J2, low J1
candidate C: high J7, low J6
candidate D: high J5, low J4
```

`map_mode[3:2]=1` flips only the upper candidate-pair orientation, `2` flips
only the lower candidate-pair orientation, and `3` flips all candidate byte
pairs. Those modes are for byte-orientation verification with asymmetric test
patterns, not for source-number cargo culting.

For the current DAC39J84 initialization, the firmware default uses
`sample_map=1`, TX lane mode 0, and `conv_sel=7` (`RW2=0x010000E2`). In that
mode, the existing preimage feeds `dac39j84_sample_remap` so the LiteJESD
converter buses receive the desired source streams, while the FPGA TX lane mux
stays identity and the DAC-side `0x3021/0x7654` octetpath crossbar performs
the physical lane correction. The table-driven `sample_map=3` mapper remains
available for byte-lane diagnostics, but it is not the firmware default after
the 2026-06-06 hidden-loopback test.

Current cabled diagnostics:

| Physical output under test | Clean setting | Recovered bin | Notes |
| --- | --- | --- | --- |
| DAC1 | `sample_map=2`, source 3 only | 2000 | Legacy remap path, useful as a byte-pair probe only. |
| DAC2 | `sample_map=1`, source 3 only | 2800 | General preimage path, useful as a byte-pair probe only. |
| DAC3 | `sample_map=3`, source 2 only | 2400 | Strong evidence that a byte-lane preimage is needed. |
| DAC3 | `sample_map=0`, source 3 only | 2800 | Coherent but weaker than the byte-lane preimage route. |
| Hidden ADC converter0 | `sample_map=1`, TX lane 0, all sources | 1600/source0 | Clean, correlation +0.961. |
| Hidden ADC converter1 | `sample_map=1`, TX lane 0, all sources | 2800/source3 | Clean, correlation +0.998 after inversion/phase. |

Do not turn the probe-source numbers in this table directly into a final
four-channel byte table: different `sample_map` modes place the same source
bytes on different lane pairs. The next acceptance test should use an
asymmetric pattern such as `0x1201, 0x2302, 0x3403, 0x4504, ...`, because a
sine can land at the correct FFT bin while byte orientation is still wrong.

```powershell
python scripts\capture_plot_adc_uart.py --port COM10 --expect-build-id 0xDA010031 --rw2 0x010000E2 --program-mode byte-pattern --program-channel all --verify-upload-words 16 --words 4096 --sources 0,1,2,3 --prefix dac_byte_pattern_check
```

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
