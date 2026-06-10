#!/usr/bin/env python3
"""Request and receive DAQ PS Ethernet UDP readout packets."""

from __future__ import annotations

import argparse
import socket
import struct
import time
from pathlib import Path


MAGIC = 0x44415144
HEADER = struct.Struct("<IHHIIIIII")


def parse_packet(data: bytes):
    if len(data) < HEADER.size:
        return None
    magic, version, header_bytes, seq, chip, word_offset, word_count, byte_count, flags = HEADER.unpack_from(data)
    if magic != MAGIC or version != 1 or header_bytes < HEADER.size:
        return None
    payload = data[header_bytes:header_bytes + byte_count]
    if len(payload) != byte_count or byte_count != word_count * 4:
        return None
    return seq, chip, word_offset, word_count, flags, payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-ip", default="192.168.1.10")
    parser.add_argument("--cmd-port", type=int, default=5006)
    parser.add_argument("--local-port", type=int, default=5005)
    parser.add_argument("--chip", default="all", choices=["all", "0", "1"])
    parser.add_argument("--frames", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--outdir", default="captures/eth")
    parser.add_argument("--prefix", default="ps_eth_capture")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    buffers: dict[int, bytearray] = {}
    words_seen: dict[int, set[int]] = {}
    done_chips: set[int] = set()

    command = "SEND"
    if args.chip != "all":
        command += f" {args.chip}"
    else:
        command += " 2"
    command += f" {args.frames}"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", args.local_port))
    sock.settimeout(0.2)
    sock.sendto(command.encode("ascii"), (args.board_ip, args.cmd_port))

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(2048)
        except TimeoutError:
            continue
        except socket.timeout:
            continue

        if data.startswith(b"DAQ_BEGIN") or data.startswith(b"DAQ_END"):
            print(data.decode("ascii", errors="replace").strip(), "from", addr)
            if data.startswith(b"DAQ_END"):
                break
            continue

        pkt = parse_packet(data)
        if pkt is None:
            print("non-data packet:", data[:80], "from", addr)
            continue

        seq, chip, word_offset, word_count, flags, payload = pkt
        end_word = word_offset + word_count
        byte_offset = word_offset * 4
        needed = end_word * 4
        buf = buffers.setdefault(chip, bytearray())
        if len(buf) < needed:
            buf.extend(b"\x00" * (needed - len(buf)))
        buf[byte_offset:byte_offset + len(payload)] = payload
        seen = words_seen.setdefault(chip, set())
        seen.update(range(word_offset, end_word))
        if flags & 1:
            done_chips.add(chip)
        deadline = time.time() + args.timeout

    sock.close()

    for chip, data in sorted(buffers.items()):
        path = outdir / f"{args.prefix}_chip{chip}.bin"
        path.write_bytes(data)
        print(f"chip{chip}: {len(data)} bytes, {len(words_seen.get(chip, set()))} words -> {path}")


if __name__ == "__main__":
    main()
