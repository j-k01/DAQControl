#!/usr/bin/env python3
"""Search simple ADC reconstruction candidates from raw source capture CSV."""

from __future__ import annotations

import argparse
import csv
import itertools
from pathlib import Path

import numpy as np


def signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def bswap16(value: int) -> int:
    value &= 0xFFFF
    return ((value & 0xFF) << 8) | (value >> 8)


def load_sources(path: Path) -> dict[int, list[int]]:
    sources = {0: [], 1: [], 2: [], 3: []}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            source = int(row["source"])
            if source in sources:
                sources[source].append(int(row["word_hex"], 16))
    return sources


def chunks_for_frame(sources: dict[int, list[int]], frame: int, byte_swap: bool) -> list[int]:
    chunks: list[int] = []
    for source in range(4):
        word = sources[source][frame]
        values = [word & 0xFFFF, (word >> 16) & 0xFFFF]
        if byte_swap:
            values = [bswap16(value) for value in values]
        chunks.extend(signed16(value) for value in values)
    return chunks


def build_stream(
    sources: dict[int, list[int]],
    indexes: tuple[int, int, int, int],
    byte_swap: bool,
) -> np.ndarray:
    frames = min(len(words) for words in sources.values())
    out = np.empty(frames * 4, dtype=np.float64)
    for frame in range(frames):
        chunks = chunks_for_frame(sources, frame, byte_swap)
        base = frame * 4
        for i, chunk_index in enumerate(indexes):
            out[base + i] = chunks[chunk_index]
    return out


def score_stream(stream: np.ndarray, target_bin: int) -> tuple[float, float, float, int]:
    x = stream - np.mean(stream)
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    if len(mag):
        mag[0] = 0.0
    total = float(np.sum(mag * mag))
    target_power = float(mag[target_bin] * mag[target_bin]) if target_bin < len(mag) else 0.0
    ratio = target_power / total if total else 0.0
    peak = int(np.argmax(mag))
    rms = float(np.sqrt(np.mean(x * x)))
    return ratio, float(mag[target_bin]), rms, peak


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv")
    parser.add_argument("--target-bin", type=int, required=True)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--restrict-pairs",
        action="store_true",
        help="Only test candidates made from two whole u32 sources.",
    )
    args = parser.parse_args()

    sources = load_sources(Path(args.csv))
    candidates = []

    if args.restrict_pairs:
        index_sets = []
        for a, b in itertools.permutations(range(4), 2):
            base = (2 * a, 2 * a + 1, 2 * b, 2 * b + 1)
            index_sets.extend(itertools.permutations(base, 4))
    else:
        index_sets = itertools.permutations(range(8), 4)

    seen = set()
    for indexes in index_sets:
        if indexes in seen:
            continue
        seen.add(indexes)
        for byte_swap in (False, True):
            stream = build_stream(sources, indexes, byte_swap)
            ratio, target_mag, rms, peak = score_stream(stream, args.target_bin)
            candidates.append((ratio, target_mag, rms, peak, byte_swap, indexes))

    candidates.sort(reverse=True, key=lambda item: (item[0], item[1]))
    for ratio, target_mag, rms, peak, byte_swap, indexes in candidates[:args.top]:
        print(
            "ratio={:.6f} target_mag={:.3e} rms={:.2f} peak={} byte_swap={} indexes={}".format(
                ratio,
                target_mag,
                rms,
                peak,
                int(byte_swap),
                ",".join(str(index) for index in indexes),
            )
        )


if __name__ == "__main__":
    main()
