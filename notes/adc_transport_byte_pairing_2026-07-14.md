# ADC transport byte-pairing diagnosis (2026-07-14)

## Symptom

With all DAC crossbar outputs off, ADC0 produced repeated offsets near one byte
(`-256..+255` ADC codes, about `-7.42..+7.39 mV` at 1.9 V full scale).  A DAC0
DDS loopback otherwise looked correct, so this resembled deterministic noise
superimposed on the signal.

This was not primarily ADS54J60 time-interleave-core offset.  Raw samples mixed
reasonable near-zero words such as `0x0005`/`0xFFFC` with words such as
`0x00F9`/`0xFF0C`.  The sample high byte belonged to the intended converter,
but its low byte came from another transport lane.  An unrelated 8-bit value
can move the signed result by as much as 255 codes (`7.3929 mV`).

## Verified LMFS=4211 publication mappings

The post-link raw-transport capture (`RW5 capture_format=1`) was searched over
all high/low lane pairs, within-word byte orders, and small relative shifts.
The valid pairs have no byte-index reversal and no sample shift:

| ADC chip | Internal channel A | Internal channel B |
|---|---|---|
| ADC0 | high lane 0 + low lane 2 | high lane 3 + low lane 1 |
| ADC1 | high lane 0 + low lane 3 | high lane 1 + low lane 2 |

ADC1 A/B is still swapped only at `adc_frontend.v` output publication so the
external channel order remains IN3, IN4, as established by the earlier
two-tone cable test.

ADC0 channel B was initially misclassified as floating-input behavior.  A
distinct-pair raw-lane search showed high3/low1 at about 16 codes RMS, while
the incorrect high1/low3 reconstruction was about 3986 codes RMS.

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

## Final four-channel verification

After correcting ADC0 channel B from high1/low3 to high3/low1, two independent
normal-format captures with every DAC route off measured:

| Host channel | Raw RMS capture 1 | Raw RMS capture 2 |
|---|---:|---:|
| ADC0 | 15.968 codes | 15.824 codes |
| ADC1 | 15.809 codes | 15.837 codes |
| ADC2 | 14.866 codes | 14.884 codes |
| ADC3 | 14.826 codes | 14.916 codes |

Before this last correction, host ADC1 was about 4150 codes RMS in normal
capture.  The corrected result is a roughly 262x reduction without filtering.
A coherent 62.5 MHz DAC0-to-ADC0 loopback retained 54.42--54.50 dB fitted SNR.
ADC1 contained only about 10 coherent counts versus about 9460 counts on ADC0
(approximately -59.4 dB coupling), while ADC2/ADC3 had no measurable tone.
