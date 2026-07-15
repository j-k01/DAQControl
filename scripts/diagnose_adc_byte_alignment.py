#!/usr/bin/env python3
"""Search ADS54J60 raw JESD lanes for the correct 16-bit byte pairing.

Run with ADC capture format 1 (raw post-link transport lanes) and a known DAC0
tone looped to ADC0.  The search tries lane pairs, the 24 within-word byte
orders, and small relative lane delays.  A correct reconstruction has a much
smaller sine-fit residual than a sample made from unrelated high/low bytes.
"""

from __future__ import annotations

import argparse
import itertools
import math
import time

import numpy as np
import serial


SYNC = b"\xFE\x10\xCA\xFE"


def capture_words(port_name: str, baud: int, frames: int, timeout: float) -> np.ndarray:
    need = frames * 8 * 4
    payload = bytearray()
    with serial.Serial(port_name, baud, timeout=2, write_timeout=timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        port.write(f"PCAP {frames}\n".encode("ascii"))
        port.flush()
        window = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = port.read(1)
            if not value:
                continue
            window += value
            if len(window) > len(SYNC):
                del window[0]
            if bytes(window) == SYNC:
                break
        else:
            raise RuntimeError("PCAP sync timeout")
        while len(payload) < need:
            block = port.read(need - len(payload))
            if not block:
                break
            payload += block
    if len(payload) != need:
        raise RuntimeError(f"short PCAP read: {len(payload)}/{need} bytes")
    return np.frombuffer(payload, dtype="<u4").reshape(-1, 8)


def tone_residual(samples: np.ndarray, frequency_hz: float, sample_rate_hz: float) -> tuple[float, float]:
    y = samples.astype(np.float64)
    y -= y.mean()
    index = np.arange(y.size, dtype=np.float64)
    angle = 2.0 * math.pi * frequency_hz * index / sample_rate_hz
    sin = np.sin(angle)
    cos = np.cos(angle)
    # The capture is long and the tone coherent enough that sin/cos are nearly
    # orthogonal.  Solve the exact two-column normal equations anyway.
    ss = float(sin @ sin)
    cc = float(cos @ cos)
    sc = float(sin @ cos)
    sy = float(sin @ y)
    cy = float(cos @ y)
    det = ss * cc - sc * sc
    a = (sy * cc - cy * sc) / det
    b = (cy * ss - sy * sc) / det
    residual = y - a * sin - b * cos
    signal_rms = math.sqrt((a * a + b * b) / 2.0)
    residual_rms = float(np.sqrt(np.mean(residual * residual)))
    return residual_rms, signal_rms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--frames", type=int, default=2048)
    parser.add_argument("--tone-mhz", type=float, default=100.0)
    parser.add_argument("--sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--chip", type=int, choices=(0, 1), default=0)
    parser.add_argument("--zero", action="store_true",
                        help="rank by reconstructed RMS for an idle/zero input")
    parser.add_argument("--max-shift", type=int, default=8)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    words = capture_words(args.port, args.baud, args.frames, args.timeout)
    # Capture format 1 publishes chip0 lanes as words 0..3.  Chip1's normal
    # frontend output deliberately swaps channels into connector order, so its
    # captured word order is lane2,lane3,lane0,lane1.  Undo that publication
    # swap here so reported lane numbers always mean transport lanes 0..3.
    base = args.chip * 4
    word_order = (0, 1, 2, 3) if args.chip == 0 else (2, 3, 0, 1)
    lane_words = [np.ascontiguousarray(words[:, base + word_order[lane]])
                  for lane in range(4)]
    byte_matrices = [lane.view(np.uint8).reshape(-1, 4) for lane in lane_words]
    results: list[tuple[float, float, float, int, int, tuple[int, ...], int, int]] = []

    for order in itertools.permutations(range(4)):
        streams = [matrix[:, order].ravel() for matrix in byte_matrices]
        for high_lane in range(4):
            for low_lane in range(4):
                if high_lane == low_lane:
                    continue
                high = streams[high_lane]
                low = streams[low_lane]
                for shift in range(-args.max_shift, args.max_shift + 1):
                    if shift < 0:
                        hi = high[-shift:]
                        lo = low[:shift]
                    elif shift > 0:
                        hi = high[:-shift]
                        lo = low[shift:]
                    else:
                        hi = high
                        lo = low
                    unsigned = (hi.astype(np.uint16) << 8) | lo.astype(np.uint16)
                    samples = unsigned.view(np.int16)
                    if args.zero:
                        residual = float(samples.astype(np.float64).std())
                        signal = 1.0
                        score = residual
                    else:
                        residual, signal = tone_residual(
                            samples, args.tone_mhz * 1e6, args.sample_rate_mhz * 1e6)
                        score = residual / max(signal, 1e-12)
                    results.append((score, residual, -signal, high_lane, low_lane,
                                    order, shift, samples.size))

    results.sort()
    print("resid/signal residual_counts signal_counts high low order shift samples")
    for score, residual, neg_signal, high_lane, low_lane, order, shift, count in results[:args.top]:
        print(f"{score:12.6f} {residual:14.4f} {-neg_signal:13.4f} "
              f"{high_lane:4d} {low_lane:3d} "
              f"{','.join(map(str, order)):>7s} {shift:+5d} {count:7d}")

    best_by_pair = {}
    for result in results:
        pair = (result[3], result[4])
        if pair not in best_by_pair:
            best_by_pair[pair] = result
    print("\nbest result for each distinct high/low lane pair")
    print("resid/signal residual_counts signal_counts high low order shift samples")
    for score, residual, neg_signal, high_lane, low_lane, order, shift, count in \
            sorted(best_by_pair.values())[:args.top]:
        print(f"{score:12.6f} {residual:14.4f} {-neg_signal:13.4f} "
              f"{high_lane:4d} {low_lane:3d} "
              f"{','.join(map(str, order)):>7s} {shift:+5d} {count:7d}")

    # Refine the best lane pair with independent high/low within-word orders.
    # This detects a byte-index reversal or rotation on only one physical lane,
    # which the coarse common-order search intentionally cannot represent.
    best_high = results[0][3]
    best_low = results[0][4]
    refined: list[tuple[float, float, float, tuple[int, ...], tuple[int, ...], int, int]] = []
    for high_order in itertools.permutations(range(4)):
        high = byte_matrices[best_high][:, high_order].ravel()
        for low_order in itertools.permutations(range(4)):
            low = byte_matrices[best_low][:, low_order].ravel()
            for shift in range(-args.max_shift, args.max_shift + 1):
                if shift < 0:
                    hi = high[-shift:]
                    lo = low[:shift]
                elif shift > 0:
                    hi = high[:-shift]
                    lo = low[shift:]
                else:
                    hi = high
                    lo = low
                unsigned = (hi.astype(np.uint16) << 8) | lo.astype(np.uint16)
                samples = unsigned.view(np.int16)
                if args.zero:
                    residual = float(samples.astype(np.float64).std())
                    signal = 1.0
                    score = residual
                else:
                    residual, signal = tone_residual(
                        samples, args.tone_mhz * 1e6, args.sample_rate_mhz * 1e6)
                    score = residual / max(signal, 1e-12)
                refined.append((score, residual, -signal, high_order, low_order,
                                shift, samples.size))
    refined.sort()
    print(f"\nrefined lane pair high={best_high} low={best_low}")
    print("resid/signal residual_counts signal_counts high_order low_order shift samples")
    for score, residual, neg_signal, high_order, low_order, shift, count in refined[:args.top]:
        print(f"{score:12.6f} {residual:14.4f} {-neg_signal:13.4f} "
              f"{','.join(map(str, high_order)):>10s} "
              f"{','.join(map(str, low_order)):>9s} {shift:+5d} {count:7d}")


if __name__ == "__main__":
    main()
