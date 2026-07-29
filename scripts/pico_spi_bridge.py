#!/usr/bin/env python3
"""Send the same Pico SPI transaction over Ethernet, COM10, or both paths."""

from __future__ import annotations

import argparse
import random
import re
import socket
import struct
import time


HEADER = struct.Struct("<4sBBHIHH")
VERSION = 1
UDP_PORT = 5007
MAX_BYTES = 128
LOOPBACK_VERIFY = 1


def parse_hex(text: str) -> bytes:
    compact = re.sub(r"[\s:_-]", "", text)
    if not compact or len(compact) % 2:
        raise argparse.ArgumentTypeError("TX hex must contain whole bytes")
    try:
        data = bytes.fromhex(compact)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid TX hex: {exc}") from exc
    if len(data) > MAX_BYTES:
        raise argparse.ArgumentTypeError(f"TX is limited to {MAX_BYTES} bytes")
    return data


def ethernet_transfer(args: argparse.Namespace, tx: bytes) -> bytes:
    sequence = random.getrandbits(32)
    flags = 0 if args.external else LOOPBACK_VERIFY
    request = HEADER.pack(
        b"PSPI", VERSION, flags, args.half_period_us,
        sequence, len(tx), 0
    ) + tx

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((args.local_ip, 0))
        sock.settimeout(args.timeout)
        for attempt in range(1, args.attempts + 1):
            sock.sendto(request, (args.board_ip, args.udp_port))
            try:
                response, peer = sock.recvfrom(HEADER.size + MAX_BYTES)
            except socket.timeout:
                if attempt == args.attempts:
                    raise RuntimeError("Ethernet Pico bridge timed out")
                continue
            if peer[0] != args.board_ip or len(response) < HEADER.size:
                continue
            magic, version, status, rx_length, reply_sequence, _, _ = (
                HEADER.unpack_from(response)
            )
            if magic != b"PSPR" or version != VERSION:
                continue
            if reply_sequence != sequence:
                continue
            if status != 0:
                raise RuntimeError(
                    f"Ethernet Pico bridge returned status {status}"
                )
            rx = response[HEADER.size:]
            if rx_length != len(rx):
                raise RuntimeError(
                    f"Ethernet response length mismatch: {rx_length} != {len(rx)}"
                )
            return rx
    raise RuntimeError("Ethernet Pico bridge returned no matching response")


def uart_transfer(args: argparse.Namespace, tx: bytes) -> bytes:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("pyserial is required for COM10 transport") from exc

    suffix = " external" if args.external else ""
    command = f"PSPI {tx.hex()} {args.half_period_us}{suffix}\n"
    with serial.Serial(
        args.port, args.baud, timeout=0.1, write_timeout=2.0
    ) as port:
        port.reset_input_buffer()
        port.write(command.encode("ascii"))
        port.flush()
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            line = port.readline().decode("ascii", errors="replace").strip()
            if line.startswith("ERR PSPI"):
                raise RuntimeError(line)
            if line.startswith("OK PSPI"):
                match = re.search(r"\bn=(\d+)\s+rx=([0-9A-Fa-f]+)$", line)
                if not match:
                    raise RuntimeError(f"malformed COM10 response: {line}")
                rx = bytes.fromhex(match.group(2))
                if int(match.group(1)) != len(rx):
                    raise RuntimeError(
                        f"COM10 response length mismatch: {line}"
                    )
                return rx
    raise RuntimeError("COM10 Pico bridge timed out")


def check_loopback(tx: bytes, rx: bytes, transport: str, external: bool) -> None:
    if len(rx) != len(tx):
        raise RuntimeError(
            f"{transport} returned {len(rx)} bytes for {len(tx)} transmitted"
        )
    if not external:
        expected = bytes(value ^ 0xA5 for value in tx)
        if rx != expected:
            raise RuntimeError(
                f"{transport} loopback mismatch: expected {expected.hex()}, "
                f"received {rx.hex()}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("auto", "ethernet", "uart", "both"),
        default="auto",
        help=(
            "auto tries Ethernet first and falls back to COM10; both requires "
            "the transaction to agree over both paths"
        ),
    )
    parser.add_argument(
        "--tx",
        type=parse_hex,
        default=parse_hex("00ffa55a3cc39669"),
        help="1..128 transmitted bytes as hexadecimal",
    )
    parser.add_argument("--half-period-us", type=int, default=5)
    parser.add_argument("--external", action="store_true")
    parser.add_argument("--board-ip", default="192.168.2.10")
    parser.add_argument("--local-ip", default="192.168.2.1")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    if not 1 <= args.half_period_us <= 100:
        parser.error("--half-period-us must be in 1..100")
    if args.attempts < 1:
        parser.error("--attempts must be positive")

    results: dict[str, bytes] = {}
    if args.transport == "auto":
        try:
            results["Ethernet"] = ethernet_transfer(args, args.tx)
        except (OSError, RuntimeError) as exc:
            print(f"Ethernet unavailable ({exc}); falling back to {args.port}.")
            results["COM10"] = uart_transfer(args, args.tx)
    elif args.transport in ("ethernet", "both"):
        results["Ethernet"] = ethernet_transfer(args, args.tx)
    if args.transport in ("uart", "both"):
        results["COM10"] = uart_transfer(args, args.tx)

    for transport, rx in results.items():
        check_loopback(args.tx, rx, transport, args.external)
        print(
            f"PASS {transport}: tx={args.tx.hex()} rx={rx.hex()} "
            f"bytes={len(rx)}"
        )
    if len(results) == 2 and results["Ethernet"] != results["COM10"]:
        raise RuntimeError("Ethernet and COM10 returned different SPI data")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
