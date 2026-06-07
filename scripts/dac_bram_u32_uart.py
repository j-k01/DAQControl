#!/usr/bin/env python3
"""Manually read/write DAC program BRAM 32-bit words over the PL UART.

Examples:

  # Read raw u32 words from DAC program BRAM channel 3.
  python scripts/dac_bram_u32_uart.py read --channel 3 --start 0 --count 16

  # Write exact u32 values at 32-bit word address 0.
  python scripts/dac_bram_u32_uart.py write --channel 3 --start 0 0x33331999 0x66664CCC

  # Zero all four DAC program BRAMs for 64 u32 words.
  python scripts/dac_bram_u32_uart.py zero --channel all --start 0 --count 64

The fabric reads two adjacent 32-bit CPU words as one 64-bit DAC BRAM frame.
This tool therefore prints both the raw u32 addresses and the paired 64-bit
frame interpretation.
"""

from __future__ import annotations

import argparse
import re
import struct
import time


DAC_CHANNELS = range(4)


def parse_u32(text: str) -> int:
    value = int(text, 0)
    if value < 0 or value > 0xFFFFFFFF:
        raise argparse.ArgumentTypeError(f"{text!r} is outside u32 range")
    return value


def parse_channel(text: str) -> int | str:
    if text.lower() == "all":
        return "all"
    value = int(text, 0)
    if value not in DAC_CHANNELS:
        raise argparse.ArgumentTypeError("channel must be 0..3 or all")
    return value


def read_line(port: serial.Serial) -> str:
    line = port.readline()
    if not line:
        raise TimeoutError("timed out waiting for UART line")
    return line.decode("ascii", errors="replace").strip()


def wait_any_prefix(port: serial.Serial, prefixes: tuple[str, ...], echo: bool) -> str:
    while True:
        line = read_line(port)
        if echo and line:
            print(line)
        if line.startswith(prefixes):
            return line


def wait_prefix(port: serial.Serial, prefix: str, echo: bool) -> str:
    return wait_any_prefix(port, (prefix,), echo)


def send_wait(port: serial.Serial, command: str, prefix: str = "OK", echo: bool = True) -> str:
    port.write((command + "\n").encode("ascii"))
    port.flush()
    return wait_prefix(port, prefix, echo)


def drain_text(port: serial.Serial, echo: bool) -> None:
    old_timeout = port.timeout
    port.timeout = 0.05
    try:
        while True:
            chunk = port.read(4096)
            if not chunk:
                break
            if echo:
                print(chunk.decode("ascii", errors="replace"), end="")
    finally:
        port.timeout = old_timeout


def upload_binary_words(port: serial.Serial, words: list[int]) -> None:
    port.write(struct.pack(f"<{len(words)}I", *words))
    port.flush()


def prog_from_zero(port: serial.Serial, channel: int, words: list[int], echo: bool) -> None:
    if len(words) & 1:
        raise ValueError("PROG fallback requires an even u32 word count")

    port.write(f"PROG {channel} {len(words)}\n".encode("ascii"))
    port.flush()
    wait_prefix(port, "PGRD", echo)
    upload_binary_words(port, words)
    wait_prefix(port, "OK PROG", echo)


def write_words_with_prog_fallback(
    port: serial.Serial,
    channel: int,
    start: int,
    words: list[int],
    echo: bool,
) -> None:
    """Emulate arbitrary u32 writes on firmware that only has PROG-at-zero."""
    if start == 0 and len(words) % 2 == 0:
        if echo:
            print("DPWR unavailable; falling back to direct PROG ch n starting at address 0")
        prog_from_zero(port, channel, words, echo)
        return

    end = start + len(words)
    total = end if (end % 2 == 0) else end + 1

    if echo:
        print(
            "DPWR unavailable; emulating with DPRD read-modify-write + "
            f"PROG ch {total} from address 0"
        )

    current = read_words(port, channel, 0, total, echo)
    current[start:end] = words
    prog_from_zero(port, channel, current, echo)


def write_words(port: serial.Serial, channel: int, start: int, words: list[int], echo: bool) -> None:
    """Write u32 words, using DPWR when available and PROG emulation otherwise."""
    port.write(f"DPWR {channel} {start} {len(words)}\n".encode("ascii"))
    port.flush()
    first = wait_any_prefix(port, ("DPWR ch=", "ERR"), echo)

    if first.startswith("DPWR ch="):
        upload_binary_words(port, words)
        wait_prefix(port, "OK DPWR", echo)
        return

    drain_text(port, echo)
    write_words_with_prog_fallback(port, channel, start, words, echo)


def read_words(port: serial.Serial, channel: int, start: int, count: int, echo: bool) -> list[int]:
    port.write(f"DPRD {channel} {start} {count}\n".encode("ascii"))
    port.flush()
    wait_prefix(port, "DPRD ch=", echo)

    pattern = re.compile(r"^\s*(\d+):\s+0x([0-9a-fA-F]{8})\s*$")
    words: list[int] = []
    while len(words) < count:
        line = read_line(port)
        if echo and line:
            print(line)
        match = pattern.match(line)
        if not match:
            raise RuntimeError(f"unexpected DPRD output line: {line!r}")
        words.append(int(match.group(2), 16))
    return words


def print_u32_words(start: int, words: list[int]) -> None:
    print("Raw u32 words:")
    for index, word in enumerate(words):
        print(f"  [{start + index:6d}] = 0x{word:08X}")


