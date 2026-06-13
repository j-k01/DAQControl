#!/usr/bin/env python3
"""Burst-capture cross-channel ALIGNMENT test (DAC->ADC loopback).

Programs each DAC with a square wave that shares a COMMON edge phase but a
DISTINCT amplitude per channel, sets them all to BRAM (each DAC is cabled back
to its ADC channel), then does a full-rate burst capture of all 4 ADC channels
and checks:

  * IDENTITY / decode -- each captured channel's amplitude matches the one we
    programmed (so we know channels aren't duplicated/swapped by the decode).
  * ALIGNMENT -- cross-correlating the channels (especially ch0/ch1 on chip 0
    vs ch2/ch3 on chip 1, the two separate DMAs) gives the inter-channel sample
    lag. Aligned 1-to-1 => lag ~0 on every pair, CONSTANT across the whole
    capture (a varying lag would mean a dropped beat / lost samples).

Because both DMAs start on one trigger and the ADCs are SYSREF-aligned, the
edges should land on the same sample index in all four channels.

  python scripts/burst_align_test.py --mb 16
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import time
from pathlib import Path

import numpy as np
import serial

sys.path.insert(0, str(Path(__file__).resolve().parent))
from burst_capture import Reassembler, uart_cmd, decode_chip  # noqa: E402

PROGRAM_SAMPLES = 16384
# common edge phase for all channels; distinct amplitude per channel (counts)
SQUARE_PERIOD = 1024          # ns at 1 GS/s -> 16 periods per BRAM loop
CH_AMPL = [0x2000, 0x3000, 0x4000, 0x5000]


def clamp_s16(v):
    return max(-32767, min(32767, int(round(v))))


def pack_pair(s0, s1):
    return ((s1 & 0xFFFF) << 16) | (s0 & 0xFFFF)


def square_words(period, amplitude):
    """Bipolar square wave (survives AC coupling), rising edge at phase 0,
    tiled across the full 16384-sample BRAM loop."""
    one = np.where((np.arange(period) % period) < period // 2,
                   amplitude, -amplitude).astype(int)
    full = np.resize(one, PROGRAM_SAMPLES)
    return [pack_pair(clamp_s16(full[2 * k]), clamp_s16(full[2 * k + 1]))
            for k in range(PROGRAM_SAMPLES // 2)]


def prog_channel(s, ch, words):
    s.reset_input_buffer()
    s.write(f"PROG {ch} {len(words)}\n".encode())
    s.flush()
    # wait PGRD
    t = time.time() + 4
    while time.time() < t:
        if s.readline().decode("ascii", "replace").strip().startswith("PGRD"):
            break
    s.write(struct.pack(f"<{len(words)}I", *words))
    s.flush()
    t = time.time() + 4
    while time.time() < t:
        if s.readline().decode("ascii", "replace").strip().startswith(
                f"OK PROG ch={ch}"):
            break
    # full-loop frame_count + program_enable (proven value, no IZH-bit clash)
    uart_cmd(s, "WRTE 3 0x00100060", ("OK", "RW3"), timeout=2)


def lag(a, b, W=64):
    """Integer sample lag (|lag| <= W) that best aligns b to a. Searches only a
    small window around 0 so a periodic pattern's repeat peaks don't fool it
    (the true misalignment is at most a few samples)."""
    a = a - a.mean()
    b = b - b.mean()
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    best_k, best_c = 0, -1e300
    for k in range(-W, W + 1):
        if k >= 0:
            c = float(np.dot(a[k:], b[:n - k]))
        else:
            c = float(np.dot(a[:n + k], b[-k:]))
        if c > best_c:
            best_c, best_k = c, k
    return best_k


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--local-ip", default="192.168.2.1")
    ap.add_argument("--local-port", type=int, default=5005)
    ap.add_argument("--mb", type=int, default=16, help="MB/chip (small = fast)")
    ap.add_argument("--drain-timeout", type=float, default=30.0)
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()

    bytes_per_chip = args.mb * (1 << 20)
    s = serial.Serial(args.port, 115200, timeout=5, write_timeout=5)
    time.sleep(0.2)

    print("Programming square-wave loopback patterns (distinct amplitude/ch)...")
    uart_cmd(s, "WRTE 2 0x01000018", ("OK", "RW2"), timeout=2)
    for ch in range(4):
        prog_channel(s, ch, square_words(SQUARE_PERIOD, CH_AMPL[ch]))
        print(f"  DAC{ch}: square {SQUARE_PERIOD} ns, +/-{CH_AMPL[ch]} counts")
    uart_cmd(s, "NSRC all bram", ("DAC source", "ERR"), timeout=3)
    time.sleep(0.3)

    asm = Reassembler(args.board_ip, args.cmd_port, args.local_ip,
                      args.local_port, bytes_per_chip)
    asm.register()
    time.sleep(0.3)

    print(f"BCAP {args.mb} MB/chip ...")
    r = uart_cmd(s, f"BCAP {args.mb}", ("OK BCAP", "ERR"), timeout=30)
    print(" ", r)
    if not r.startswith("OK BCAP"):
        asm.close(); s.close(); sys.exit("capture failed")
    print(" ", uart_cmd(s, "BRDO", ("OK BRDO", "ERR"), timeout=10))
    deadline = time.time() + args.drain_timeout
    while not asm.complete() and time.time() < deadline:
        time.sleep(0.1)
    s.close()
    for chip in (0, 1):
        print(f"  chip{chip}: {100.0*asm.got[chip]/bytes_per_chip:.2f}% received")

    chans = {}
    chans.update(decode_chip(asm.buf[0], 0))
    chans.update(decode_chip(asm.buf[1], 2))
    asm.close()
    sig = {c: chans[c].astype(np.float64) for c in range(4)}

    print("\n--- identity / decode (amplitude per channel) ---")
    ok_id = True
    for c in range(4):
        amp = (np.percentile(sig[c], 95) - np.percentile(sig[c], 5)) / 2.0
        exp = CH_AMPL[c]
        match = abs(amp - exp) < 0.35 * exp
        ok_id &= match
        print(f"  ch{c}: measured +/-{amp:.0f}  expected +/-{exp}  "
              f"[{'OK' if match else 'MISMATCH'}]")

    print("\n--- alignment (sample lag vs ch0, in windows across the capture) ---")
    nwin = 5
    span = min(8192, len(sig[0]) // (nwin + 1))   # ~8 square periods, fast
    ok_align = True
    for c in range(1, 4):
        lags = []
        for w in range(nwin):
            o = w * (len(sig[0]) // nwin)
            o = min(o, len(sig[0]) - span)
            lags.append(lag(sig[0][o:o + span], sig[c][o:o + span]))
        const = (max(lags) - min(lags)) == 0
        aligned = all(abs(l) <= 1 for l in lags)
        ok_align &= aligned and const
        chip = "chip1 (cross-DMA!)" if c >= 2 else "chip0"
        print(f"  ch0 vs ch{c} [{chip}]: lags={lags}  "
              f"{'ALIGNED' if aligned else 'OFFSET'}"
              f"{'' if const else '  (NOT CONSTANT -> dropped samples!)'}")

    print(f"\nRESULT: identity {'OK' if ok_id else 'FAIL'}, "
          f"alignment {'OK (all channels 1-to-1)' if ok_align else 'FAIL'}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        span = 4 * SQUARE_PERIOD
        fig, ax = plt.subplots(figsize=(12, 5))
        for c in range(4):
            ax.plot(sig[c][:span], lw=0.8, label=f"ch{c}")
        ax.axvline(0, color="k", ls=":")
        ax.set_xlabel("sample (ns @ 1 GS/s)")
        ax.set_title("burst loopback -- 4 ADC channels overlaid (edges align)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.savefig("captures/burst_align.png", dpi=110)
        print("wrote captures/burst_align.png")


if __name__ == "__main__":
    main()
