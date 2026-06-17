#!/usr/bin/env python3
"""Full-rate burst capture: trigger an un-decimated all-channel capture and
read it out over Ethernet.

Flow (all MB-controlled, capture and readout decoupled):
  1. register this host with the A53 (UDP "BRST")
  2. BCAP <MB>  over UART -> both DMAs capture <MB> MB/chip in parallel (~128 ms
     for 512 MB), sample-aligned (chip0=ch0/ch1, chip1=ch2/ch3, 1-to-1)
  3. BRDO       over UART -> A53 streams both 512 MB regions out over UDP
  4. reassemble by (chip, byte-offset), decode to 4 channels, report loss

Each chip region is 2 channels x int16; frame = 8 int16 (4 even-ch + 4 odd-ch),
so the decode matches the streaming path. The Ethernet drain is the slow part
(~9 s for 1 GB over 1 GbE); the capture itself is ~128 ms.

  python scripts/burst_capture.py --mb 512
  python scripts/burst_capture.py --mb 64 --plot
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import threading
import time

import numpy as np
import serial

HDR = struct.Struct("<IHHIIIIII")   # magic ver hdr seq chip off bytes drops decim
MAGIC = 0x53514144
VOLTS_PER_COUNT = 1.9 / 65536.0


class Reassembler:
    def __init__(self, board_ip, cmd_port, local_ip, local_port, bytes_per_chip,
                 rcvbuf=256 << 20):
        self.bytes_per_chip = bytes_per_chip
        self.buf = [bytearray(bytes_per_chip), bytearray(bytes_per_chip)]
        self.got = [0, 0]               # packets placed per chip (may double-count dups)
        # coverage bitmap per chip in PAYLOAD-sized slots -> true unique coverage
        slot = 1408
        self.nslot = (bytes_per_chip + slot - 1) // slot
        self.slot = slot
        self.cov = [np.zeros(self.nslot, dtype=bool), np.zeros(self.nslot, dtype=bool)]
        self.last_t = time.time()
        self.lock = threading.Lock()
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.sock.bind((local_ip, local_port))
        self.sock.settimeout(1.0)
        self.board = (board_ip, cmd_port)
        threading.Thread(target=self._rx, daemon=True).start()

    def register(self):
        self.sock.sendto(b"BRST", self.board)

    def _rx(self):
        bpc = self.bytes_per_chip
        while self.running:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < HDR.size:
                continue
            magic, _v, hdr, _seq, chip, off, nbytes, _d, _dec = HDR.unpack_from(data)
            if magic != MAGIC or chip > 1:
                continue
            payload = data[hdr:hdr + nbytes]
            if off + len(payload) > bpc:
                continue
            self.buf[chip][off:off + len(payload)] = payload
            self.got[chip] += len(payload)
            self.cov[chip][off // self.slot] = True
            self.last_t = time.time()

    def coverage(self, chip):
        return float(self.cov[chip].mean())

    def idle(self, secs):
        return (time.time() - self.last_t) > secs

    def complete(self):
        return self.cov[0].all() and self.cov[1].all()

    def clear(self):
        """Drop everything received so far (coverage + byte counts). Call right
        before triggering the real BRDO so a phantom drain of the stale DDR
        region (the A53 fires one on the first registration after a reload, since
        its burst_req_last starts at 0 vs the MB's monotonic readout_req) does
        not get mistaken for fresh data."""
        with self.lock:
            self.cov = [np.zeros(self.nslot, dtype=bool),
                        np.zeros(self.nslot, dtype=bool)]
            self.got = [0, 0]
            self.last_t = time.time()

    def close(self):
        self.running = False
        self.sock.close()


def uart_cmd(s, cmd, ok_prefixes, timeout=20.0):
    s.reset_input_buffer()
    s.write((cmd + "\n").encode("ascii"))
    s.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = s.readline().decode("ascii", errors="replace").strip()
        if line.startswith(tuple(ok_prefixes)):
            return line
    return ""


def decode_chip(raw, base_ch):
    """raw bytes of a chip region -> {ch: int16 array} for the 2 channels."""
    n = len(raw) - (len(raw) % 16)
    sm = np.frombuffer(bytes(raw[:n]), dtype="<i2").reshape(-1, 8)
    return {base_ch: sm[:, :4].ravel(), base_ch + 1: sm[:, 4:].ravel()}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--local-ip", default="192.168.2.1")
    ap.add_argument("--local-port", type=int, default=5005)
    ap.add_argument("--mb", type=int, default=512, help="MB/chip to capture")
    ap.add_argument("--drain-timeout", type=float, default=60.0)
    ap.add_argument("--out", default="captures/burst.npy")
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()

    bytes_per_chip = args.mb * (1 << 20)
    s = serial.Serial(args.port, args.baud, timeout=5, write_timeout=5)
    time.sleep(0.2)

    asm = Reassembler(args.board_ip, args.cmd_port, args.local_ip,
                      args.local_port, bytes_per_chip)
    asm.register()
    time.sleep(0.3)

    print(f"BCAP {args.mb} MB/chip ...")
    t0 = time.time()
    resp = uart_cmd(s, f"BCAP {args.mb}", ("OK BCAP", "ERR"), timeout=30.0)
    print(" ", resp or "(no BCAP response)")
    if not resp.startswith("OK BCAP"):
        asm.close(); s.close(); sys.exit("capture failed")
    print(f"  capture done in {1000*(time.time()-t0):.0f} ms")

    print("BRDO (reading out over UDP) ...")
    t1 = time.time()
    print(" ", uart_cmd(s, "BRDO", ("OK BRDO", "ERR"), timeout=10.0))
    deadline = time.time() + args.drain_timeout
    while not asm.complete() and time.time() < deadline:
        time.sleep(0.1)
    dt = time.time() - t1
    s.close()

    for chip in (0, 1):
        pct = 100.0 * asm.got[chip] / bytes_per_chip
        print(f"  chip{chip}: {asm.got[chip]}/{bytes_per_chip} bytes ({pct:.2f}%)")
    rate = (asm.got[0] + asm.got[1]) / dt / 1e6
    print(f"  drained {dt:.1f} s, ~{rate:.0f} MB/s")

    chans = {}
    chans.update(decode_chip(asm.buf[0], 0))
    chans.update(decode_chip(asm.buf[1], 2))
    asm.close()
    for ch in range(4):
        x = chans[ch].astype(np.int32)
        print(f"  ch{ch}: {len(x)} samples  min={x.min()} max={x.max()} "
              f"mean={x.mean():.0f}")

    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    np.save(args.out, np.stack([chans[c] for c in range(4)]))
    print(f"saved {args.out}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        span = min(4096, len(chans[0]))
        fig, axes = plt.subplots(4, 1, figsize=(12, 8), sharex=True)
        for ch in range(4):
            t = np.arange(span)               # ns at 1 GS/s
            axes[ch].plot(t, chans[ch][:span] * VOLTS_PER_COUNT, lw=0.7)
            axes[ch].set_ylabel(f"ch{ch} [V]")
            axes[ch].grid(True, alpha=0.3)
        axes[-1].set_xlabel("sample (ns @ 1 GS/s)")
        fig.suptitle(f"burst capture {args.mb} MB/chip, first {span} samples")
        png = args.out.rsplit(".", 1)[0] + ".png"
        fig.savefig(png, dpi=110)
        print(f"wrote {png}")


if __name__ == "__main__":
    main()
