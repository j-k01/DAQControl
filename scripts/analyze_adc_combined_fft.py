#!/usr/bin/env python3
"""Analyze reconstructed ADC converter streams from a capture combined CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np


def load_streams(path: Path) -> dict[str, list[int]]:
    streams: dict[str, list[int]] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            streams.setdefault(row["stream"], []).append(int(row["sample_signed"]))
    return streams


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv")
    parser.add_argument("--sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--top", type=int, default=8)
    args = parser.parse_args()

    streams = load_streams(Path(args.csv))
    for name, values in streams.items():
        x = np.asarray(values, dtype=np.float64)
        x_centered = x - np.mean(x)
        mag = np.abs(np.fft.rfft(x_centered * np.hanning(len(x_centered))))
        if len(mag):
            mag[0] = 0.0
        peaks = np.argsort(mag)[-args.top:][::-1]

        print(name)
        print(
            "  samples={} min={} max={} mean={:.2f} rms={:.2f}".format(
                len(values),
                int(np.min(x)),
                int(np.max(x)),
                float(np.mean(x)),
                float(np.sqrt(np.mean(x_centered * x_centered))),
            )
        )
        print("  peaks:")
        for peak in peaks:
            freq_mhz = peak * args.sample_rate_mhz / len(x_centered)
            print("    bin={:5d} freq_mhz={:10.6f} mag={:.3e}".format(
                int(peak),
                freq_mhz,
                float(mag[peak]),
            ))


if __name__ == "__main__":
    main()
