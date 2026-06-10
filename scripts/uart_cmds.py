#!/usr/bin/env python3
"""Send simple line-oriented UART commands to the DAQ firmware."""

from __future__ import annotations

import argparse
import time

import serial


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("commands", nargs="+")
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=2.0, write_timeout=2.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        for command in args.commands:
            port.write((command + "\n").encode("ascii"))
            port.flush()
            time.sleep(args.delay)
            print(f"--- {command} ---")
            print(port.read_all().decode("ascii", errors="replace").strip())


if __name__ == "__main__":
    main()
