#!/usr/bin/env python3
"""Continuous decimated ADC stream receiver.

Flow: arm the MicroBlaze first (UART: `STRM <decim>`), then run this script.
It sends `STRM` to the A53 app, which drains the DDR rings to us as "DAQS"
packets; on exit it sends `STOP`.

Each packet: 32-byte little-endian header
  u32 magic 0x53514144 "DAQS", u16 version, u16 header_bytes,
  u32 seq (per chip), u32 chip, u32 ring_offset, u32 byte_count,
  u32 board_drops (bytes the board skipped), u32 decim
followed by byte_count payload bytes (raw 16-byte ADC frames, sample period
= decim ns at the 1 GS/s ADC rate).
"""

from __future__ import annotations

import argparse
import socket
import struct
import time
from pathlib import Path

HDR = struct.Struct("<IHHIIIIII")
MAGIC = 0x53514144


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-ip", default="192.168.2.10")
    parser.add_argument("--cmd-port", type=int, default=5006)
    parser.add_argument("--local-ip", default="192.168.2.1")
    parser.add_argument("--local-port", type=int, default=5005)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--outdir", default="captures/eth")
    parser.add_argument("--prefix", default="stream")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    sock.bind((args.local_ip, args.local_port))
    sock.settimeout(2.0)

    files = {c: (outdir / f"{args.prefix}_chip{c}.bin").open("wb") for c in (0, 1)}
    stats = {c: {"pkts": 0, "bytes": 0, "next_seq": None, "gaps": 0,
                 "gap_pkts": 0, "board_drops": 0} for c in (0, 1)}
    decim = None

    sock.sendto(b"STRM", (args.board_ip, args.cmd_port))
    t0 = time.time()
    t_last = t0
    bytes_last = 0
    print(f"streaming for {args.seconds:.1f}s ...")

    try:
        while time.time() - t0 < args.seconds:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                print("WARNING: 2s with no packets")
                continue
            if len(data) < HDR.size:
                if data.startswith(b"ERR"):
                    print(data.decode(errors="replace").strip())
                    return
                continue
            magic, _ver, hdr_len, seq, chip, _off, count, drops, dec = HDR.unpack_from(data)
            if magic != MAGIC or chip not in stats:
                continue
            decim = dec
            st = stats[chip]
            if st["next_seq"] is not None and seq != st["next_seq"]:
                st["gaps"] += 1
                st["gap_pkts"] += (seq - st["next_seq"]) & 0xFFFFFFFF
            st["next_seq"] = (seq + 1) & 0xFFFFFFFF
            st["board_drops"] = drops
            payload = data[hdr_len:hdr_len + count]
            files[chip].write(payload)
            st["pkts"] += 1
            st["bytes"] += len(payload)

            now = time.time()
            if now - t_last >= 1.0:
                total = sum(s["bytes"] for s in stats.values())
                rate = (total - bytes_last) / (now - t_last) / 1e6
                print(f"  t={now - t0:5.1f}s  {rate:6.2f} MB/s  "
                      f"chip0={stats[0]['bytes']/1e6:.1f}MB "
                      f"chip1={stats[1]['bytes']/1e6:.1f}MB")
                t_last = now
                bytes_last = total
    finally:
        sock.sendto(b"STOP", (args.board_ip, args.cmd_port))
        for f in files.values():
            f.close()
        sock.close()

    elapsed = time.time() - t0
    print(f"\ndone: {elapsed:.2f}s, decim={decim}")
    for c in (0, 1):
        st = stats[c]
        print(f"chip{c}: {st['pkts']} pkts, {st['bytes']/1e6:.2f} MB "
              f"({st['bytes']/elapsed/1e6:.2f} MB/s), "
              f"seq gaps={st['gaps']} ({st['gap_pkts']} pkts), "
              f"board ring drops={st['board_drops']} bytes")
    if decim:
        print(f"sample period = {decim} ns/sample per channel "
              f"({1000.0/decim:.3f} MS/s per channel)")


if __name__ == "__main__":
    main()
