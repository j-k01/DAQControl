#!/usr/bin/env python3
"""Verify the unified Linux Ethernet service without requiring live ADC clocks.

The test writes two known 16-byte records into the reserved DAQ DDR region
through the Linux console, publishes the normal MicroBlaze burst mailbox,
issues the real ``BRDO`` UART command, and verifies both DAQS UDP packets.
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

import serial


DAQS = 0x53514144
HEADER = struct.Struct("<IHHIIIIII")
MAILBOX = 0x1003FF00
BASES = (0x18000000, 0x19000000)
PATTERNS = (
    (0x03020100, 0x07060504, 0x0B0A0908, 0x0F0E0D0C),
    (0x83828180, 0x87868584, 0x8B8A8988, 0x8F8E8D8C),
)


def shell_batch(port: serial.Serial, commands: list[str], timeout: float = 8.0) -> None:
    marker = "__DAQ_LINUX_WRITE_DONE__"
    port.reset_input_buffer()
    command = "; ".join(commands + [f"echo {marker}"])
    port.write((command + "\n").encode("ascii"))
    port.flush()
    deadline = time.monotonic() + timeout
    received = bytearray()
    while time.monotonic() < deadline:
        received.extend(port.read(4096))
        if marker.encode("ascii") in received:
            transcript = received.decode("ascii", errors="replace")
            if "not found" in transcript or "daq-mem:" in transcript:
                raise RuntimeError(
                    "Linux rejected a DDR/mailbox write:\n" + transcript
                )
            return
    raise RuntimeError(
        "Linux UART did not complete DDR/mailbox writes:\n"
        + received.decode("ascii", errors="replace")
    )


def mb_command(
    port: serial.Serial, command: str, prefix: str, timeout: float = 5.0
) -> str:
    port.reset_input_buffer()
    port.write((command + "\n").encode("ascii"))
    port.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = port.readline().decode("ascii", errors="replace").strip()
        if line.startswith(prefix):
            return line
    raise RuntimeError(f"MicroBlaze did not return {prefix!r} after {command!r}")


def wait_ready(sock: socket.socket, board: tuple[str, int]) -> None:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        sock.sendto(b"BRST", board)
        try:
            packet, _ = sock.recvfrom(256)
        except socket.timeout:
            continue
        if packet == b"BRST_READY\n":
            return
    raise RuntimeError("Linux DAQ service did not return BRST_READY")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ps-port", default="COM9")
    parser.add_argument("--mb-port", default="COM10")
    parser.add_argument("--board-ip", default="192.168.2.10")
    parser.add_argument("--local-ip", default="192.168.2.1")
    parser.add_argument("--local-port", type=int, default=5005)
    args = parser.parse_args()

    writes: list[str] = []
    for base, words in zip(BASES, PATTERNS):
        for index, word in enumerate(words):
            writes.append(f"daq-mem 0x{base + 4 * index:08X} 0x{word:08X}")
    writes += [
        f"daq-mem 0x{MAILBOX + 0x04:08X} 0x00000010",
        f"daq-mem 0x{MAILBOX + 0x08:08X} 0x{BASES[0]:08X}",
        f"daq-mem 0x{MAILBOX + 0x0C:08X} 0x{BASES[1]:08X}",
        f"daq-mem 0x{MAILBOX + 0x10:08X} 0x00000000",
        f"daq-mem 0x{MAILBOX + 0x14:08X} 0x00000000",
        f"daq-mem 0x{MAILBOX + 0x00:08X} 0x42435054",
    ]

    with serial.Serial(args.ps_port, 115200, timeout=0.1, write_timeout=2) as ps:
        shell_batch(ps, writes)

    board = (args.board_ip, 5006)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.bind((args.local_ip, args.local_port))
        sock.settimeout(0.25)
        wait_ready(sock, board)

        with serial.Serial(
            args.mb_port, 115200, timeout=0.2, write_timeout=2
        ) as microblaze:
            reply = mb_command(microblaze, "BRDO", "OK BRDO")

        received: dict[int, bytes] = {}
        deadline = time.monotonic() + 5.0
        while len(received) < 2 and time.monotonic() < deadline:
            try:
                packet, peer = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if peer[0] != args.board_ip or len(packet) < HEADER.size:
                continue
            fields = HEADER.unpack_from(packet)
            magic, version, header_bytes, _, chip, offset, count, request, decim = fields
            if (
                magic != DAQS
                or version != 1
                or header_bytes != HEADER.size
                or chip not in (0, 1)
                or offset != 0
                or count != 16
                or decim != 1
            ):
                continue
            received[chip] = packet[header_bytes : header_bytes + count]
            if request != 1:
                raise RuntimeError(f"unexpected burst request id {request}, expected 1")

    for chip, words in enumerate(PATTERNS):
        expected = struct.pack("<4I", *words)
        actual = received.get(chip)
        if actual != expected:
            raise RuntimeError(
                f"chip{chip} payload mismatch: expected {expected.hex()}, "
                f"received {actual.hex() if actual is not None else 'nothing'}"
            )

    print(
        "PASS: MicroBlaze accepted the BRDO request "
        f"({' '.join(reply.split()[:3])})"
    )
    print("PASS: Linux mapped reserved DDR and returned exact chip0/chip1 DAQS payloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