def print_frame_view(start: int, words: list[int]) -> None:
    print("Paired 64-bit DAC BRAM frame view:")
    for offset in range(0, len(words), 2):
        addr0 = start + offset
        word0 = words[offset]
        word1 = words[offset + 1] if offset + 1 < len(words) else None

        if word1 is None:
            print(f"  addr {addr0}: unpaired u32 0x{word0:08X}")
            break

        samples = (
            word0 & 0xFFFF,
            (word0 >> 16) & 0xFFFF,
            word1 & 0xFFFF,
            (word1 >> 16) & 0xFFFF,
        )
        print(
            f"  frame addrs {addr0}/{addr0 + 1}: "
            f"0x{word1:08X}_{word0:08X} "
            f"samples=[0x{samples[0]:04X},0x{samples[1]:04X},"
            f"0x{samples[2]:04X},0x{samples[3]:04X}]"
        )


def selected_channels(channel: int | str) -> list[int]:
    return list(DAC_CHANNELS) if channel == "all" else [int(channel)]


def run_read(port: serial.Serial, args: argparse.Namespace) -> None:
    for channel in selected_channels(args.channel):
        print(f"=== DAC BRAM channel {channel} read start={args.start} count={args.count} ===")
        words = read_words(port, channel, args.start, args.count, args.echo_uart)
        print_u32_words(args.start, words)
        print_frame_view(args.start, words)


def run_write(port: serial.Serial, args: argparse.Namespace) -> None:
    if not args.values:
        raise SystemExit("write requires at least one u32 value")

    for channel in selected_channels(args.channel):
        print(f"=== DAC BRAM channel {channel} write start={args.start} count={len(args.values)} ===")
        print_u32_words(args.start, args.values)
        print_frame_view(args.start, args.values)
        if args.dry_run:
            total = args.start + len(args.values)
            if total & 1:
                total += 1
            print(f"dry run: would patch addresses {args.start}..{args.start + len(args.values) - 1}")
            print(f"dry run: old-firmware fallback would rewrite u32 prefix 0..{total - 1}")
            continue
        write_words(port, channel, args.start, args.values, args.echo_uart)

        if args.verify:
            print(f"=== DAC BRAM channel {channel} verify ===")
            readback = read_words(port, channel, args.start, len(args.values), args.echo_uart)
            if readback != args.values:
                raise RuntimeError(
                    f"verify failed on channel {channel}: expected "
                    f"{[f'0x{x:08X}' for x in args.values]}, got {[f'0x{x:08X}' for x in readback]}"
                )
            print("verify OK")


def run_zero(port: serial.Serial, args: argparse.Namespace) -> None:
    if args.count <= 0:
        raise SystemExit("zero --count must be positive")
    zero_words = [0] * args.count
    for channel in selected_channels(args.channel):
        print(f"=== DAC BRAM channel {channel} zero start={args.start} count={args.count} ===")
        if args.dry_run:
            total = args.start + args.count
            if total & 1:
                total += 1
            print(f"dry run: would zero addresses {args.start}..{args.start + args.count - 1}")
            print(f"dry run: old-firmware fallback would rewrite u32 prefix 0..{total - 1}")
            continue
        write_words(port, channel, args.start, zero_words, args.echo_uart)

        if args.verify:
            readback = read_words(port, channel, args.start, args.count, args.echo_uart)
            if readback != zero_words:
                raise RuntimeError(f"zero verify failed on channel {channel}")
            print("verify OK")


def run_play(port: serial.Serial, args: argparse.Namespace) -> None:
    rw3_run = ((args.frames << 8) & 0xFFFFFF00) | 0x60
    send_wait(port, f"WRTE 2 0x{args.rw2:08X}", echo=args.echo_uart)
    send_wait(port, "NSRC all bram", prefix="DAC source", echo=args.echo_uart)
    send_wait(port, f"WRTE 3 0x{rw3_run | 0x08:08X}", echo=args.echo_uart)
    send_wait(port, f"WRTE 3 0x{rw3_run:08X}", echo=args.echo_uart)
    send_wait(port, "RDRW 2", prefix="RW2", echo=args.echo_uart)
    send_wait(port, "RDRW 3", prefix="RW3", echo=args.echo_uart)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--quiet-uart", dest="echo_uart", action="store_false")
    parser.set_defaults(echo_uart=True)

    sub = parser.add_subparsers(dest="cmd", required=True)

    read = sub.add_parser("read", help="Read DAC program BRAM u32 words with DPRD.")
    read.add_argument("--channel", type=parse_channel, default=0)
    read.add_argument("--start", type=parse_u32, default=0)
    read.add_argument("--count", type=parse_u32, default=16)
    read.set_defaults(func=run_read)

    write = sub.add_parser("write", help="Write exact DAC program BRAM u32 words.")
    write.add_argument("--channel", type=parse_channel, default=0)
    write.add_argument("--start", type=parse_u32, default=0)
    write.add_argument("--dry-run", action="store_true")
    write.add_argument("--no-verify", dest="verify", action="store_false")
    write.add_argument("values", nargs="+", type=parse_u32)
    write.set_defaults(func=run_write, verify=True)

    zero = sub.add_parser("zero", help="Write zeros to DAC program BRAM.")
    zero.add_argument("--channel", type=parse_channel, default="all")
    zero.add_argument("--start", type=parse_u32, default=0)
    zero.add_argument("--count", type=parse_u32, default=16)
    zero.add_argument("--dry-run", action="store_true")
    zero.add_argument("--no-verify", dest="verify", action="store_false")
    zero.set_defaults(func=run_zero, verify=True)

    play = sub.add_parser("play", help="Set BRAM playback mode and frame loop count.")
    play.add_argument("--frames", type=parse_u32, required=True)
    play.add_argument("--rw2", type=parse_u32, default=0x010000F8)
    play.set_defaults(func=run_play)

    args = parser.parse_args()

    if getattr(args, "dry_run", False):
        args.func(None, args)
        return

    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required; run from a Python environment with pyserial installed") from exc

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        args.func(port, args)


if __name__ == "__main__":
    main()
