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
# Same pseudo-random sequence on every channel (so cross-correlation has a sharp,
# unambiguous peak at the inter-channel lag), scaled by a distinct amplitude per
# channel (so a decode/duplicate bug can't fake alignment). "chip" held PRN_K
# samples to keep it inside the loopback bandwidth.
PRN_K = 8                     # samples per random chip (~125 MHz)
PRN_SEED = 0xC0DE
CH_AMPL = [0x2000, 0x3000, 0x4000, 0x5000]


def clamp_s16(v):
    return max(-32767, min(32767, int(round(v))))


def pack_pair(s0, s1):
    return ((s1 & 0xFFFF) << 16) | (s0 & 0xFFFF)


def prn_words(amplitude):
    """Band-limited up/down chirp (2->38 MHz and back over the 16384-sample
    loop, so it's loop-continuous). Same on every channel; pulse-compression
    gives a sharp, strong cross-correlation peak that survives the ~40 MHz
    loopback, unlike a fast PRN."""
    n = PROGRAM_SAMPLES
    fs = 1.0e9
    half = n // 2
    t = np.arange(half) / fs
    f0, f1 = 2.0e6, 38.0e6
    k = (f1 - f0) / (half / fs)
    ph_up = 2.0 * np.pi * (f0 * t + 0.5 * k * t * t)
    up = np.sin(ph_up)
    full = np.concatenate([up, up[::-1]])[:n]      # up then down -> seamless loop
    full = (amplitude * full).astype(int)
    return [pack_pair(clamp_s16(full[2 * j]), clamp_s16(full[2 * j + 1]))
            for j in range(n // 2)]


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


def lag(a, b, W=8192):
    """Integer sample lag (|lag| <= W) that aligns b to a, via FFT cross-
    correlation (fast, wide search). The chirp's pulse-compression gives one
    sharp peak at the true inter-channel offset (which may be large, since the
    DAC BRAM loops can start at different phases)."""
    a = a.astype(float) - a.mean()
    b = b.astype(float) - b.mean()
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    N = 1
    while N < 2 * n:
        N *= 2
    c = np.fft.irfft(np.fft.rfft(a, N) * np.conj(np.fft.rfft(b, N)), N)
    c = np.concatenate([c[N - W:], c[:W + 1]])     # lags -W .. +W
    return int(np.argmax(c) - W)


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

    print("Programming chirp loopback patterns (common sweep, distinct amp/ch)...")
    uart_cmd(s, "WRTE 2 0x01000018", ("OK", "RW2"), timeout=2)
    for ch in range(4):
        prog_channel(s, ch, prn_words(CH_AMPL[ch]))
        print(f"  DAC{ch}: chirp 2-38 MHz, +/-{CH_AMPL[ch]} counts")
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
    # wait until the drain goes idle (no packet for 0.6 s) or it all arrives
    while time.time() < deadline:
        if asm.complete():
            break
        if (time.time() - asm.last_t) > 0.6 and asm.coverage(0) > 0:
            break
        time.sleep(0.1)
    s.close()
    for chip in (0, 1):
        print(f"  chip{chip}: coverage {100.0*asm.coverage(chip):.2f}% "
              f"(lossless = 100%)")

    chans = {}
    chans.update(decode_chip(asm.buf[0], 0))
    chans.update(decode_chip(asm.buf[1], 2))
    asm.close()
    sig = {c: chans[c].astype(np.float64) for c in range(4)}

    amps = {c: (np.percentile(sig[c], 95) - np.percentile(sig[c], 5)) / 2.0
            for c in range(4)}
    alive = [c for c in range(4) if amps[c] < 25000.0]   # railed = open input
    dead = [c for c in range(4) if c not in alive]
    ratios = {c: amps[c] / CH_AMPL[c] for c in alive}
    gain = float(np.median(list(ratios.values()))) if alive else 0.0

    print("\n--- identity / decode (amplitude per channel) ---")
    ok_id = True
    for c in range(4):
        if c in dead:
            print(f"  ch{c}: +/-{amps[c]:.0f}  RAILED -> open ADC input / loopback "
                  f"cable loose")
            continue
        rel = ratios[c] / gain if gain else 0.0
        match = 0.7 < rel < 1.4
        ok_id &= match
        print(f"  ch{c}: +/-{amps[c]:.0f}  loopback gain {ratios[c]:.2f}x  "
              f"[{'OK' if match else 'MISMATCH'}]")
    print(f"  estimated loopback gain ~{gain:.2f}x; alive={alive} dead={dead}")

    print("\n--- alignment (sample lag vs reference, windows across capture) ---")
    print("  (the meaningful test is a CONSTANT offset = the two chips stay")
    print("   sample-locked; a nonzero value is the fixed DAC-loop-phase + cable")
    print("   skew, not a capture error. A DRIFTING offset = lost sync.)")
    ref = alive[0] if alive else 0
    nwin = 6
    span = 16384
    ok_align = True
    pairs = [c for c in alive if c != ref]
    for c in pairs:
        lags = []
        for w in range(nwin):
            o = min(w * (len(sig[ref]) // nwin), len(sig[ref]) - span)
            lags.append(lag(sig[ref][o:o + span], sig[c][o:o + span]))
        steady = lags[1:]                      # skip the first-loop start transient
        const = (max(steady) - min(steady)) <= 2
        ok_align &= const
        chip = "cross-DMA chip0<->chip1" if (ref < 2) != (c < 2) else "same chip"
        print(f"  ch{ref} vs ch{c} [{chip}]: lags={lags}  offset~{steady[-1]} "
              f"samples  {'SAMPLE-LOCKED (constant)' if const else 'DRIFTING -> FAIL'}")

    # per-channel losslessness: the PRN repeats every 16384 samples, so a lossless
    # capture has ch[n] == ch[n+16384] everywhere (period-lag stays 0). A dropped
    # beat shifts everything after it -> the period-lag drifts.
    print("\n--- losslessness (PRN self-periodicity per channel) ---")
    LOOP = PROGRAM_SAMPLES
    ok_loss = True
    for c in alive:
        drifts = []
        for w in range(6):
            o = min(w * (len(sig[c]) // 6), len(sig[c]) - LOOP - 8192)
            drifts.append(lag(sig[c][o:o + 8192], sig[c][o + LOOP:o + LOOP + 8192]))
        good = all(d == 0 for d in drifts[1:])   # skip first-loop start transient
        ok_loss &= good
        print(f"  ch{c}: period-lag = {drifts}  "
              f"{'LOSSLESS (steady)' if good else 'DRIFT -> dropped beats!'}")

    np.save("captures/burst_align_raw.npy", np.stack([sig[c] for c in range(4)]))
    print(f"\nRESULT: dead {dead} (reseat cables); live {alive}: "
          f"identity {'OK' if ok_id else 'FAIL'}, "
          f"lossless {'OK' if ok_loss else 'FAIL'}, "
          f"alignment {'OK (1-to-1)' if ok_align and pairs else 'FAIL' if pairs else 'n/a'}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        span = 256
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
