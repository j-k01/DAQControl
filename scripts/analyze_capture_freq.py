#!/usr/bin/env python3
"""Per-channel RMS + dominant frequency of burst .npy captures, plus an
identity check across files (to catch STALE/repeated data). Channels are
full-rate (1 GS/s).
Usage: python scripts/analyze_capture_freq.py cap1.npy cap2.npy ..."""
import sys
import hashlib
import numpy as np

FS = 1e9
sigs = {}
for path in sys.argv[1:]:
    d = np.load(path)                      # shape [4, N]
    print(f"=== {path}  shape={d.shape} ===")
    for ch in range(d.shape[0]):
        x = d[ch].astype(np.float64)
        x = x - x.mean()
        rms = float(np.sqrt(np.mean(x * x)))
        w = x * np.hanning(len(x))
        X = np.abs(np.fft.rfft(w))
        f = np.fft.rfftfreq(len(x), 1.0 / FS)
        lo = np.searchsorted(f, 30e3)      # skip DC/drift below 30 kHz
        pk = lo + int(np.argmax(X[lo:]))
        h = hashlib.md5(d[ch].tobytes()).hexdigest()[:8]
        sigs.setdefault(h, []).append(f"{path}:ch{ch}")
        print(f"  ch{ch}: rms={rms:9.1f}  peak={f[pk]/1e3:9.1f} kHz  md5={h}")

dups = {h: v for h, v in sigs.items() if len(v) > 1}
if dups:
    print("\n!!! IDENTICAL channel data across captures (possible STALE data):")
    for h, v in dups.items():
        print(f"  md5 {h}: {v}")
else:
    print("\nOK: every channel/capture is byte-distinct (no stale/repeated data).")
