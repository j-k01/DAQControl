#!/usr/bin/env python3
"""Dump selected UART RO3 selector values from the DAQ firmware."""

from __future__ import annotations

import argparse
import time

import serial


def send_and_read(port: serial.Serial, command: str, delay: float) -> str:
    port.write((command + "\n").encode("ascii"))
    port.flush()
    time.sleep(delay)
    return port.read_all().decode("ascii", errors="replace").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument(
        "selectors",
        nargs="*",
        default=["3", "25", "26", "27", "28", "29", "30", "4", "5", "6", "7"],
        help="RW1 selector values to query via RDRO 3.",
    )
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=2.0, write_timeout=2.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        print(send_and_read(port, "STAT", args.delay))
        for selector in args.selectors:
            print(f"--- selector {selector} ---")
            print(send_and_read(port, f"WRTE 1 {selector}", args.delay))
            print(send_and_read(port, "RDRO 3", args.delay))


if __name__ == "__main__":
    main()
