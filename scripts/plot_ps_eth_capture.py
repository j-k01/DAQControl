#!/usr/bin/env python3
"""Plot PS Ethernet ADC captures (ps_eth_capture_chipN.bin).

Each chip buffer is a stream of 16-byte DMA frames: four little-endian u32
words per frame. Following the BRAM capture convention, each logical ADC
channel is built from a (low, high) word pair, two chronological 16-bit
samples per word:

    chip0 frame: [ch0_low, ch0_high, ch1_low, ch1_high]
    chip1 frame: [ch2_low, ch2_high, ch3_low, ch3_high]

Use --raw-sources to plot the four raw word streams per chip instead, for
reverse-engineering the layout if the channel assumption looks wrong.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

WORDS_PER_FRAME = 4


def signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def load_chip_words(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) % 4:
        raise ValueError(f"{path} length {len(data)} is not a multiple of 4")
    return list(struct.unpack(f"<{len(data) // 4}I", data))


def split_sources(words: list[int]) -> dict[int, list[int]]:
    if len(words) % WORDS_PER_FRAME:
        raise ValueError(
            f"word count {len(words)} is not a multiple of {WORDS_PER_FRAME}"
        )
    sources: dict[int, list[int]] = {s: [] for s in range(WORDS_PER_FRAME)}
    for index in range(0, len(words), WORDS_PER_FRAME):
        for source in range(WORDS_PER_FRAME):
            sources[source].append(words[index + source])
    return sources


def combine_channel(low_words: list[int], high_words: list[int]) -> list[int]:
    samples = []
    for low, high in zip(low_words, high_words):
        samples.append(signed16(low & 0xFFFF))
        samples.append(signed16((low >> 16) & 0xFFFF))
        samples.append(signed16(high & 0xFFFF))
        samples.append(signed16((high >> 16) & 0xFFFF))
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indir", default="captures/eth")
    parser.add_argument("--prefix", default="ps_eth_capture")
    parser.add_argument("--outdir", default=None,
                        help="Output directory (default: same as --indir)")
    parser.add_argument("--plot-samples", type=int, default=2048,
                        help="Samples per channel in the time-domain plot")
    parser.add_argument("--sample-rate", type=float, default=1e9,
                        help="ADC sample rate in Hz for FFT axis (1 GS/s per input)")
    parser.add_argument("--raw-sources", action="store_true",
                        help="Plot raw u32 word streams as lo16/hi16 pairs")
    args = parser.parse_args()

    indir = Path(args.indir)
    outdir = Path(args.outdir) if args.outdir else indir
    outdir.mkdir(parents=True, exist_ok=True)

    streams: dict[str, list[int]] = {}
    raw: dict[str, list[int]] = {}
    for chip in (0, 1):
        path = indir / f"{args.prefix}_chip{chip}.bin"
        if not path.exists():
            print(f"chip{chip}: {path} not found, skipping")
            continue
        words = load_chip_words(path)
        sources = split_sources(words)
        base = chip * 2
        streams[f"adc_ch{base}"] = combine_channel(sources[0], sources[1])
        streams[f"adc_ch{base + 1}"] = combine_channel(sources[2], sources[3])
        for source in range(WORDS_PER_FRAME):
            raw[f"chip{chip}_w{source}"] = sources[source]
        print(f"chip{chip}: {len(words) // WORDS_PER_FRAME} frames "
              f"-> {len(streams[f'adc_ch{base}'])} samples/channel")

    if not streams:
        raise SystemExit("no capture files found")

    if args.raw_sources:
        names = sorted(raw)
        fig, axes = plt.subplots(len(names), 1, figsize=(12, 2.2 * len(names)),
                                 sharex=True)
        for ax, name in zip(np.atleast_1d(axes), names):
            lo = [signed16(w & 0xFFFF) for w in raw[name][: args.plot_samples]]
            hi = [signed16((w >> 16) & 0xFFFF) for w in raw[name][: args.plot_samples]]
            ax.plot(lo, lw=0.7, label="lo16")
            ax.plot(hi, lw=0.7, label="hi16")
            ax.set_title(name)
            ax.legend(loc="upper right", fontsize=7)
        png = outdir / f"{args.prefix}_raw_sources.png"
        fig.tight_layout()
        fig.savefig(png, dpi=120)
        print(f"wrote {png}")
        return

    names = sorted(streams)

    # CSV with all channels side by side.
    length = max(len(v) for v in streams.values())
    csv_path = outdir / f"{args.prefix}_channels.csv"
    with csv_path.open("w", encoding="ascii") as fh:
        fh.write("sample," + ",".join(names) + "\n")
        for i in range(length):
            row = [str(i)]
            for name in names:
                vals = streams[name]
                row.append(str(vals[i]) if i < len(vals) else "")
            fh.write(",".join(row) + "\n")
    print(f"wrote {csv_path}")

    # Time-domain plot.
    fig, axes = plt.subplots(len(names), 1, figsize=(12, 2.5 * len(names)),
                             sharex=True)
    for ax, name in zip(np.atleast_1d(axes), names):
        ax.plot(streams[name][: args.plot_samples], lw=0.7)
        ax.set_title(f"{name} (first {args.plot_samples} samples)")
        ax.grid(True, alpha=0.3)
    np.atleast_1d(axes)[-1].set_xlabel("sample index")
    png_path = outdir / f"{args.prefix}_channels.png"
    fig.tight_layout()
    fig.savefig(png_path, dpi=120)
    print(f"wrote {png_path}")

    # FFT per channel.
    fig, axes = plt.subplots(len(names), 1, figsize=(12, 2.5 * len(names)),
                             sharex=True)
    for ax, name in zip(np.atleast_1d(axes), names):
        x = np.asarray(streams[name], dtype=np.float64)
        x = x - x.mean()
        n = len(x)
        if n < 8:
            continue
        win = np.hanning(n)
        spec = np.abs(np.fft.rfft(x * win))
        spec = 20 * np.log10(spec / (spec.max() if spec.max() > 0 else 1) + 1e-12)
        freqs = np.fft.rfftfreq(n, d=1.0 / args.sample_rate)
        ax.plot(freqs / 1e6, spec, lw=0.7)
        ax.set_title(f"{name} FFT")
        ax.set_ylabel("dBc")
        ax.grid(True, alpha=0.3)
    np.atleast_1d(axes)[-1].set_xlabel("frequency (MHz)")
    fft_path = outdir / f"{args.prefix}_fft.png"
    fig.tight_layout()
    fig.savefig(fft_path, dpi=120)
    print(f"wrote {fft_path}")

    # Simple per-channel stats.
    for name in names:
        arr = np.asarray(streams[name], dtype=np.int64)
        print(f"{name}: min={arr.min()} max={arr.max()} mean={arr.mean():.1f} "
              f"pkpk={arr.max() - arr.min()}")


if __name__ == "__main__":
    main()
