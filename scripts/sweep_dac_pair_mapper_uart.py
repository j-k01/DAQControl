#!/usr/bin/env python3
"""Sweep DAC TX lane modes.

This is a focused bring-up helper for the current wiring:
  DAC physical output 0 -> ADC1 channel A
  DAC physical output 1 -> ADC1 channel B

It uploads different deterministic programs to DAC output sources 0 and 1,
zeros sources 2 and 3, sweeps RW2[4:3] TX lane modes, captures ADC sources
0..3, and scores the reconstructed ADC converter streams against the expected
waveforms. RW2[15:8] is TX polarity and is deliberately not touched.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import serial

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import capture_plot_adc_uart as cap  # noqa: E402


def corr(a, b):
    if len(a) != len(b) or not a:
        return 0.0
    am = sum(a) / len(a)
    bm = sum(b) / len(b)
    av = [x - am for x in a]
    bv = [x - bm for x in b]
    denom = math.sqrt(sum(x * x for x in av) * sum(x * x for x in bv))
    if denom == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(av, bv)) / denom


def expected_trapezoid(sample_count, frequency_mhz, sample_rate_mhz, amplitude, offset):
    period = int(round((sample_rate_mhz * 1.0e6) / (frequency_mhz * 1.0e6)))
    period = max(64, ((period + 3) // 4) * 4)
    return [
        int(round(cap.trapezoid_value(i, period, amplitude, offset)))
        for i in range(sample_count)
    ]


def best_match(samples, expected, skip, max_shift):
    samples = list(samples[skip:])
    expected = list(expected[skip:])
    n = min(len(samples), len(expected))
    samples = samples[:n]
    expected = expected[:n]
    if n == 0:
        return (0.0, 0, False)

    best = (-2.0, 0, False)
    max_shift = min(max_shift, n - 1)
    for shift in range(max_shift + 1):
        shifted = expected[shift:] + expected[:shift]
        for inverted in (False, True):
            ref = [-x for x in shifted] if inverted else shifted
            score = corr(samples, ref)
            if score > best[0]:
                best = (score, shift, inverted)
    return best


def capture_once(port, words):
    presync, frame_words = cap.capture_frames(port, "PCAP", words)
    captures = cap.split_frame_captures(frame_words)
    streams = cap.build_converter_streams(captures)
    return presync, captures, streams


def write_outputs(outdir, prefix, captures, streams, plot_words, max_points):
    source_tag = "0_1_2_3"
    csv_path = outdir / f"{prefix}_sources_{source_tag}.csv"
    combined_csv_path = outdir / f"{prefix}_sources_{source_tag}_combined.csv"
    png_path = outdir / f"{prefix}_sources_{source_tag}.png"
    summary_path = outdir / f"{prefix}_sources_{source_tag}_summary.txt"

    cap.write_csv(csv_path, captures)
    cap.write_combined_csv(combined_csv_path, streams)
    summaries = []
    for source in range(4):
        summaries.append(cap.summarize(source, captures[source], b""))
    for name, samples in streams.items():
        summaries.append(cap.summarize_stream(name, samples))
    summary_path.write_text("\n\n".join(summaries) + "\n")
    cap.write_plot(png_path, captures, plot_words, max_points, False, False)
    return csv_path, combined_csv_path, png_path, summary_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--expect-build-id", type=lambda x: int(x, 0), default=0xDA010034)
    parser.add_argument("--rw2-base", type=lambda x: int(x, 0), default=0x01000018)
    parser.add_argument(
        "--tx-lane-modes",
        default="0,1,2,3",
        help="Comma-separated TX lane modes for RW2[4:3].",
    )
    parser.add_argument("--words", type=int, default=4096)
    parser.add_argument("--program-words", type=int, default=8192)
    parser.add_argument("--sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--ch0-frequency-mhz", type=float, default=5.0)
    parser.add_argument("--ch1-frequency-mhz", type=float, default=10.0)
    parser.add_argument("--amplitude", type=lambda x: int(x, 0), default=0x3000)
    parser.add_argument("--offset", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--skip", type=int, default=400)
    parser.add_argument("--max-shift", type=int, default=400)
    parser.add_argument("--outdir", default=r"D:\DAVIS\Research\HighSpeedDAQ\daq_captures")
    parser.add_argument("--prefix", default="dac_pair_mapper_sweep")
    parser.add_argument("--plot-words", type=int, default=1024)
    parser.add_argument("--max-points", type=int, default=2000)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    ch0_program = cap.make_trapezoid_program(
        args.program_words,
        args.ch0_frequency_mhz * 1.0e6,
        args.sample_rate_mhz * 1.0e6,
        args.amplitude,
        args.offset,
        "twos",
    )
    ch1_program = cap.make_trapezoid_program(
        args.program_words,
        args.ch1_frequency_mhz * 1.0e6,
        args.sample_rate_mhz * 1.0e6,
        args.amplitude,
        args.offset,
        "twos",
    )
    zero_program = [0] * args.program_words

    with serial.Serial(args.port, args.baud, timeout=120.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        cap.check_build_id(port, args.expect_build_id)

        for channel, program in (
            (0, ch0_program),
            (1, ch1_program),
            (2, zero_program),
            (3, zero_program),
        ):
            print(f"Uploading {len(program)} words to DAC source {channel}...")
            cap.upload_program(port, program, channel)
            cap.verify_program_upload(port, program, channel, 4)

        expected0 = expected_trapezoid(
            args.words * 4,
            args.ch0_frequency_mhz,
            args.sample_rate_mhz,
            args.amplitude,
            args.offset,
        )
        expected1 = expected_trapezoid(
            args.words * 4,
            args.ch1_frequency_mhz,
            args.sample_rate_mhz,
            args.amplitude,
            args.offset,
        )

        tx_lane_modes = [
            int(item, 0) for item in args.tx_lane_modes.split(",") if item.strip()
        ]

        rows = []
        for tx_lane in tx_lane_modes:
            rw2 = (args.rw2_base & ~(3 << 3)) | (tx_lane << 3)
            cap.set_rw2(port, rw2)
            case_prefix = f"{args.prefix}_txlane_{tx_lane}"
            print(f"Capturing tx_lane={tx_lane} RW2=0x{rw2:08X}...")
            _presync, captures, streams = capture_once(port, args.words)
            write_outputs(outdir, case_prefix, captures, streams, args.plot_words, args.max_points)

            conv0 = streams.get("adc1_converter0", [])
            conv1 = streams.get("adc1_converter1", [])
            c0_to_ch0 = best_match(conv0, expected0, args.skip, args.max_shift)
            c0_to_ch1 = best_match(conv0, expected1, args.skip, args.max_shift)
            c1_to_ch0 = best_match(conv1, expected0, args.skip, args.max_shift)
            c1_to_ch1 = best_match(conv1, expected1, args.skip, args.max_shift)
            rows.append((tx_lane, rw2, c0_to_ch0, c0_to_ch1, c1_to_ch0, c1_to_ch1))

        print("\nTX-lane scores: correlation, shift, inverted")
        for tx_lane, rw2, c0_ch0, c0_ch1, c1_ch0, c1_ch1 in rows:
            print(
                f"tx_lane={tx_lane} RW2=0x{rw2:08X} "
                f"ADC_A<-DAC0 {c0_ch0[0]:+.3f}/{c0_ch0[1]}/{int(c0_ch0[2])} "
                f"ADC_A<-DAC1 {c0_ch1[0]:+.3f}/{c0_ch1[1]}/{int(c0_ch1[2])} "
                f"ADC_B<-DAC0 {c1_ch0[0]:+.3f}/{c1_ch0[1]}/{int(c1_ch0[2])} "
                f"ADC_B<-DAC1 {c1_ch1[0]:+.3f}/{c1_ch1[1]}/{int(c1_ch1[2])}"
            )


if __name__ == "__main__":
    main()
