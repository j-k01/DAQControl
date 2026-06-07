#!/usr/bin/env python3
"""Program a generated waveform into DAC0 BRAM over the PL UART.

The DAC program BRAM stores 32-bit little-endian words. Each word carries two
chronological signed 16-bit DAC samples. The fabric reads two 32-bit words per
64-bit DAC beat, so the default 8192 u32 words form 4096 64-bit frames.
"""

from __future__ import annotations

import argparse
import math
import re
import struct
import time
from typing import Iterable


MAX_PROGRAM_WORDS = 8192
DAC_NORMAL_RW2 = 0x01000018  # sample_map=0, tx_lane=3; source selection is NSRC.


def parse_int(text: str) -> int:
    return int(text, 0)


def clamp_s16(value: float) -> int:
    rounded = int(round(value))
    return max(-32768, min(32767, rounded))


def pack_pair(sample0: int, sample1: int) -> int:
    s0 = sample0 & 0xFFFF
    s1 = sample1 & 0xFFFF
    return s0 | (s1 << 16)


def waveform_sample(
    shape: str,
    index: int,
    sample_rate_hz: float,
    frequency_hz: float,
    amplitude: int,
    offset: int,
    pulse_width_samples: int,
) -> int:
    phase = (index * frequency_hz / sample_rate_hz) % 1.0

    if shape == "sine":
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
    elif shape == "pulse":
        period_samples = max(1, int(round(sample_rate_hz / frequency_hz)))
        value = offset + amplitude if (index % period_samples) < pulse_width_samples else offset
    elif shape == "trapulse":
        period_samples = max(1, int(round(sample_rate_hz / frequency_hz)))
        pulse_phase = index % period_samples
        if pulse_phase >= pulse_width_samples:
            value = offset
        elif pulse_width_samples == 7:
            # Match the hardware IZH spike pulse shape exactly for the bring-up
            # case: 7 samples total at 1 GSPS.
            profile = (0.25, 0.5, 1.0, 1.0, 1.0, 0.5, 0.25)
            value = offset + amplitude * profile[pulse_phase]
        else:
            midpoint = max(1, pulse_width_samples - 1)
            normalized = pulse_phase / midpoint
            if normalized < 0.25:
                value = offset + amplitude * (normalized / 0.25)
            elif normalized < 0.75:
                value = offset + amplitude
            else:
                value = offset + amplitude * ((1.0 - normalized) / 0.25)
    elif shape == "squarewave":
        value = offset + amplitude if phase < 0.5 else offset - amplitude
    else:
        raise ValueError(f"unsupported waveform shape {shape!r}")

    return clamp_s16(value)


def make_program(args: argparse.Namespace) -> list[int]:
    sample_rate_hz = args.sample_rate_mhz * 1.0e6
    frequency_hz = args.frequency_mhz * 1.0e6
    pulse_width_samples = max(1, int(round(args.pulse_width_ns * sample_rate_hz / 1.0e9)))

    words: list[int] = []
    for word_index in range(args.words):
        sample0 = waveform_sample(
            args.shape,
            2 * word_index,
            sample_rate_hz,
            frequency_hz,
            args.amplitude,
            args.offset,
            pulse_width_samples,
        )
        sample1 = waveform_sample(
            args.shape,
            2 * word_index + 1,
            sample_rate_hz,
            frequency_hz,
            args.amplitude,
            args.offset,
            pulse_width_samples,
        )
        words.append(pack_pair(sample0, sample1))
    return words


def read_line(port: serial.Serial) -> str:
    line = port.readline()
    if not line:
        raise TimeoutError("timed out waiting for UART line")
    return line.decode("ascii", errors="replace").strip()


def wait_prefix(port: serial.Serial, prefix: str) -> str:
    while True:
        line = read_line(port)
        if line.startswith(prefix):
            return line


def send_wait(port: serial.Serial, command: str, prefix: str = "OK") -> str:
    port.write((command + "\n").encode("ascii"))
    port.flush()
    return wait_prefix(port, prefix)


def parse_reg(line: str, name: str) -> int:
    match = re.match(rf"^{name}\s*=\s*0x([0-9a-fA-F]{{8}})$", line)
    if not match:
        raise ValueError(f"unexpected {name} line: {line!r}")
    return int(match.group(1), 16)


