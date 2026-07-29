#!/usr/bin/env python3
"""Probe or safely restart the unified Linux DAQ Ethernet service over PS UART.

This never resets or reprograms the ZCU102. It requires the unified Linux
runtime to be booted and uses its COM9 shell to inspect or restart only
``daq-eth-service``.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time

try:
    import serial
except ImportError:
    serial = None


def wait_for_exact_line(
    port: object, expected: str, timeout: float
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    received = bytearray()
    while time.monotonic() < deadline:
        data = port.read(4096)
        if data:
            received.extend(data)
            text = received.decode("ascii", errors="replace")
            if any(line.strip() == expected for line in text.splitlines()):
                return True, text
    return False, received.decode("ascii", errors="replace")


def run_shell_check(
    port: object, command: str, expected: str, timeout: float
) -> tuple[bool, str]:
    port.reset_input_buffer()
    port.write((command + "\n").encode("ascii"))
    port.flush()
    return wait_for_exact_line(port, expected, timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM9", help="ZCU102 PS UART")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="inspect the service without changing it",
    )
    args = parser.parse_args()

    if serial is None:
        print("ERROR: pyserial is required (`python -m pip install pyserial`).")
        return 2

    nonce = secrets.token_hex(6)
    shell_marker = f"__DAQ_LINUX_SHELL_{nonce}__"
    running_marker = f"__DAQ_SERVICE_RUNNING_{nonce}__"
    stopped_marker = f"__DAQ_SERVICE_STOPPED_{nonce}__"

    try:
        with serial.Serial(
            args.port,
            args.baud,
            timeout=0.1,
            write_timeout=2.0,
        ) as port:
            # The UART echoes input. Requiring a line that equals the marker,
            # rather than a substring, distinguishes shell output from echo.
            ok, transcript = run_shell_check(
                port,
                f"echo {shell_marker}",
                shell_marker,
                args.timeout,
            )
            if not ok:
                print(
                    f"FAIL: no unified Linux shell response on {args.port}.",
                    file=sys.stderr,
                )
                if transcript.strip():
                    print(transcript, file=sys.stderr)
                return 3

            if args.probe:
                command = (
                    "if pidof daq-eth-service >/dev/null; then "
                    f"echo {running_marker}; else echo {stopped_marker}; fi"
                )
            else:
                command = (
                    "killall daq-eth-service 2>/dev/null || true; "
                    "sleep 1; "
                    "/usr/sbin/daq-eth-service >/dev/ttyPS0 2>&1 & "
                    "sleep 1; "
                    "if pidof daq-eth-service >/dev/null; then "
                    f"echo {running_marker}; else echo {stopped_marker}; fi"
                )

            ok, transcript = run_shell_check(
                port, command, running_marker, args.timeout
            )
            if ok:
                action = "is running" if args.probe else "was restarted"
                print(f"PASS: Linux DAQ Ethernet service {action} on {args.port}.")
                return 0
            if any(
                line.strip() == stopped_marker for line in transcript.splitlines()
            ):
                print("FAIL: Linux is responsive, but daq-eth-service is stopped.")
            else:
                print("FAIL: Linux did not report the DAQ service state.")
            if transcript.strip():
                print(transcript)
            return 4
    except (OSError, serial.SerialException) as exc:
        print(f"ERROR: cannot use {args.port}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
