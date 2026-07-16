#!/usr/bin/env python3
"""Test the DAQ board's actual UDP command path (PING -> PONG).

Unlike ICMP ping, this proves that the A53 lwIP application is running, UDP
port 5006 is bound, the host route is correct, and the reply reaches the host.
The script is read-only: it never programs or resets either processor.
"""

from __future__ import annotations

import argparse
import errno
import re
import socket
import subprocess
import sys
import time

BOARD_MAC = "00:0a:35:00:01:10"


def arp_entries():
    """Return matching board-MAC ARP lines (best effort, Windows/Linux)."""
    for cmd in (["arp", "-a"], ["ip", "neigh"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        hits = []
        for line in out.splitlines():
            norm = line.lower().replace("-", ":")
            if BOARD_MAC in norm:
                hits.append(line.strip())
        if hits:
            return hits
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--local-ip", help="host IP to bind (recommended for direct link)")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=1.5,
                    help="seconds per attempt")
    args = ap.parse_args()

    bind_ip = args.local_ip or "0.0.0.0"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((bind_ip, 0))
    except OSError as exc:
        print(f"FAIL BIND: cannot bind local IP {bind_ip}: {exc}")
        print("The requested IP is not assigned to an active host adapter.")
        return 2

    sock.settimeout(max(0.1, args.timeout))
    target = (args.board_ip, args.cmd_port)
    print(f"UDP test: local {sock.getsockname()[0]} -> "
          f"{args.board_ip}:{args.cmd_port}")
    try:
        for attempt in range(1, max(1, args.attempts) + 1):
            t0 = time.perf_counter()
            try:
                sock.sendto(b"PING", target)
                # After the first send, getsockname reports the OS-selected
                # source address even when the socket was bound to 0.0.0.0.
                source = sock.getsockname()[0]
                data, addr = sock.recvfrom(256)
            except socket.timeout:
                print(f"  attempt {attempt}: timeout")
                continue
            except OSError as exc:
                # Windows turns an ICMP UDP-port-unreachable response into
                # WSAECONNRESET (10054).  That proves the IP path reached a
                # host, but nothing is listening on the A53 command port.
                if getattr(exc, "winerror", None) == 10054 \
                        or exc.errno == errno.ECONNREFUSED:
                    print(f"  attempt {attempt}: UDP port unreachable "
                          "(IP path works; A53 app is not listening)")
                    continue
                print(f"FAIL SEND/RECV: {exc}")
                return 2
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            if data.strip() == b"PONG" and addr[0] == args.board_ip:
                print(f"PASS UDP: PONG from {addr[0]}:{addr[1]} via "
                      f"{source} ({rtt_ms:.1f} ms)")
                return 0
            printable = re.sub(r"[^ -~]", ".", data.decode("ascii", "replace"))
            print(f"  attempt {attempt}: unexpected {addr}: {printable!r}")
    finally:
        sock.close()

    print("FAIL UDP: no PONG; the A53 app is not reachable on its command port.")
    hits = arp_entries()
    if hits:
        print("Board MAC is present in ARP (L2 works; investigate app/UDP/firewall):")
        for line in hits:
            print(f"  {line}")
    else:
        print("Board MAC is absent from ARP (app down, link down, or wrong host subnet).")
    return 3


if __name__ == "__main__":
    sys.exit(main())
