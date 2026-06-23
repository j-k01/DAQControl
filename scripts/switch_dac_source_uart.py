#!/usr/bin/env python3
"""Switch DAC source muxes between live DDS and programmed BRAM over UART.

This does not upload BRAM data. It is for fast scope A/B testing after a BRAM
program is already loaded.

Examples:

  # Switch all DAC sources to live DDS using the hardware default DDS step.
  python scripts/switch_dac_source_uart.py dds

  # Switch all DAC sources to BRAM and restart a 4095-frame loop.
  python scripts/switch_dac_source_uart.py bram --frames 4095

  # Alternate DDS/BRAM every 3 seconds for scope comparison.
  python scripts/switch_dac_source_uart.py toggle --frames 4095 --period 3
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import dac_bram_u32_uart as bram  # noqa: E402


def parse_channel(text: str) -> int | str:
    if text.lower() == "all":
        return "all"
    value = int(text, 0)
    if value < 0 or value > 3:
        raise argparse.ArgumentTypeError("channel must be 0..3 or all")
    return value


def source_target(channel: int | str) -> str:
    return "all" if channel == "all" else str(channel)


def dds_inc_value(args: argparse.Namespace) -> int | None:
    if args.preserve_step:
        return None
    return args.step & 0xFFFFFF


def rw3_bram_value(frames: int, restart: bool) -> int:
    value = ((frames & 0xFFFFFF) << 8) | 0x60
    if restart:
        value |= 0x08
    return value


def set_dds(port, args: argparse.Namespace) -> None:
    target = source_target(args.channel)
    bram.send_wait(port, f"NSRC {target} dds", prefix="DAC source", echo=args.echo_uart)
    value = dds_inc_value(args)
    if value is not None:
        bram.send_wait(port, f"DDSI 0x{value:06X}", prefix="DDS inc", echo=args.echo_uart)
    show_state(port, args)


def set_bram(port, args: argparse.Namespace) -> None:
    target = source_target(args.channel)
    bram.send_wait(port, f"NSRC {target} bram", prefix="DAC source", echo=args.echo_uart)
    if args.restart:
        bram.send_wait(port, f"WRTE 3 0x{rw3_bram_value(args.frames, True):08X}", echo=args.echo_uart)
    bram.send_wait(port, f"WRTE 3 0x{rw3_bram_value(args.frames, False):08X}", echo=args.echo_uart)
    show_state(port, args)


def show_state(port, args: argparse.Namespace) -> None:
    if not args.status:
        return
    bram.send_wait(port, "RDRW 2", prefix="RW2", echo=args.echo_uart)
    bram.send_wait(port, "RDRW 3", prefix="RW3", echo=args.echo_uart)
    bram.send_wait(port, "WRTE 1 7", echo=args.echo_uart)
    bram.send_wait(port, "RDRO 3", prefix="RO3", echo=args.echo_uart)


def run_toggle(port, args: argparse.Namespace) -> None:
    for cycle in range(args.cycles):
        print(f"=== toggle cycle {cycle + 1}/{args.cycles}: DDS ===")
        set_dds(port, args)
        time.sleep(args.period)
        print(f"=== toggle cycle {cycle + 1}/{args.cycles}: BRAM ===")
        set_bram(port, args)
        time.sleep(args.period)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["dds", "bram", "toggle"])
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--channel", type=parse_channel, default="all")
    parser.add_argument(
        "--frames",
        type=bram.parse_u32,
        default=4095,
        help="BRAM loop frame count. Use 0 to loop the full BRAM.",
    )
    parser.add_argument(
        "--step",
        type=bram.parse_u32,
        default=0,
        help="DDS phase increment. Default 0 selects the HDL default 0x19999A.",
    )
    parser.add_argument(
        "--preserve-step",
        action="store_true",
        help="Do not write reg19[23:0] when switching to DDS.",
    )
    parser.add_argument("--no-restart", dest="restart", action="store_false")
    parser.add_argument("--period", type=float, default=2.0)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--no-status", dest="status", action="store_false")
    parser.add_argument("--quiet-uart", dest="echo_uart", action="store_false")
    parser.set_defaults(restart=True, status=True, echo_uart=True)
    args = parser.parse_args()

    if args.cycles <= 0:
        raise SystemExit("--cycles must be positive")
    if args.period < 0:
        raise SystemExit("--period must be non-negative")

    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required; run from a Python environment with pyserial installed") from exc

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        if args.mode == "dds":
            set_dds(port, args)
        elif args.mode == "bram":
            set_bram(port, args)
        else:
            run_toggle(port, args)


if __name__ == "__main__":
    main()
