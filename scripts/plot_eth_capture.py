"""Plot an Ethernet-received ADC capture (.bin of u32 frame words).

Each DMA frame word packs ADC samples per the capture format; for a quick
first-light view we unpack as little-endian s16 pairs and plot both chips.

Usage: python plot_eth_capture.py <chip0.bin> <chip1.bin> <out.png>
"""
import struct
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ADC_VOLTS_PER_COUNT = 1.9 / 65536.0


def load_s16(path):
    with open(path, "rb") as f:
        raw = f.read()
    n = len(raw) // 2
    return struct.unpack("<%dh" % n, raw[: n * 2])


def main():
    chip0, chip1, out = sys.argv[1], sys.argv[2], sys.argv[3]
    fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=False)
    for ax, path, label in ((axes[0], chip0, "chip0 (IN1/IN2)"),
                            (axes[1], chip1, "chip1 (IN3/IN4)")):
        samples = load_s16(path)
        volts = [s * ADC_VOLTS_PER_COUNT for s in samples]
        ax.plot(volts, linewidth=0.4)
        ax.set_title("%s - %d samples" % (label, len(samples)))
        ax.set