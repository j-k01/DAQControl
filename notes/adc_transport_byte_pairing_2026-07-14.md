# ADC transport byte-pairing diagnosis (2026-07-14)

## Symptom

With all DAC crossbar outputs off, ADC0 produced repeated offsets near one byte
(`-256..+255` ADC codes, about `-7.42..+7.39 mV` at 1.9 V full scale).  A DAC0
DDS loopback otherwise looked correct, so this resembled deterministic noise
superimposed on the signal.

This was not primarily ADS54J60 time-interleave-core offset.  Raw samples mixed
reasonable near-zero words such as `0x0005`/`0xFFFC` with words such as
`0x00F9`/`0xFF0C`.  The sample high byte belonged to the intended converter,
but its low byte came from another transport lane.  Changing an unrelated low
byte changes the signed 16-bit value in 256-code (`7.421875 mV`) increments.

## Verified LMFS=4211 publication mappings

The post-link raw-transport capture (`RW5 capture_format=1`) was searched over
all high/low lane pairs, within-word byte orders, and small relative shifts.
The valid pairs have no byte-index reversal and no sample shift:

| ADC chip | Internal channel A | Internal channel B |
|---|---|---|
| ADC0 | high lane 0 + low lane 2 | high lane 1 + low lane 3 |
| ADC1 | high lane 0 + low lane 3 | high lane 1 + low lane 2 |

ADC1 A/B is still swapped only at `adc_frontend.v` output publication so the
external channel order remains IN3, IN4, as established by the earlier
two-tone cable test.

`scripts/diagnose_adc_byte_alignment.py` undoes that connector-order swap for
chip1 before searching, so its lane numbers always refer to actual transport
lanes 0 through 3.

## ADC0 measured result

With all DAC routes off and normal unfiltered capture format:

| Build | Raw RMS | Raw range | Voltage RMS |
|---|---:|---:|---:|
| Adjacent/generated pairing | about 169 codes | -256..+255 | about 4.90 mV |
| Correct high0/low2 pairing | about 15.9 codes | typically -116..+145 | about 0.46 mV |

The improvement is about 10.7x RMS.  Post-fix modulo-4 phase means are only a
few ADC codes, so display-time modulo-4 baseline subtraction is not required
to remove the former +/-7 mV artifact and should not be used as a substitute
for correct transport-byte assembly.
