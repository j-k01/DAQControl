#!/usr/bin/env python3
"""Program four DAC source waveforms through the discovered byte preimage.

This is a diagnostic for the current DAC/LiteJESD byte mapping.  It generates
four natural source streams, each as four chronological signed 16-bit samples
per 64-bit source frame:

    source = {t3, t2, t1, t0}

where each sample is two bytes.  It then writes the converter BRAMs with the
byte preimage:

    first CPU u32, for t1/t0:
        converter0 = {0D, 1C, 0B, 1A}
        converter1 = {1D, 0C, 1B, 0A}
        converter2 = {3C, 2C, 3A, 2A}
        converter3 = {2D, 3D, 2B, 3B}

    second CPU u32, for t3/t2:
        converter0 = {0H, 1G, 0F, 1E}
        converter1 = {1H, 0G, 1F, 0E}
        converter2 = {3G, 2G, 3E, 2E}
        converter3 = {2H, 3H, 2F, 3F}

For a source frame named HHGG_FFEE_DDCC_BBAA:
    A/E/etc are low bytes, B/F/etc are high bytes.

Default test:
    clear all converter BRAMs, then program source2 with a trapezoid and
    leave sources 0, 1, and 3 at zero.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import dac_bram_u32_uart as bram_uart  # noqa: E402


MAX_FRAMES = 4096
DEFAULT_RW2 = 0x01000018


def parse_int(text: str) -> int:
    return int(text, 0)


def clamp_s16(value: float) -> int:
    rounded = int(round(value))
    return max(-32768, min(32767, rounded))


def sample_u16(value: int) -> int:
    return value & 0xFFFF


def waveform_sample(
    shape: str,
    index: int,
    sample_rate_hz: float,
    frequency_hz: float,
    amplitude: int,
    offset: int,
) -> int:
    phase = (index * frequency_hz / sample_rate_hz) % 1.0

    if shape == "zero":
        value = offset
    elif shape == "sine":
        value = offset + amplitude * math.sin(2.0 * math.pi * phase)
    elif shape == "triangle":
        if phase < 0.5:
            value = offset - amplitude + (4.0 * amplitude * phase)
        else:
            value = offset + amplitude - (4.0 * amplitude * (phase - 0.5))
    elif shape == "trapezoid":
        if phase < 0.25:
            value = offset - amplitude + (8.0 * amplitude * phase)
        elif phase < 0.5:
            value = offset + amplitude
        elif phase < 0.75:
            value = offset + amplitude - (8.0 * amplitude * (phase - 0.5))
        else:
            value = offset - amplitude
    elif shape == "square":
        value = offset + amplitude if phase < 0.5 else offset - amplitude
    else:
        raise ValueError(f"unknown waveform shape {shape!r}")

    return clamp_s16(value)


def source_bytes(samples: list[int], frame: int, source: int) -> dict[str, int]:
    """Return A..H bytes for one 64-bit source frame.

    t0 = BBAA, t1 = DDCC, t2 = FFEE, t3 = HHGG.
    """
    base = frame * 4
    t0 = sample_u16(samples[source][base + 0])
    t1 = sample_u16(samples[source][base + 1])
    t2 = sample_u16(samples[source][base + 2])
    t3 = sample_u16(samples[source][base + 3])
    return {
        "A": t0 & 0xFF,
        "B": (t0 >> 8) & 0xFF,
        "C": t1 & 0xFF,
        "D": (t1 >> 8) & 0xFF,
        "E": t2 & 0xFF,
        "F": (t2 >> 8) & 0xFF,
        "G": t3 & 0xFF,
        "H": (t3 >> 8) & 0xFF,
    }


def pack_u32_bytes(byte3_to_byte0: list[int]) -> int:
    if len(byte3_to_byte0) != 4:
        raise ValueError("expected 4 bytes")
    value = 0
    for byte in byte3_to_byte0:
        value = (value << 8) | (byte & 0xFF)
    return value


def build_converter_programs(
    source_samples: list[list[int]],
    frames: int,
) -> list[list[int]]:
    """Return four converter BRAM programs, as u32 words."""
    programs = [[] for _ in range(4)]

    for frame in range(frames):
        s0 = source_bytes(source_samples, frame, 0)
        s1 = source_bytes(source_samples, frame, 1)
        s2 = source_bytes(source_samples, frame, 2)
        s3 = source_bytes(source_samples, frame, 3)

        # CPU writes 32-bit little-endian words at sequential addresses.  The
        # fabric BRAM port reads two adjacent CPU words as one 64-bit frame:
        #     fabric64 = {u32[address + 1], u32[address]}
        # Therefore append the t1/t0 preimage word first, then the t3/t2 word.
        conv0_w0 = pack_u32_bytes([s0["D"], s1["C"], s0["B"], s1["A"]])
        conv0_w1 = pack_u32_bytes([s0["H"], s1["G"], s0["F"], s1["E"]])
        conv1_w0 = pack_u32_bytes([s1["D"], s0["C"], s1["B"], s0["A"]])
        conv1_w1 = pack_u32_bytes([s1["H"], s0["G"], s1["F"], s0["E"]])
        conv2_w0 = pack_u32_bytes([s3["C"], s2["C"], s3["A"], s2["A"]])
        conv2_w1 = pack_u32_bytes([s3["G"], s2["G"], s3["E"], s2["E"]])
        conv3_w0 = pack_u32_bytes([s2["D"], s3["D"], s2["B"], s3["B"]])
        conv3_w1 = pack_u32_bytes([s2["H"], s3["H"], s2["F"], s3["F"]])

        for channel, (word0, word1) in enumerate((
            (conv0_w0, conv0_w1),
            (conv1_w0, conv1_w1),
            (conv2_w0, conv2_w1),
            (conv3_w0, conv3_w1),
        )):
            programs[channel].append(word0)
            programs[channel].append(word1)

    return programs


def generate_sources(args: argparse.Namespace) -> list[list[int]]:
    sample_rate_hz = args.sample_rate_mhz * 1.0e6
    shapes = [args.shape0, args.shape1, args.shape2, args.shape3]
    freqs_hz = [
        args.freq0_mhz * 1.0e6,
        args.freq1_mhz * 1.0e6,
        args.freq2_mhz * 1.0e6,
        args.freq3_mhz * 1.0e6,
    ]
    amps = [args.amp0, args.amp1, args.amp2, args.amp3]
    offsets = [args.offset0, args.offset1, args.offset2, args.offset3]

    sample_count = args.frames * 4
    sources: list[list[int]] = []
    for source in range(4):
        sources.append([
            waveform_sample(
                shapes[source],
                index,
                sample_rate_hz,
                freqs_hz[source],
                amps[source],
                offsets[source],
            )
            for index in range(sample_count)
        ])
    return sources


def print_preview(programs: list[list[int]], frames: int) -> None:
    preview_frames = min(frames, 4)
    print("First converter BRAM frames after preimage:")
    for channel, words in enumerate(programs):
        print(f"  converter BRAM channel {channel}:")
        for frame in range(preview_frames):
            lo32 = words[2 * frame]
            hi32 = words[2 * frame + 1]
            print(f"    frame {frame:4d}: 0x{hi32:08X}_{lo32:08X}")


def play(port: serial.Serial, frames: int, rw2: int, echo: bool) -> None:
    rw3_run = ((frames << 8) & 0xFFFFFF00) | 0x60
    bram_uart.send_wait(port, f"WRTE 2 0x{rw2:08X}", echo=echo)
    bram_uart.send_wait(port, "NSRC all bram", prefix="DAC source", echo=echo)
    bram_uart.send_wait(port, f"WRTE 3 0x{rw3_run | 0x08:08X}", echo=echo)
    bram_uart.send_wait(port, f"WRTE 3 0x{rw3_run:08X}", echo=echo)
    bram_uart.send_wait(port, "RDRW 2", prefix="RW2", echo=echo)
    bram_uart.send_wait(port, "RDRW 3", prefix="RW3", echo=echo)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--frames", type=int, default=MAX_FRAMES)
    parser.add_argument("--sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--rw2", type=parse_int, default=DEFAULT_RW2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-clear", dest="clear_first", action="store_false")
    parser.add_argument("--no-play", action="store_true")
    parser.add_argument("--no-verify", dest="verify", action="store_false")
    parser.add_argument("--quiet-uart", dest="echo_uart", action="store_false")
    parser.set_defaults(verify=True, echo_uart=True, clear_first=True)

    for source, default_shape, default_freq in (
        (0, "zero", 50.0),
        (1, "zero", 100.0),
        (2, "trapezoid", 50.0),
        (3, "zero", 75.0),
    ):
        parser.add_argument(
            f"--shape{source}",
            choices=("zero", "trapezoid", "sine", "square", "triangle"),
            default=default_shape,
        )
        parser.add_argument(f"--freq{source}-mhz", type=float, default=default_freq)
        parser.add_argument(f"--amp{source}", type=parse_int, default=0x4000)
        parser.add_argument(f"--offset{source}", type=parse_int, default=0)

    args = parser.parse_args()

    if args.frames <= 0 or args.frames > MAX_FRAMES:
        raise SystemExit(f"--frames must be 1..{MAX_FRAMES}")

    sources = generate_sources(args)
    programs = build_converter_programs(sources, args.frames)
    print_preview(programs, args.frames)
    print(
        "Source waveforms: "
        f"src0={args.shape0}@{args.freq0_mhz:g}MHz, "
        f"src1={args.shape1}@{args.freq1_mhz:g}MHz, "
        f"src2={args.shape2}@{args.freq2_mhz:g}MHz, "
        f"src3={args.shape3}@{args.freq3_mhz:g}MHz"
    )

    if args.dry_run:
        return

    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required; run from a Python environment with pyserial installed") from exc

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        if args.clear_first:
            print("Clearing all four converter BRAMs")
            zero_words = [0] * (args.frames * 2)
            for channel in range(4):
                bram_uart.write_words(port, channel, 0, zero_words, args.echo_uart)

        for channel, words in enumerate(programs):
            print(f"Writing converter BRAM channel {channel}: {len(words)} u32 words")
            bram_uart.write_words(port, channel, 0, words, args.echo_uart)
            if args.verify:
                readback = bram_uart.read_words(port, channel, 0, min(16, len(words)), args.echo_uart)
                if readback != words[:len(readback)]:
                    raise RuntimeError(f"verify failed on converter BRAM channel {channel}")

        if not args.no_play:
            play(port, args.frames, args.rw2, args.echo_uart)


if __name__ == "__main__":
    main()
