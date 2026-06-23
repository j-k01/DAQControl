#!/usr/bin/env python3
"""Program DAC BRAM scope-test patterns over the PL UART.

This is a scope-side diagnostic tool. It writes one generated waveform/pattern
into one DAC program BRAM channel, zeros the other channels by default, selects
BRAM playback, and loops the requested number of 64-bit DAC frames.

Each DAC BRAM channel is a stream of chronological signed 16-bit DAC samples.
CPU writes are 32-bit words:

  u32 word N     = {sample1, sample0}
  u32 word N + 1 = {sample3, sample2}

The fabric reads those two u32 words as one 64-bit DAC beat.
"""

from __future__ import annotations

import argparse
import math
import re
import struct
import time


MAX_U32_WORDS = 8192
DAC_CHANNELS = range(4)


def parse_u32(text: str) -> int:
    value = int(text, 0)
    if value < 0 or value > 0xFFFFFFFF:
        raise argparse.ArgumentTypeError(f"{text!r} is outside u32 range")
    return value


def clamp_s16(value: float) -> int:
    rounded = int(round(value))
    return max(-32768, min(32767, rounded))


def pack_pair(sample0: int, sample1: int) -> int:
    return ((sample1 & 0xFFFF) << 16) | (sample0 & 0xFFFF)


def swap_bytes16(sample: int) -> int:
    value = sample & 0xFFFF
    return ((value & 0x00FF) << 8) | ((value & 0xFF00) >> 8)


