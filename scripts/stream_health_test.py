#!/usr/bin/env python3
"""Trustworthy stream health + source-switch test (self-contained).

The earlier diagnostics were unreliable because they did FFT work INSIDE the
receive loop, so Python fell behind and dropped packets at the HOST -- and that
host loss looked like board-side corruption. This script avoids that:

  * the receive thread does NOTHING but timestamp and stash raw bytes (so it
    keeps up), with a 64 MB socket buffer;
  * ALL parsing/FFT happens AFTER capture;
  * it separates the three independent failure modes -- HOST loss (gaps in the
    per-packet seq), BOARD drops (the `drops` header field the A53 reports), and
    true CONTENT discontinuity -- and only trusts content once loss/drops are 0;
  * it classifies only full-size packets, so small catch-up packets don't get
    misread as "discontinuities".

No GUI deps (does not import pyqtgraph). Runs two phases: steady-state on BRAM,
then a BRAM->DDS switch, with a clear PASS/FAIL for each.

  python scripts/stream_health_test.py
  python scripts/stream_health_test.py --decim 256
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import threading
import time
from collections import Counter

import numpy as np
import serial

HDR = struct.Struct("<IHHIIIIII")
MAGIC = 0x53514144
PROGRAM_SAMPLES = 16384
BRAM_F = 0.916   # MHz, ch0 BRAM tone
DDS_F = 0.244    # MHz, DDS tone at step 4096
DDS_STEP = 4096
MIN_SAMPLES = 512   # only classify packets with at least this many ch0 samples


# ----------------------------------------------------------------- DAC content
def clamp_s16(v):
    return max(-32768, min(32767, int(round(v))))


def pack_pair(s0, s1):
    return ((s1 & 0xFFFF) << 16) | (s0 & 0xFFFF)


def sine_words(freq_mhz, amplitude):
    cycles = max(1, round(freq_mhz * PROGRAM_SAMPLES / 1000.0))  # 1 GS/s
    ph = 2.0 * math.pi * cycles / PROGRAM_SAMPLES
    s = [clamp_s16(amplitude * math.sin(ph * i)) for i in range(PROGRAM_SAMPLES)]
    return [pack_pair(s[2 * i], s[2 * i + 1]) for i in range(PROGRAM_SAMPLES // 2)]


# ----------------------------------------------------------------- UART control
class DacControl:
    def __init__(self, port, baud=115200):
        self.s = serial.Serial(port, baud, timeout=2, write_timeout=3)
        self.lock = threading.Lock()
        time.sleep(0.2)

    def _readuntil(self, prefixes):
        deadline = time.time() + 4
        while time.time() < deadline:
            line = self.s.readline().decode("ascii", errors="replace").strip()
            if line.startswith(tuple(prefixes)):
                return line
        return ""

    def cmd(self, c, ok=("OK", "DAC source", "STRM")):
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write((c + "\n").encode("ascii"))
            self.s.flush()
            return self._readuntil(ok)

    def prog(self, ch, words):
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write(f"PROG {ch} {len(words)}\n".encode("ascii"))
            self.s.flush()
            self._readuntil(("PGRD",))
            self.s.write(struct.pack(f"<{len(words)}I", *words))
            self.s.flush()
            self._readuntil((f"OK PROG ch={ch}",))

    def set_source(self, ch, label):
        return self.cmd(f"NSRC {ch} {label}", ok=("DAC source", "ERR"))

    def close(self):
        self.s.close()


# ----------------------------------------------------------------- UDP capture
class Capture:
    """Fast raw UDP capture: timestamp + stash bytes only."""

    def __init__(self, board_ip="192.168.2.10", cmd_port=5006,
                 local_ip="192.168.2.1", local_port=5005):
        self.pkts = []          # list of (t, bytes)
        self.run = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 << 20)
        self.sock.bind((local_ip, local_port))
        self.sock.settimeout(0.5)
        self.board = (board_ip, cmd_port)
        self.sock.sendto(b"STRM", self.board)
        self.t0 = time.time()
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        while self.run:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self.pkts.append((time.time() - self.t0, bytes(data)))

    def close(self):
        self.run = False
        try:
            self.sock.sendto(b"STOP", self.board)
        except OSError:
            pass
        self.sock.close()


# ----------------------------------------------------------------- analysis
def classify(samples, fs):
    v = samples.astype(float)
    v = v - v.mean()
    if v.std() < 5:
        return "_"
    w = np.hanning(len(v))
    Y = np.abs(np.fft.rfft(v * w))
    f = np.fft.rfftfreq(len(v), 1.0 / fs)
    fm = f[1 + np.argmax(Y[1:])] / 1e6
    if abs(fm - BRAM_F) < 0.12:
        return "B"
    if abs(fm - DDS_F) < 0.12:
        return "D"
    return "?"


def parse(pkts, fs, chip=0):
    rows = []
    for t, data in pkts:
        if len(data) < HDR.size:
            continue
        magic, _v, hdr, seq, ch, _o, count, drops, dec = HDR.unpack_from(data)
        if magic != MAGIC or ch != chip:
            continue
        payload = data[hdr:hdr + count]
        payload = payload[: len(payload) - (len(payload) % 16)]
        sm = np.frombuffer(payload, dtype="<i2").reshape(-1, 8)
        s0 = sm[:, :4].ravel()
        rows.append({"t": t, "seq": seq, "drops": drops, "n": len(s0),
                     "cls": classify(s0, fs) if len(s0) >= MIN_SAMPLES else None})
    rows.sort(key=lambda r: r["seq"])
    return rows


def host_loss(rows):
    if len(rows) < 2:
        return 0, 0
    seqs = [r["seq"] for r in rows]
    span = seqs[-1] - seqs[0]
    return span + 1 - len(rows), span


def run_phase(pkts, label, t_lo, t_hi, fs, expect):
    rows = [r for r in parse(pkts, fs) if t_lo <= r["t"] < t_hi]
    if not rows:
        print(f"  {label}: no packets in window")
        return False
    missing, _ = host_loss(rows)
    bd = rows[-1]["drops"] - rows[0]["drops"]
    full = [r for r in rows if r["cls"] is not None]
    good = sum(1 for r in full if r["cls"] == expect)
    other = [r["cls"] for r in full if r["cls"] not in (expect, "_")]
    pct = 100.0 * good / max(1, len(full))
    ok = (missing == 0 and bd == 0 and pct > 99.0)
    print(f"  {label}: {len(rows)} pkts  host_loss={missing}  "
          f"board_drops={bd}  content={pct:.1f}% {expect}  "
          f"[{'PASS' if ok else 'FAIL'}]")
    if other:
        print(f"     non-{expect} classes: {dict(Counter(other))}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--decim", type=int, default=128)
    args = ap.parse_args()
    fs = 1e9 / args.decim

    dac = DacControl(args.port)
    dac.cmd("WRTE 2 0x01000018")
    dac.cmd("DDSI 0x%06X" % (DDS_STEP & 0xFFFFFF))
    dac.prog(0, sine_words(BRAM_F, 0x5000))
    resp = dac.cmd(f"STRM {args.decim}", ok=("OK STRM", "ERR"))
    print(resp or "(no STRM response from board UART)")
    dac.set_source(0, "BRAM")

    cap = Capture()
    time.sleep(0.8)                       # settle

    # ---- guard: nothing streaming -> clear diagnosis, not a traceback ----
    if not cap.pkts:
        cap.close()
        dac.cmd("STRM STOP", ok=("OK STRM", "ERR"))
        dac.close()
        print("\nNO PACKETS RECEIVED. The board UART answered but no UDP stream "
              "is arriving.")
        print("Most likely the A53 PS-eth streamer is not running. Checklist:")
        print("  * On capitolpeak: xsct load_ps_eth_stream.tcl   (revive the A53"
              " app; do program_and_load.tcl first if the A53 download wedges)")
        print("  * NIC 192.168.2.1/24 up, board at 192.168.2.10, cable connected")
        print("  * `STRM STAT` over UART shows active=1")
        return

    steady_lo = cap.pkts[-1][0]
    time.sleep(1.5)                       # ---- steady-state window ----
    t_switch = cap.pkts[-1][0]
    dac.set_source(0, "DDS")              # ---- the switch ----
    time.sleep(2.0)                       # ---- post-switch window ----
    cap.close()
    dac.cmd("STRM STOP", ok=("OK STRM", "ERR"))
    dac.close()

    print(f"\n=== stream health @ decim={args.decim} "
          f"({1000.0/args.decim:.2f} MS/s/ch, {1e9/args.decim/2e6:.1f} MB/s/ch) ===")
    print("1) STEADY STATE (should be clean BRAM):")
    run_phase(cap.pkts, "steady ", steady_lo, t_switch, fs, "B")

    print("2) SWITCH BRAM->DDS (how fast does new source take over?):")
    rows = [r for r in parse(cap.pkts, fs)
            if r["t"] >= t_switch and r["cls"] is not None]
    if not rows:
        print("   no classifiable packets after switch")
        return
    by_time = sorted(rows, key=lambda r: r["t"])
    by_seq = sorted(rows, key=lambda r: r["seq"])
    first_d = next((r["t"] for r in by_time if r["cls"] == "D"), None)
    last_b = max((r["t"] for r in rows if r["cls"] == "B"), default=None)
    flips = sum(1 for i in range(1, len(by_seq))
                if by_seq[i]["cls"] in "BD" and by_seq[i - 1]["cls"] in "BD"
                and by_seq[i]["cls"] != by_seq[i - 1]["cls"])
    bd = by_seq[-1]["drops"] - by_seq[0]["drops"]
    if first_d is not None:
        print(f"   first DDS at +{1000*(first_d - t_switch):.0f} ms")
    if last_b is not None:
        print(f"   settles to DDS at +{1000*(last_b - t_switch):.0f} ms "
              f"(last BRAM seen)")
    print(f"   board_drops during switch: {bd}   B<->D flips: {flips}")
    settle = (last_b - t_switch) if last_b else 0
    clean = flips <= 2 and settle < 0.3
    print(f"   [{'PASS' if clean else 'NEEDS WORK'}] "
          f"(target: clean cut, settle <300 ms, no re-alternation)")


if __name__ == "__main__":
    main()
