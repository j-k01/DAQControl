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
It instantiates one `dac_channel_source_mux` per DAC channel. Each mux selects
between BRAM, DDS, and neuron/pulse sources and emits one complete 64-bit DAC
channel input word. The source contract is deliberately simple:

```text
dac_channel_word[15:0]  = sample 0
dac_channel_word[31:16] = sample 1
dac_channel_word[47:32] = sample 2
dac_channel_word[63:48] = sample 3
```

No source is allowed to prearrange bytes, lanes, physical DAC outputs, or
connector labels before this mux boundary. After source selection, the four
complete channel streams are registered on `jesd_clk` and then fed through
`dac_source_to_converter_preimage`, a wiring-only module that creates the
LiteJESD converter preimage. Board lane correction is still handled after
LiteJESD by moving `TXDATA` and `TXCHARISK` together. Legacy remap calculations
remain visible only as ILA diagnostics and do not drive the DAC output. The
shell exports:

```verilog
gth_txdata[255:0]    // {lane7, ..., lane0}, 32 bits per lane
gth_txcharisk[31:0]  // {lane7, ..., lane0}, 4 bits per lane
```

`dac39j84_physical_mapper` is a whole-stream order diagnostic for ILA only.
It is not in the live output path. Source order `0` means LiteJESD converter N
uses `srcN`; source order `1` reverses them, and source orders `2`/`3` swap one
stream pair at a time only for diagnostics. `map_mode[3:2]` is intentionally
ignored so this mapper cannot silently reintroduce byte-lane preimage behavior.

For the current DAC39J84 initialization, the firmware default remains
source-preimage mode, TX lane mode 3, and `RW2=0x00000018`. The normal source
contract, before the preimage module, is:

```text
BRAM/DDS/neuron channel 0 -> source0[63:0] = {t3,t2,t1,t0}
BRAM/DDS/neuron channel 1 -> source1[63:0] = {t3,t2,t1,t0}
BRAM/DDS/neuron channel 2 -> source2[63:0] = {t3,t2,t1,t0}
BRAM/DDS/neuron channel 3 -> source3[63:0] = {t3,t2,t1,t0}
```

The legacy remap calculations are diagnostic probes only. Do not turn
probe-source numbers from those modes into a final mapping. The acceptance test
must use a byte-asymmetric pattern such as
`0x1201, 0x2302, 0x3403, 0x4504, ...`, because a sine can land at the right FFT
bin while byte orientation is still wrong.

```powershell
python scripts\capture_plot_adc_uart.py --port COM10 --expect-build-id 0xDA010034 --rw2 0x00000018 --program-mode byte-pattern --program-channel all --verify-upload-words 16 --words 4096 --sources 0,1,2,3 --prefix dac_byte_pattern_check
```

After a bitstream/firmware load, the firmware does more than set the DAC source
mux. It releases the GT path, waits for the TX/LiteJESD stream to become
stable, then pulses the DAC39J84 init FSM so the DAC sees a clean
CGS/ILAS/data sequence. A valid no-reprogram DAC recovery sequence is:

```text
WRTE 3 0
WRTE 5 0x80000100
WRTE 2 0x00000019
WRTE 2 0x00000018
WRTE 3 2
WRTE 3 0
```

Do not judge an ADC loopback capture until that sequence, or the equivalent
firmware startup path, has restored visible DAC output. In particular,
`RW3[0]` is the HMC restart bit; accidentally leaving it asserted can disturb
the clock tree and will not be repaired by merely selecting DDS afterward.

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
