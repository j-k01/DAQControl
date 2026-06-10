#!/usr/bin/env python3
"""Listen on the ZCU102 PS UART0 (A53 console) and print whatever arrives.

On the current local PC the CP2108 quad UART maps Interface 0 (PS UART0)
to COM9; the MicroBlaze UART is Interface 2 (COM10).
"""

from __future__ import annotations

import argparse
import sys
import time

import serial


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM9")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()

    deadline = time.time() + args.seconds
    with serial.Serial(args.port, args.baud, timeout=0.5) as port:
        port.reset_input_buffer()
        print(f"listening on {args.port} @ {args.baud} for {args.seconds:.0f}s", flush=True)
        while time.time() < deadline:
            data = port.read(4096)
            if data:
                sys.stdout.write(data.decode("ascii", errors="replace"))
                sys.stdout.flush()
    print("\n[listener done]", flush=True)


if __name__ == "__main__":
    main()