def make_trapezoid_sample(index: int, sample_rate_mhz: float, frequency_mhz: float, amplitude: int) -> int:
    period = max(4, int(round(sample_rate_mhz / frequency_mhz)))
    quarter = max(1, period // 4)
    phase = index % period
    lo = -amplitude
    hi = amplitude

    if phase < quarter:
        return clamp_s16(lo + (hi - lo) * phase / max(1, quarter - 1))
    if phase < 2 * quarter:
        return hi
    if phase < 3 * quarter:
        k = phase - 2 * quarter
        return clamp_s16(hi - (hi - lo) * k / max(1, quarter - 1))
    return lo


def make_sine_sample(index: int, sample_rate_mhz: float, frequency_mhz: float, amplitude: int) -> int:
    phase = (index * frequency_mhz / sample_rate_mhz) % 1.0
    return clamp_s16(amplitude * math.sin(2.0 * math.pi * phase))


def build_samples(args: argparse.Namespace) -> list[int]:
    sample_count = args.words * 2

    if args.test.startswith("tags"):
        tag = [0x1101, 0x2202, 0x3303, 0x4404]
        samples = [tag[i % 4] for i in range(sample_count)]
    elif args.test.startswith("sine"):
        samples = [
            make_sine_sample(i, args.sample_rate_mhz, args.frequency_mhz, args.amplitude)
            for i in range(sample_count)
        ]
    else:
        samples = []
        for i in range(sample_count):
            if args.test == "trap_half" and i >= sample_count // 2:
                samples.append(0)
            else:
                samples.append(
                    make_trapezoid_sample(
                        i,
                        args.sample_rate_mhz,
                        args.frequency_mhz,
                        args.amplitude,
                    )
                )

    if args.test.endswith("_bswap"):
        samples = [swap_bytes16(sample) for sample in samples]

    if args.test.endswith("_rev4"):
        for i in range(0, len(samples), 4):
            if i + 3 < len(samples):
                samples[i : i + 4] = [samples[i + 3], samples[i + 2], samples[i + 1], samples[i]]

    return samples


def build_u32_program(samples: list[int]) -> list[int]:
    return [pack_pair(samples[2 * i], samples[2 * i + 1]) for i in range(len(samples) // 2)]


def print_program_preview(channel: int, args: argparse.Namespace, samples: list[int], words: list[int], loop_frames: int) -> None:
    rw3_run = ((loop_frames << 8) & 0xFFFFFF00) | 0x60
    print(f"test={args.test} channel={channel} rw2=0x{args.rw2:08X} loop_frames={loop_frames} rw3=0x{rw3_run:08X}")
    print("first u32 words:", " ".join(f"0x{word:08X}" for word in words[:8]))
    print("first samples:", " ".join(f"0x{sample & 0xFFFF:04X}" for sample in samples[:16]))

    if args.test == "tags":
        print("scope meaning: correct DAC3 byte pairing gives large 0x1101/0x2202/0x3303/0x4404 levels.")
        print("scope meaning: lane6/lane7 swapped gives much smaller 0x0111/0x0222/0x0333/0x0444 levels.")
    elif args.test == "tags_bswap":
        print("scope meaning: if this is the first tag test with large levels, suspect lane6/lane7 high-low swap.")
    elif args.test == "tags_rev4":
        print("scope meaning: if this is the first sane tag order, suspect 4-sample time reversal.")
    elif args.test == "trap_bswap":
        print("scope meaning: if this cleans the trapezoid, suspect lane6/lane7 high-low swap.")
    elif args.test == "trap_rev4":
        print("scope meaning: if this cleans the trapezoid, suspect 4-sample time/byte reversal.")
    elif args.test == "trap_half":
        print("scope meaning: compare --loop-frames 2048 against --loop-frames 0 for stale-memory/full-loop effects.")


def read_line(port: serial.Serial) -> str:
    line = port.readline()
    if not line:
        raise TimeoutError("timed out waiting for UART line")
    return line.decode("ascii", errors="replace").strip()


def wait_any_prefix(port: serial.Serial, prefixes: tuple[str, ...], echo: bool = True) -> str:
    while True:
        line = read_line(port)
        if echo and line:
            print(line)
        if line.startswith(prefixes):
            return line


def wait_prefix(port: serial.Serial, prefix: str, echo: bool = True) -> str:
    return wait_any_prefix(port, (prefix,), echo)


def send_wait(port: serial.Serial, command: str, prefix: str = "OK", echo: bool = True) -> str:
    port.write((command + "\n").encode("ascii"))
    port.flush()
    return wait_prefix(port, prefix, echo)


def write_words(port: serial.Serial, channel: int, words: list[int], echo: bool) -> None:
    port.write(f"DPWR {channel} 0 {len(words)}\n".encode("ascii"))
    port.flush()
    wait_prefix(port, f"DPWR ch={channel}", echo)
    port.write(struct.pack(f"<{len(words)}I", *words))
    port.flush()
    wait_prefix(port, f"OK DPWR ch={channel}", echo)


def read_words(port: serial.Serial, channel: int, count: int, echo: bool) -> list[int]:
    port.write(f"DPRD {channel} 0 {count}\n".encode("ascii"))
    port.flush()
    wait_prefix(port, "DPRD ch=", echo)

    pattern = re.compile(r"^\s*\d+:\s+0x([0-9a-fA-F]{8})\s*$")
    words: list[int] = []
    while len(words) < count:
        line = read_line(port)
        if echo and line:
            print(line)
        match = pattern.match(line)
        if not match:
            raise RuntimeError(f"unexpected DPRD line: {line!r}")
        words.append(int(match.group(1), 16))
    return words


def run_uart(args: argparse.Namespace, program: list[int], loop_frames: int) -> None:
    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required; run from a Python environment with pyserial installed") from exc

    zero = [0] * args.words
    rw3_run = ((loop_frames << 8) & 0xFFFFFF00) | 0x60

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        send_wait(port, "WRTE 1 3", echo=args.echo_uart)
        port.write(b"RDRO 3\n")
        port.flush()
        wait_prefix(port, "RO3", echo=args.echo_uart)
        send_wait(port, f"WRTE 2 0x{args.rw2:08X}", echo=args.echo_uart)

        if args.zero_others:
            for channel in DAC_CHANNELS:
                if channel != args.channel:
                    write_words(port, channel, zero, args.echo_uart)

        write_words(port, args.channel, program, args.echo_uart)
        send_wait(port, "NSRC all bram", prefix="DAC source", echo=args.echo_uart)
        send_wait(port, f"WRTE 3 0x{rw3_run | 0x08:08X}", echo=args.echo_uart)
        send_wait(port, f"WRTE 3 0x{rw3_run:08X}", echo=args.echo_uart)
        read_words(port, args.channel, min(8, args.words), args.echo_uart)

        port.write(b"STAT\n")
        port.flush()
        time.sleep(0.5)
        status = port.read_all().decode("ascii", errors="replace")
        if status:
            print(status)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--test",
        choices=[
            "tags",
            "tags_bswap",
            "tags_rev4",
            "trap",
            "trap_bswap",
            "trap_rev4",
            "trap_half",
            "sine",
            "sine_bswap",
            "sine_rev4",
        ],
        default="tags",
    )
    parser.add_argument("--channel", type=int, choices=range(4), default=3)
    parser.add_argument("--words", type=int, default=MAX_U32_WORDS)
    parser.add_argument("--loop-frames", type=parse_u32, default=None)
    parser.add_argument("--sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--frequency-mhz", type=float, default=25.0)
    parser.add_argument("--amplitude", type=parse_u32, default=0x5000)
    parser.add_argument("--rw2", type=parse_u32, default=0x01000018)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--keep-others", dest="zero_others", action="store_false")
    parser.add_argument("--quiet-uart", dest="echo_uart", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(zero_others=True, echo_uart=True)
    args = parser.parse_args()

    if args.words <= 0 or args.words > MAX_U32_WORDS:
        raise SystemExit(f"--words must be 1..{MAX_U32_WORDS}")
    if args.words & 1:
        raise SystemExit("--words must be even")

    samples = build_samples(args)
    program = build_u32_program(samples)

    if args.loop_frames is None:
        loop_frames = 1 if args.test.startswith("tags") else len(program) // 2
    else:
        loop_frames = args.loop_frames

    print_program_preview(args.channel, args, samples, program, loop_frames)

    if args.dry_run:
        return

    run_uart(args, program, loop_frames)


if __name__ == "__main__":
    main()
