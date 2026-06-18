#!/usr/bin/env python3
"""DDS loopback tone-purity + alignment check (no BRAM involved).

Sets every DAC to its internal DDS sine, bursts a short capture of all 4 ADC
channels (DAC->ADC loopback), and for each channel reports:

  * ALIVE/RAILED  -- a railed (open-input) channel sits near full scale.
  * peak tone freq + SFDR (dB to the worst spur) -- is it a CLEAN single tone?
  * inter-channel sample lag vs ch0 (cross-correlation) -- are the channels
    sample-aligned? With shared DAC read pointers + SYSREF this should be ~0
    (any residual is fixed analog/JESD skew, not a sync error).

This isolates the analog loopback + ADC from the DAC BRAM program path, so it
answers "are the loopback cables good and the tones clean?" directly.

  python scripts/dds_tone_test.py --mb 16 --freq-mhz 10
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import serial

sys.path.insert(0, str(Path(__file__).resolve().parent))
from burst_capture import Reassembler, uart_cmd, decode_chip  # noqa: E402

FS = 1.0e9                      # ADC sample rate (capture domain)
DDS_ACC_BITS = 24               # phase accumulator width feeding reg19[23:0]


def set_dds_freq(s, freq_hz):
    """reg19[23:0] = phase increment (DDS step); 0 selects HDL default."""
    inc = int(round(freq_hz / FS * (1 << DDS_ACC_BITS))) & 0xFFFFFF
    uart_cmd(s, f"DDSI 0x{inc:06X}", ("DDS inc", "ERR"), timeout=2)
    return inc


def sfdr_db(x):
    """Single-tone SFDR: dB from the fundamental peak to the next-largest spur
    (DC and the 2 bins around the fundamental excluded)."""
    x = x.astype(np.float64)
    x = x - x.mean()
    n = len(x)
    w = np.hanning(n)
    X = np.abs(np.fft.rfft(x * w))
    X[0] = 0.0
    k = int(np.argmax(X))
    fund = X[k]
    guard = 3
    spur = X.copy()
    spur[max(0, k - guard):k + guard + 1] = 0.0
    s = spur.max()
    freq = k / n * FS
    return freq, (20.0 * np.log10(fund / s) if s > 0 else float("inf"))


def lag(a, b, W=4096):
    a = a.astype(float) - a.mean()
    b = b.astype(float) - b.mean()
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    N = 1
    while N < 2 * n:
        N *= 2
    c = np.fft.irfft(np.fft.rfft(a, N) * np.conj(np.fft.rfft(b, N)), N)
    c = np.concatenate([c[N - W:], c[:W + 1]])
    return int(np.argmax(c) - W)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--local-ip", default="192.168.2.1")
    ap.add_argument("--local-port", type=int, default=5005)
    ap.add_argument("--mb", type=int, default=16)
    ap.add_argument("--freq-mhz", type=float, default=10.0)
    ap.add_argument("--drain-timeout", type=float, default=30.0)
    args = ap.parse_args()

    bytes_per_chip = args.mb * (1 << 20)
    s = serial.Serial(args.port, 115200, timeout=5, write_timeout=5)
    time.sleep(0.2)

    print("STAT:", uart_cmd(s, "STAT", ("RW0", "decoded", "DAQ"), timeout=3) or "(no reply)")
    print("Setting all DACs to DDS sine ...")
    print("  NSRC:", uart_cmd(s, "NSRC all dds", ("DAC source", "ERR"), timeout=3))
    inc = set_dds_freq(s, args.freq_mhz * 1e6)
    print(f"  DDS step = {inc} (~{args.freq_mhz:.1f} MHz target @ {FS/1e9:.0f} GS/s)")
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
    while time.time() < deadline:
        if asm.complete():
            break
        if (time.time() - asm.last_t) > 0.6 and asm.coverage(0) > 0:
            break
        time.sleep(0.1)
    s.close()
    for chip in (0, 1):
        print(f"  chip{chip}: coverage {100.0*asm.coverage(chip):.1f}%")

    chans = {}
    chans.update(decode_chip(asm.buf[0], 0))
    chans.update(decode_chip(asm.buf[1], 2))
    asm.close()
    sig = {c: chans[c].astype(np.float64) for c in range(4)}

    print("\n--- per-channel DDS tone purity ---")
    span = min(1 << 16, *(len(sig[c]) for c in range(4)))
    alive = []
    for c in range(4):
        x = sig[c][:span]
        amp = (np.percentile(x, 95) - np.percentile(x, 5)) / 2.0
        railed = amp > 25000.0 or amp < 50.0
        freq, sfdr = sfdr_db(x)
        tag = "RAILED/dead" if railed else f"{sfdr:5.1f} dB SFDR @ {freq/1e6:6.2f} MHz"
        if not railed:
            alive.append(c)
        print(f"  ch{c}: +/-{amp:7.0f}  {tag}")

    if len(alive) >= 2:
        print("\n--- inter-channel sample lag (vs ch%d) ---" % alive[0])
        ref = alive[0]
        for c in alive[1:]:
            cross = "chip0<->chip1" if (ref < 2) != (c < 2) else "same chip"
            print(f"  ch{ref} vs ch{c} [{cross}]: lag = {lag(sig[ref], sig[c])} samples")

    np.save("captures/dds_tone_raw.npy", np.stack([sig[c] for c in range(4)]))
    print(f"\nalive={alive}; saved captures/dds_tone_raw.npy")


if __name__ == "__main__":
    main()