def read_rw(port: serial.Serial, index: int) -> int:
    port.write(f"RDRW {index}\n".encode("ascii"))
    port.flush()
    return parse_reg(wait_prefix(port, f"RW{index}"), f"RW{index}")


def read_ro(port: serial.Serial, index: int) -> int:
    port.write(f"RDRO {index}\n".encode("ascii"))
    port.flush()
    return parse_reg(wait_prefix(port, f"RO{index}"), f"RO{index}")


def check_build_id(port: serial.Serial, expected: int) -> None:
    old_rw1 = read_rw(port, 1)
    print(send_wait(port, "WRTE 1 3"))
    actual = read_ro(port, 3)
    print(send_wait(port, f"WRTE 1 0x{old_rw1:08X}"))
    if actual != expected:
        raise ValueError(f"build ID mismatch: expected 0x{expected:08X}, got 0x{actual:08X}")
    print(f"Build ID OK: 0x{actual:08X}")


def upload_dac0_program(port: serial.Serial, words: Iterable[int]) -> int:
    word_list = list(words)
    if not word_list or len(word_list) > MAX_PROGRAM_WORDS:
        raise ValueError(f"program word count must be 1..{MAX_PROGRAM_WORDS}")
    if len(word_list) & 1:
        raise ValueError("program word count must be even so DAC frames are 64-bit aligned")

    port.write(f"PROG 0 {len(word_list)}\n".encode("ascii"))
    port.flush()
    print(wait_prefix(port, "PGRD"))
    port.write(struct.pack(f"<{len(word_list)}I", *word_list))
    port.flush()
    print(wait_prefix(port, "OK PROG"))
    return len(word_list) // 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10", help="PL UART port.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--shape",
        choices=[
            "sine",
            "triangle",
            "trapezoid",
            "trapulse",
            "trapezoid-pulse",
            "pulse",
            "squarewave",
            "squareweave",
            "square",
        ],
        default="sine",
    )
    parser.add_argument("--frequency-mhz", type=float, default=100.0)
    parser.add_argument("--sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--amplitude", type=parse_int, default=0x6000, help="Signed DAC counts.")
    parser.add_argument("--offset", type=parse_int, default=0, help="Signed DAC counts.")
    parser.add_argument("--pulse-width-ns", type=float, default=7.0)
    parser.add_argument("--words", type=int, default=MAX_PROGRAM_WORDS, help="u32 program words.")
    parser.add_argument("--expect-build-id", type=parse_int, default=None)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--leave-all-active",
        action="store_true",
        help="Deprecated; RW2 no longer gates DAC channels. Source selection is done with NSRC.",
    )
    args = parser.parse_args()

    if args.shape in ("square", "squareweave"):
        args.shape = "squarewave"
    if args.shape == "trapezoid-pulse":
        args.shape = "trapulse"

    program = make_program(args)
    frame_count = len(program) // 2
    rw2 = DAC_NORMAL_RW2
    rw3_run = ((frame_count << 8) & 0xFFFFFF00) | 0x60

    print(
        f"DAC0 {args.shape}: f={args.frequency_mhz:g} MHz "
        f"amp={args.amplitude} offset={args.offset} words={len(program)} frames={frame_count}"
    )

    try:
        import serial
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required to open the UART. Run this from a Python environment "
            "that has pyserial installed."
        ) from exc

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        if args.expect_build_id is not None:
            check_build_id(port, args.expect_build_id)

        frame_count = upload_dac0_program(port, program)
        print(send_wait(port, "NSRC all dds", "DAC source"))
        print(send_wait(port, "NSRC 0 bram", "DAC source"))
        print(send_wait(port, f"WRTE 2 0x{rw2:08X}"))
        print(send_wait(port, f"WRTE 3 0x{rw3_run | 0x08:08X}"))
        print(send_wait(port, f"WRTE 3 0x{rw3_run:08X}"))
        port.write(b"STAT\n")
        time.sleep(0.5)
        print(port.read_all().decode("ascii", errors="replace"))


if __name__ == "__main__":
    main()
