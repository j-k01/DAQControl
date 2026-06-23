#!/usr/bin/env python3
"""Program one DAC BRAM channel with a nominal voltage ramp.

Default behavior writes only one selected DAC/source BRAM and only the small
diagnostic window. It programs a 0.0 V to 0.2 V ramp definition over 32
32-bit CPU-visible BRAM addresses, but `--active-addresses` can cut the ramp
after any step. The rest of those 32 addresses is written as zeros, so previous
attempts in the diagnostic window are cleared without a long full-BRAM zeroing
pass. Other DAC BRAMs are left untouched unless `--clear-all` is given. Each
32-bit address contains two identical 16-bit DAC samples so the address-level
ramp is easy to inspect:

    addr N = {code_N, code_N}

The voltage conversion follows the current bring-up convention:

    +0.5 V nominal -> signed DAC code 0x7FFF

Actual connector voltage still depends on DAC full-scale current, analog output
network, load, and coupling.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import dac_bram_u32_uart as bram  # noqa: E402


MAX_U32_WORDS = 8192
DEFAULT_RW2 = 0x01000018


def parse_int(text: str) -> int:
    return int(text, 0)


def clamp_s16(value: int) -> int:
    return max(-32768, min(32767, value))


def voltage_to_code(volts: float, full_scale_volts: float) -> int:
    if full_scale_volts <= 0.0:
        raise ValueError("--full-scale-volts must be positive")
    return clamp_s16(int(round((volts / full_scale_volts) * 32767.0)))


def pack_duplicate_pair(code: int) -> int:
    value = code & 0xFFFF
    return value | (value << 16)


def make_ramp_window_words(
    write_addresses: int,
    ramp_addresses: int,
    active_addresses: int,
    start_volts: float,
    end_volts: float,
    full_scale_volts: float,
) -> list[int]:
    if write_addresses <= 0:
        raise ValueError("--write-addresses must be positive")
    if write_addresses & 1:
        raise ValueError("--write-addresses must be even so fabric 64-bit frames are complete")
    if ramp_addresses <= 0:
        raise ValueError("--ramp-addresses/--addresses must be positive")
    if active_addresses < 0 or active_addresses > ramp_addresses:
        raise ValueError("--active-addresses must be between 0 and the ramp length")
    if ramp_addresses > write_addresses:
        raise ValueError("--ramp-addresses/--addresses cannot exceed --write-addresses")

    words: list[int] = []
    denom = max(1, ramp_addresses - 1)
    for index in range(write_addresses):
        if index >= active_addresses or index >= ramp_addresses:
            words.append(0)
            continue
        volts = start_volts + (end_volts - start_volts) * index / denom
        code = voltage_to_code(volts, full_scale_volts)
        words.append(pack_duplicate_pair(code))
    return words


def print_preview(args: argparse.Namespace, words: list[int]) -> None:
    print(f"DAC channel: {args.channel}")
    print(
        f"Ramp definition: {args.start_volts:g} V -> {args.end_volts:g} V "
        f"over {args.ramp_addresses} u32 BRAM addresses"
    )
    print(f"Active ramp addresses: {args.active_addresses}")
    print(f"Selected-channel write window: {args.write_addresses} u32 BRAM addresses")
    print(f"Scale: +{args.full_scale_volts:g} V nominal -> 0x7FFF")
    print(f"Playback frames: {args.loop_frames}")
    print(
        "Windowing: only the selected DAC BRAM write window is rewritten; "
        f"{args.write_addresses - args.active_addresses} trailing u32 addresses in that window are zero."
    )
    if args.clear_all:
        print("Clear mode: --clear-all selected; all four DAC BRAMs will be zeroed before writing.")
    else:
        print("Clear mode: other DAC BRAMs are left untouched.")
    print("First ramp words:")
    for index, word in enumerate(words[: min(len(words), args.preview_words)]):
        sample = word & 0xFFFF
        signed = sample - 0x10000 if sample & 0x8000 else sample
        volts = signed * args.full_scale_volts / 32767.0
        print(f"  [{index:4d}] 0x{word:08X}  code=0x{sample:04X}  approx={volts:.6f} V")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--channel", type=int, choices=range(4), default=1)
    parser.add_argument(
        "--addresses",
        dest="ramp_addresses",
        type=parse_int,
        default=32,
        help="Number of u32 BRAM addresses in the full ramp definition.",
    )
    parser.add_argument(
        "--active-addresses",
        type=parse_int,
        default=None,
        help="Only write this many ramp positions; remaining write window addresses become zero.",
    )
    parser.add_argument(
        "--write-addresses",
        type=parse_int,
        default=None,
        help="Selected-channel u32 address window to rewrite; default is the ramp length.",
    )
    parser.add_argument("--start-address", type=parse_int, default=0)
    parser.add_argument("--start-volts", type=float, default=0.0)
    parser.add_argument("--end-volts", type=float, default=0.2)
    parser.add_argument("--full-scale-volts", type=float, default=0.5)
    parser.add_argument("--clear-all", action="store_true", help="Zero all four DAC BRAMs before writing.")
    parser.add_argument("--rw2", type=parse_int, default=DEFAULT_RW2)
    parser.add_argument(
        "--loop-frames",
        type=parse_int,
        default=None,
        help="64-bit frames to loop; default loops the selected write window.",
    )
    parser.add_argument("--preview-words", type=int, default=32)
    parser.add_argument("--quiet-uart", dest="echo_uart", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(echo_uart=True)
    args = parser.parse_args()

    if args.active_addresses is None:
        args.active_addresses = args.ramp_addresses
    if args.write_addresses is None:
        args.write_addresses = args.ramp_addresses
    if args.write_addresses > MAX_U32_WORDS:
        raise SystemExit(f"--write-addresses must be <= {MAX_U32_WORDS}")
    if args.ramp_addresses > args.write_addresses:
        raise SystemExit("--addresses/--ramp-addresses cannot exceed --write-addresses")
    if args.start_address < 0 or args.start_address + args.write_addresses > MAX_U32_WORDS:
        raise SystemExit("--start-address + --write-addresses exceeds DAC BRAM depth")
    if args.loop_frames is None:
        args.loop_frames = args.write_addresses // 2

    words = make_ramp_window_words(
        args.write_addresses,
        args.ramp_addresses,
        args.active_addresses,
        args.start_volts,
        args.end_volts,
        args.full_scale_volts,
    )
    print_preview(args, words)

    if args.dry_run:
        return

    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required; run from an environment with pyserial installed") from exc

    zero_words = [0] * MAX_U32_WORDS

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        if args.clear_all:
            for channel in range(4):
                bram.write_words(port, channel, 0, zero_words, args.echo_uart)

        bram.write_words(port, args.channel, args.start_address, words, args.echo_uart)
        bram.send_wait(port, f"WRTE 2 0x{args.rw2:08X}", echo=args.echo_uart)
        bram.send_wait(port, "NSRC all bram", prefix="DAC source", echo=args.echo_uart)

        rw3_run = ((args.loop_frames << 8) & 0xFFFFFF00) | 0x60
        bram.send_wait(port, f"WRTE 3 0x{rw3_run | 0x08:08X}", echo=args.echo_uart)
        bram.send_wait(port, f"WRTE 3 0x{rw3_run:08X}", echo=args.echo_uart)

        readback_count = min(args.write_addresses, args.preview_words)
        readback = bram.read_words(port, args.channel, args.start_address, readback_count, args.echo_uart)
        if readback != words[:readback_count]:
            raise RuntimeError("readback mismatch in ramp preview window")
        print("Ramp write/readback OK")


if __name__ == "__main__":
    main()
