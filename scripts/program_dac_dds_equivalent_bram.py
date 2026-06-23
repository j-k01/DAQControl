#!/usr/bin/env python3
"""Program a DAC BRAM with the same sine sequence used by the HDL DDS.

This is a like-for-like diagnostic: it uses the HDL quarter-wave table,
default phase increment, and normal 64-bit source-word packing:

  word0 = {sample1, sample0}
  word1 = {sample3, sample2}

The selected DAC BRAM channel is filled with the DDS-equivalent stream. Other
channels are zeroed by default, then playback is switched to BRAM.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import dac_bram_u32_uart as bram  # noqa: E402


DEFAULT_DDS_STEP = 0x19999A
PHASE_MODULUS = 1 << 24
DAC_CHANNELS = range(4)

SINE_QUARTER = [
    0, 817, 1633, 2449, 3263, 4074, 4884, 5690,
    6493, 7291, 8085, 8875, 9658, 10436, 11207, 11971,
    12728, 13477, 14217, 14949, 15671, 16383, 17086, 17778,
    18458, 19128, 19785, 20430, 21062, 21681, 22287, 22879,
    23457, 24020, 24568, 25101, 25618, 26120, 26605, 27073,
    27525, 27960, 28377, 28777, 29158, 29522, 29867, 30194,
    30502, 30791, 31061, 31311, 31542, 31754, 31945, 32117,
    32269, 32401, 32513, 32604, 32675, 32726, 32757, 32767,
]


def s16_to_u16(value: int) -> int:
    return value & 0xFFFF


def sine_from_phase(phase: int) -> int:
    phase &= 0xFFFFFF
    quadrant = (phase >> 22) & 0x3
    index = (phase >> 16) & 0x3F

    if quadrant == 0:
        return SINE_QUARTER[index]
    if quadrant == 1:
        return SINE_QUARTER[index ^ 0x3F]
    if quadrant == 2:
        return -SINE_QUARTER[index]
    return -SINE_QUARTER[index ^ 0x3F]


def pack_pair(sample0: int, sample1: int) -> int:
    return (s16_to_u16(sample1) << 16) | s16_to_u16(sample0)


def build_dds_words(frames: int, step: int) -> list[int]:
    words: list[int] = []
    phase = 0

    for _ in range(frames):
        samples = [sine_from_phase(phase + step * i) for i in range(4)]
        words.append(pack_pair(samples[0], samples[1]))
        words.append(pack_pair(samples[2], samples[3]))
        phase = (phase + step * 4) & 0xFFFFFF

    return words


def loop_phase_error(frames: int, step: int) -> int:
    samples = frames * 4
    phase_after_loop = (samples * step) % PHASE_MODULUS
    return min(phase_after_loop, PHASE_MODULUS - phase_after_loop)


def parse_channel(text: str) -> int | str:
    if text.lower() == "all":
        return "all"
    value = int(text, 0)
    if value not in DAC_CHANNELS:
        raise argparse.ArgumentTypeError("channel must be 0..3 or all")
    return value


def selected_channels(channel: int | str) -> list[int]:
    return list(DAC_CHANNELS) if channel == "all" else [int(channel)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--channel",
        type=parse_channel,
        default="all",
        help=(
            "DAC BRAM source to program. Default 'all' is the only true "
            "like-for-like comparison with the default DDS broadcast path."
        ),
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=4095,
        help=(
            "64-bit BRAM frames to loop. Default 4095 is intentionally a "
            "multiple of 5, making the default 0x19999A DDS step wrap "
            "nearly phase-continuously. 4096 frames produces a visible loop "
            "discontinuity."
        ),
    )
    parser.add_argument("--step", type=bram.parse_u32, default=DEFAULT_DDS_STEP)
    parser.add_argument("--rw2", type=bram.parse_u32, default=0x01000018)
    parser.add_argument(
        "--zero-others",
        action="store_true",
        help="When programming one channel, zero the other BRAM sources.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet-uart", dest="echo_uart", action="store_false")
    parser.set_defaults(echo_uart=True)
    args = parser.parse_args()

    if args.frames <= 0 or args.frames > 4096:
        raise SystemExit("--frames must be 1..4096")

    words = build_dds_words(args.frames, args.step)
    phase_error = loop_phase_error(args.frames, args.step)
    print(
        f"DDS-equivalent BRAM: channel={args.channel} "
        f"frames={args.frames} words={len(words)} step=0x{args.step:06X}"
    )
    print(
        "loop phase error: "
        f"0x{phase_error:06X} ({phase_error / PHASE_MODULUS:.9f} cycles)"
    )
    if phase_error > 0x10000:
        print(
            "WARNING: this BRAM loop is not phase-continuous. The scope can "
            "show a sine with apparent jitter even when every BRAM word is "
            "read correctly."
        )
    print("first u32 words:", " ".join(f"0x{word:08X}" for word in words[:12]))
    first_samples = []
    for word in words[:8]:
        first_samples.extend([word & 0xFFFF, (word >> 16) & 0xFFFF])
    print("first samples:", " ".join(f"0x{sample:04X}" for sample in first_samples[:16]))

    if args.dry_run:
        return

    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required; run from a Python environment with pyserial installed") from exc

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        import time

        time.sleep(0.2)
        port.reset_input_buffer()

        bram.send_wait(port, f"WRTE 2 0x{args.rw2:08X}", echo=args.echo_uart)
        channels = selected_channels(args.channel)
        if args.zero_others and args.channel != "all":
            zero_words = [0] * len(words)
            for channel in DAC_CHANNELS:
                if channel not in channels:
                    bram.write_words(port, channel, 0, zero_words, args.echo_uart)
        for channel in channels:
            bram.write_words(port, channel, 0, words, args.echo_uart)
        bram.send_wait(port, "NSRC all bram", prefix="DAC source", echo=args.echo_uart)
        bram.send_wait(port, f"WRTE 3 0x{((args.frames << 8) & 0xFFFFFF00) | 0x68:08X}", echo=args.echo_uart)
        bram.send_wait(port, f"WRTE 3 0x{((args.frames << 8) & 0xFFFFFF00) | 0x60:08X}", echo=args.echo_uart)
        bram.send_wait(port, "RDRW 2", prefix="RW2", echo=args.echo_uart)
        bram.send_wait(port, "RDRW 3", prefix="RW3", echo=args.echo_uart)


if __name__ == "__main__":
    main()
