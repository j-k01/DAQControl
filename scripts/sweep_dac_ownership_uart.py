#!/usr/bin/env python3
"""Identify which DAC source appears on each ADC loopback stream.

This is the ownership sweep for the native DAC path:
  source0..3 -> LiteJESD converter0..3, no byte mapper.

It uploads a distinct sine tone to each DAC source, sweeps TX lane modes,
captures ADC1 converter streams, and scores ADC A/B against every DAC source.
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
    denom = math.sqrt(sum(x * x for x in av) * sum(y * y for y in bv))
    if denom == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(av, bv)) / denom


def expected_sine(sample_count, program_sample_count, cycles, amplitude, offset):
    return [
        int(round(offset + amplitude * math.sin(
            2.0 * math.pi * cycles * index / program_sample_count
        )))
        for index in range(sample_count)
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


def parse_csv_floats(text):
    values = [float(item) for item in text.replace(",", " ").split()]
    if len(values) != 4:
        raise argparse.ArgumentTypeError("expected exactly four values")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--expect-build-id", type=lambda x: int(x, 0), default=0xDA010031)
    parser.add_argument("--rw2-base", type=lambda x: int(x, 0), default=0x010000F8)
    parser.add_argument("--sample-map-modes", default="0")
    parser.add_argument("--tx-lane-modes", default="0,1,2,3")
    parser.add_argument("--words", type=int, default=2048)
    parser.add_argument("--program-words", type=int, default=8192)
    parser.add_argument("--sine-cycles", type=parse_csv_floats, default=[1600.0, 2000.0, 2400.0, 2800.0])
    parser.add_argument("--amplitude", type=lambda x: int(x, 0), default=0x3000)
    parser.add_argument("--offset", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--skip", type=int, default=400)
    parser.add_argument("--max-shift", type=int, default=1600)
    parser.add_argument("--outdir", default=r"D:\DAVIS\Research\HighSpeedDAQ\daq_captures")
    parser.add_argument("--prefix", default="dac_ownership_sweep")
    parser.add_argument("--plot-words", type=int, default=1024)
    parser.add_argument("--max-points", type=int, default=2000)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    programs = [
        cap.make_sine_program(
            args.program_words,
            cycles,
            args.amplitude,
            args.offset,
            "twos",
        )
        for cycles in args.sine_cycles
    ]

    program_sample_count = args.program_words * 2
    expected = [
        expected_sine(
            args.words * 4,
            program_sample_count,
            cycles,
            args.amplitude,
            args.offset,
        )
        for cycles in args.sine_cycles
    ]

    tx_lane_modes = [
        int(item, 0) for item in args.tx_lane_modes.split(",") if item.strip()
    ]
    sample_map_modes = [
        int(item, 0) for item in args.sample_map_modes.split(",") if item.strip()
    ]

    with serial.Serial(args.port, args.baud, timeout=120.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        cap.check_build_id(port, args.expect_build_id)

        for channel, program in enumerate(programs):
            print(f"Uploading source{channel}: {args.sine_cycles[channel]} cycles / {program_sample_count} samples")
            cap.upload_program(port, program, channel)
            cap.verify_program_upload(port, program, channel, 4)

        rows = []
        for sample_map in sample_map_modes:
            for tx_lane in tx_lane_modes:
                rw2 = (
                    args.rw2_base
                    & ~((3 << 1) | (3 << 3))
                ) | (sample_map << 1) | (tx_lane << 3)
                cap.set_rw2(port, rw2)
                case_prefix = f"{args.prefix}_smap_{sample_map}_txlane_{tx_lane}"
                print(f"Capturing sample_map={sample_map} tx_lane={tx_lane} RW2=0x{rw2:08X}...")
                _presync, captures, streams = capture_once(port, args.words)
                write_outputs(outdir, case_prefix, captures, streams, args.plot_words, args.max_points)

                adc_a = streams.get("adc1_converter0", [])
                adc_b = streams.get("adc1_converter1", [])
                scores_a = [best_match(adc_a, ref, args.skip, args.max_shift) for ref in expected]
                scores_b = [best_match(adc_b, ref, args.skip, args.max_shift) for ref in expected]
                rows.append((sample_map, tx_lane, rw2, scores_a, scores_b))

        print("\nOwnership scores: correlation/shift/inverted")
        print("source cycles:", " ".join(str(cycles) for cycles in args.sine_cycles))
        for sample_map, tx_lane, rw2, scores_a, scores_b in rows:
            best_a = max(range(4), key=lambda idx: scores_a[idx][0])
            best_b = max(range(4), key=lambda idx: scores_b[idx][0])
            print(f"sample_map={sample_map} tx_lane={tx_lane} RW2=0x{rw2:08X}")
            print(
                "  ADC_A:",
                " ".join(
                    f"src{idx}={score[0]:+.3f}/{score[1]}/{int(score[2])}"
                    for idx, score in enumerate(scores_a)
                ),
                f"best=src{best_a}",
            )
            print(
                "  ADC_B:",
                " ".join(
                    f"src{idx}={score[0]:+.3f}/{score[1]}/{int(score[2])}"
                    for idx, score in enumerate(scores_b)
                ),
                f"best=src{best_b}",
            )


if __name__ == "__main__":
    main()
