#!/usr/bin/env python3
"""Read-only test of the FPGA-hosted Pico CDC bridge and PICO-002 handshake."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fpga_pico_serial import Serial


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-ip", default="192.168.2.10")
    parser.add_argument("--local-ip", default="192.168.2.1")
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    try:
        with Serial(
            transport="ethernet",
            board_ip=args.board_ip,
            local_ip=args.local_ip,
            timeout=args.timeout,
            write_timeout=args.timeout,
        ) as pico:
            pico.reset_input_buffer()
            pico.write(b"HANDSHAKE\n")
            uid = pico.readline().decode("ascii", errors="replace").strip()
            pico.write(b"ENDHS\n")
            end = pico.readline().decode("ascii", errors="replace").strip()
    except OSError as exc:
        print(
            f"FAIL: no usable Pico bridge at {args.board_ip}:5007: {exc}\n"
            f"NOTE: ADC Ethernet on {args.board_ip}:5006 is independent. "
            "Load the unified USB-host runtime with:\n"
            "  uv run python pico_usb\\load_and_test.py --local-jtag --port COM9",
            file=sys.stderr,
        )
        return 1

    if uid != "UID:PICO-002":
        print(
            f"FAIL: bridge responded, but Pico returned {uid!r}; "
            "expected 'UID:PICO-002'.",
            file=sys.stderr,
        )
        return 2
    if end != "HSOK":
        print(
            f"FAIL: PICO-002 answered, but ENDHS returned {end!r}; "
            "expected 'HSOK'.",
            file=sys.stderr,
        )
        return 3

    print(
        f"PASS: {args.board_ip}:5007 forwarded the complete "
        "PICO-002 HANDSHAKE/ENDHS exchange."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())