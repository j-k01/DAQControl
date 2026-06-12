"""Plot the 4 logical ADC channels from a capture_adc_bram_uart.py CSV.

CSV is long-format: columns frame, source(0..7), word_hex, lo16, hi16,
lo16_signed, hi16_signed -- one row per frame-word. Each chip contributes 4
words; a logical channel is a (low-word, high-word) pair, two chronological
16-bit samples per word:
    ch0 = source 0,1   ch1 = source 2,3   ch2 = source 4,5   ch3 = source 6,7
Samples run at the full 1 GS/s ADC rate.
"""
import sys
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "captures/bram_4ch.csv"
fs = 1e9

# frames[frame][source] = (lo_signed, hi_signed)
frames = {}
with open(path) as f:
    for row in csv.DictReader(f):
        fr = int(row["frame"])
        src = int(row["source"])
        frames.setdefault(fr, {})[src] = (
            int(row["lo16_signed"]), int(row["hi16_signed"]))

order = sorted(frames)


def chan(lo_src, hi_src):
    out = []
    for fr in order:
        d = frames[fr]
        if lo_src in d and hi_src in d:
            lo, hi = d[lo_src], d[hi_src]
            out += [lo[0], lo[1], hi[0], hi[1]]
    return np.array(out, dtype=np.float64)


chans = {0: chan(0, 1), 1: chan(2, 3), 2: chan(4, 5), 3: chan(6, 7)}

fig, axes = plt.subplots(4, 2, figsize=(15, 9))
nshow = min(400, len(chans[0]))
for ch in range(4):
    x = chans[ch]
    axes[ch][0].plot(np.arange(nshow), x[:nshow], lw=0.8)
    axes[ch][0].set_ylabel(f"ch{ch} [counts]")
    axes[ch][0].grid(True, alpha=0.3)
    xz = x - x.mean()
    n = len(xz)
    sp = np.abs(np.fft.rfft(xz * np.hanning(n)))
    spd = 20 * np.log10(sp / (sp.max() or 1) + 1e-9)
    fr = np.fft.rfftfreq(n, 1 / fs) / 1e6
    axes[ch][1].plot(fr, spd, lw=0.7)
    axes[ch][1].set_ylabel(f"ch{ch} [dB]")
    axes[ch][1].set_xlim(0, 500)
    axes[ch][1].set_ylim(-90, 3)
    axes[ch][1].grid(True, alpha=0.3)
    k = np.argmax(sp[1:]) + 1
    print(f"ch{ch}: n={n} pkpk={int(x.max()-x.min())} mean={x.mean():.0f} "
          f"peak_tone={fr[k]:.3f} MHz")
axes[3][0].set_xlabel("sample (1 GS/s)")
axes[3][1].set_xlabel("frequency [MHz]")
fig.tight_layout()
out = path.rsplit(".", 1)[0] + ".png"
fig.savefig(out, dpi=110)
print("wrote", out)
