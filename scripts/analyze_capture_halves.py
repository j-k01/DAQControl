#!/usr/bin/env python3
"""Split each burst .npy into quarters and report per-section RMS + dominant
freq + md5, then flag identical sections ACROSS captures (STALE data that a
whole-capture compare would miss). Channels are 1 GS/s.
Usage: python scripts/analyze_capture_halves.py cap1.npy cap2.npy ..."""
import sys
import hashlib
import numpy as np

FS = 1e9
NSEC = 4


def dom_khz(x):
    x = x.astype(np.float64)
    x = x - x.mean()
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1.0 / FS)
    lo = np.searchsorted(f, 30e3)
    return f[lo + int(np.argmax(X[lo:]))] / 1e3


secs = {}
for path in sys.argv[1:]:
    d = np.load(path)
    n = d.shape[1]
    print(f"=== {path}  N={n} (sections of {n//NSEC}) ===")
    for ch in range(d.shape[0]):
        parts = []
        for s in range(NSEC):
            seg = d[ch][s * n // NSEC:(s + 1) * n // NSEC]
            h = hashlib.md5(seg.tobytes()).hexdigest()[:6]
            rms = float(np.sqrt(np.mean((seg.astype(np.float64) - seg.mean()) ** 2)))
            parts.append(f"[{s}]rms={rms:6.0f} f={dom_khz(seg):7.0f}k {h}")
            secs.setdefault(h, []).append(f"{path.split(chr(92))[-1]}:ch{ch}:s{s}")
        print(f"  ch{ch}: " + "  ".join(parts))

dups = {h: v for h, v in secs.items() if len(v) > 1}
if dups:
    print("\n!!! IDENTICAL sections across captures (STALE / not refreshed):")
    for h, v in sorted(dups.items()):
        print(f"  {h}: {v}")
else:
    print("\nOK: every section is byte-distinct across all captures.")
