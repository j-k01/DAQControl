"""Plot the RAW capture from capture_adc_bram_uart.py with NO channel assumptions.

CSV is long-format: frame, source(0..7), word_hex, lo16, hi16, lo16_signed,
hi16_signed -- one row per frame-word. We plot all 8 source words separately
(each = two chronological 16-bit samples) so nothing is hidden by a channel-
pairing guess. 8 sources = the four 64-bit ADC channel words split into halves.
"""
import sys
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "captures/dds_raw.csv"
nshow = int(sys.argv[2]) if len(sys.argv) > 2 else 300

frames = {}
with open(path) as f:
    for row in csv.DictReader(f):
        fr = int(row["frame"]); src = int(row["source"])
        frames.setdefault(fr, {})[src] = (int(row["lo16_signed"]), int(row["hi16_signed"]))

order = sorted(frames)
# interleave lo,hi per source into a continuous sample stream
streams = {s: [] for s in range(8)}
for fr in order:
    d = frames[fr]
    for s in range(8):
        if s in d:
            streams[s].extend(d[s])

fig, axes = plt.subplots(8, 1, figsize=(13, 12), sharex=True)
for s in range(8):
    x = np.array(streams[s])
    axes[s].plot(x[:nshow], lw=0.8)
    axes[s].set_ylabel("src%d" % s)
    axes[s].grid(True, alpha=0.3)
    axes[s].set_ylim(-2000, 2000) if x[:nshow].ptp() < 4000 else None
    print("src%d: n=%d pkpk=%d mean=%.0f first=%s"
          % (s, len(x), int(x.max()-x.min()), x.mean(), list(x[:8])))
axes[-1].set_xlabel("sample (two per frame word, 1 GS/s)")
fig.suptitle("RAW ADC capture, all 8 frame-words (no channel pairing) -- %s" % path)
fig.tight_layout()
out = path.rsplit(".", 1)[0] + "_raw8.png"
fig.savefig(out, dpi=110)
print("wrote", out)
