# DAC BRAM Byte Mapping Observation - 2026-06-07

## Context

This note records the current empirical DAC BRAM mapping problem observed during
scope-driven bring-up of the DAC39J84 path. The board was running the diagnostic
DAC BRAM/JESD path with BRAM sources selected and the current physical-map mode
under test.

The observed behavior is not consistent with the intended user-facing model:

```text
DAC BRAM channel 0 -> physical DAC0
DAC BRAM channel 1 -> physical DAC1
DAC BRAM channel 2 -> physical DAC2
DAC BRAM channel 3 -> physical DAC3
```

Instead, writing byte-distinct words into the per-channel DAC BRAMs shows that
the current "channel" streams are being interpreted as byte-lane/preimage
containers somewhere before the DAC39J84 receives the samples.

## Empirical Mapping

Using a symbolic 32-bit/64-bit BRAM test word with byte positions named
`A B C D E F G H`, the observed physical DAC effects were:

```text
BRAM channel 3:
  No apparent effect on any physical DAC output.

BRAM channel 2:
  Controls physical DAC3 and physical DAC2.
  For the byte pattern ABCD / EFGH:
    DAC2 responds to the [D, B] sequence.
    DAC3 responds to the [C, A] sequence.

BRAM channel 1:
  Controls physical DAC0.
  For the byte pattern ABCD:
    DAC0 responds to the [D, B] sequence.
    [A, C] appears unused for this physical output.

BRAM channel 0:
  Controls physical DAC1.
  For the byte pattern ABCD:
    DAC1 responds to the [D, B] sequence.
    [A, C] appears unused for this physical output.
```

In a more concrete 32-bit write form, data of the shape `0xXXBBXXAA` was the
portion that visibly controlled some DAC outputs. This means that only every
other byte of the nominal source stream was behaving like the physical DAC
sample stream for those outputs.

## Relevant Design Facts

The generated `litejesd_dac_tx.v` interface expects four converter streams:

```text
converter0[63:0] = four chronological 16-bit samples for converter 0
converter1[63:0] = four chronological 16-bit samples for converter 1
converter2[63:0] = four chronological 16-bit samples for converter 2
converter3[63:0] = four chronological 16-bit samples for converter 3
```

For each 16-bit sample, LiteJESD then emits high-byte and low-byte octets onto
logical JESD lanes. In other words, the LiteJESD converter inputs are not
physical byte lanes. They are sample streams.

The Sundance adapter uses a different external contract. It takes a wider
time-slot-oriented frame, then scatters bytes into DAC/JESD lane positions. It
does not treat four 64-bit words as four independent physical DAC outputs in the
same way our current BRAM player tries to expose them.

The current HDL has diagnostic/experimental mapper paths that split bytes from
source streams and repack them into LiteJESD `converterN` buses. That makes the
`converterN` buses act like byte-lane preimage containers rather than normal
converter sample streams. This is the wrong abstraction for a user-facing
"DAC channel N BRAM" contract.

## Working Interpretation

The current behavior is best explained by an abstraction mismatch:

1. The user-facing DAC BRAMs are intended to be physical DAC output streams.
2. The active mapper consumes those streams as ingredients for a JESD/DAC
   byte-lane preimage.
3. The generated LiteJESD block then performs its own converter-to-octet split.
4. The DAC39J84 init also has a DAC-side octetpath remap enabled
   (`config95/config96` currently using the Sundance remapped sequence).

Because of this, "BRAM channel 2 affects DAC2 and DAC3" is not analog leakage
or a random DAC failure. It is direct evidence that bytes from one nominal
source stream are being routed into multiple physical DAC byte positions.

The fact that channel 3 has no visible effect suggests the active preimage path
either does not consume that stream in the tested mode, consumes it only in byte
positions that do not reach the scoped outputs, or consumes it in the wrong
coordinate system relative to the DAC39J84 octetpath remap.

## Required Fix Direction

The design needs one clean DAC data contract:

```text
dac_src0[63:0] = physical DAC0, four chronological 16-bit samples
dac_src1[63:0] = physical DAC1, four chronological 16-bit samples
dac_src2[63:0] = physical DAC2, four chronological 16-bit samples
dac_src3[63:0] = physical DAC3, four chronological 16-bit samples
```

Then exactly one adapter stage should transform those physical streams into the
LiteJESD/DAC39J84 lane order. That adapter must be verified against the generated
LiteJESD byte-lane order and the DAC39J84/Sundance lane remap.

The fix should not be a source-specific workaround such as "source 3 goes to DAC
1." It should be a table-driven, testable transport adapter:

1. Start with four physical DAC output streams.
2. Build a byte-lane vector for the DAC39J84/LiteJESD transport.
3. Apply either FPGA-side lane remapping or DAC39J84-side octetpath remapping,
   but not both for the same coordinate transform.
4. Feed LiteJESD only with valid converter sample streams, or replace/wrap the
   transport so that explicit byte-lane inputs are used intentionally.

## Minimum Verification Before Accepting A Fix

Add a simulation/reference test using byte-asymmetric samples, for example:

```text
DAC0: 0x1201 0x2302 0x3403 0x4504
DAC1: 0x5605 0x6706 0x7807 0x8908
DAC2: 0x9A09 0xAB0A 0xBC0B 0xCD0C
DAC3: 0xDE0D 0xEF0E 0x7A0F 0x6B10
```

The model should run:

```text
physical DAC source streams
  -> proposed DAC adapter
  -> generated LiteJESD converter/octet mapping
  -> TX lane permutation
  -> DAC39J84 octetpath mapping
  -> recovered physical DAC streams
```

and assert that each recovered DAC stream exactly matches its corresponding
input stream. Sine waves are useful for link checks, but they are not sufficient
to prove byte order because byte swaps and lane mixing can still produce a
plausible periodic waveform.

