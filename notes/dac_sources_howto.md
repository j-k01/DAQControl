# DAC sources: how to drive them without screwing up

This board has **one DAC source mux per channel** (`dac_channel_source_mux` in
`src/jesd/daq_litejesd_dac_tx_path.v`). Each of the four DAC channels can be
fed from one of these sources, selected at runtime:

| Source | NSRC name | What it is |
|---|---|---|
| DDS | `dds` | Hardware sine generator. **One frequency for all channels** (single RW3[31:8] phase-increment field). |
| BRAM | `bram` | Per-channel arbitrary waveform played from a program BRAM. Distinct content per channel. |
| IZH | `izh` | Per-channel Izhikevich neuron spike → trapezoid pulse shaper. |
| vout | `vout` | Direct neuron membrane voltage. |
| auto | `auto` | DDS if program not enabled, BRAM if it is. |

The mux output is **registered, then byte-remapped by `dac_source_to_converter_preimage`**
(a fixed, validated reordering for the DAC39J84 lane layout) and sent to LiteJESD.
The preimage is correct — do not touch it. All sources share the identical
64-bit contract: `{sample3, sample2, sample1, sample0}`, sample0 in `[15:0]`,
four chronological 16-bit samples.

## THE RULES (these are the mistakes that cost a full debugging session)

1. **Select sources only with `NSRC`. NEVER hand-poke RW3.** Post-merge, RW3 is
   heavily overloaded and shares bits with the CSTP/IZH config:
   - `[1]` dac restart, `[2]` adc restart, **`[3]` = ADC capture trigger**,
     `[5:4]` = DAC source mask, `[6]` = program_enable, `[7]` = IZH config
     strobe, `[11:8]` = IZH param, `[13:12]` = IZH channel, `[14]` = IZH all,
     `[31:8]` = DDS phase-inc / BRAM frame-count.
   - Writing a "restart" value like `0x68` quietly fires an **ADC capture** and
     corrupts state. `NSRC bram` already sets both the source mask AND
     `program_enable` for you. Trust it.

2. **Load BRAM content with `PROG`, not `DPWR`.** `PROG ch <nwords>` followed by
   the binary u32 stream is the program command. (`DPWR` writes the same BRAM but
   is the debug/partial-write path; don't use it for playback.)

3. **`RW2 = 0x01000018`** (`DAC_NORMAL_RW2`): sample_map=0, tx_lane=3. Set this
   before programming. It is the validated lane/sample-map mode.

4. **BRAM loop = 8192 u32 words = 16384 samples.** For a seamless sine, snap the
   frequency to an **integer number of cycles per 16384 samples** (multiples of
   ~61.04 kHz at 1 GS/s), otherwise the loop wrap injects a phase discontinuity
   that splatters the spectrum.

5. **The on-chip `program_word`/debug registers are CDC-mangled** (a fast 64-bit
   bus through a single 2-flop synchronizer). They read as plausible garbage —
   do **not** trust them to verify the player. Verify with an actual ADC
   loopback capture instead.

## Proven scripts — use these, don't hand-roll register pokes

| Goal | Script |
|---|---|
| Four distinct sines (BRAM), one per channel, with PASS/FAIL | `scripts/quad_sine_loopback_check_uart.py` |
| Four Izhikevich neuron profiles on the four DACs | `scripts/four_izh_profiles_capture_uart.py` |
| A/B a single channel DDS vs BRAM | `scripts/switch_dac_source_uart.py {dds,bram,toggle}` |
| Continuous decimated ADC stream over Ethernet | `scripts/receive_ps_eth_stream_continuous.py` (arm `STRM <D>` first) |
| Live scope + per-channel source switching | `scripts/dac_source_scope.py` |

## Minimal manual recipe (e.g. for a new waveform)

```
WRTE 2 0x01000018           # DAC_NORMAL_RW2
PROG 0 8192                 # then stream 8192 little-endian u32 (2 samples/word)
PROG 1 8192                 # ... per channel
NSRC all bram              # selects BRAM source + enables the player (no RW3 poke)
# capture the loopback to verify
```

For DDS: `NSRC all dds` (optionally set frequency via the DDS step / RW3[31:8],
but prefer `switch_dac_source_uart.py dds --step N`).
For neurons: `NEUR <ch> <profile>`, `NEUR all dt 0x8000`, `NEUR all period 1`,
then `NSRC all izh`.

## Verify

Always confirm with a real DAC→ADC loopback capture (the `*_loopback_check`
scripts report PASS/FAIL and purity). DDS plays even with a stuck select, so
"DDS looks fine" is not proof the select works — test BRAM or IZH.
