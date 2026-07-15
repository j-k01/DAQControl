#!/usr/bin/env python3
"""Measure raw PCAPT repetition alignment without Ethernet or display filtering."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from dac_scope_qt import DacControl


def strongest_edge(x: np.ndarray, limit: int) -> int:
    y = x.astype(np.float64)
    return int(np.argmax(np.abs(np.diff(y[:limit]))) + 1)


def shifts_to_reference(stack: np.ndarray, max_lag: int) -> list[int]:
    ref = np.median(stack.astype(np.float64), axis=0)
    ref -= ref.mean()
    shifts = []
    for row in stack:
        sig = row.astype(np.float64) - float(row.mean())
        best_lag = 0
        best_score = float("-inf")
        for lag in range(-max_lag, max_lag + 1):
            if lag < 0:
                score = float(np.dot(sig[-lag:], ref[:lag]))
            elif lag > 0:
                score = float(np.dot(sig[:-lag], ref[lag:]))
            else:
                score = float(np.dot(sig, ref))
            if score > best_score:
                best_score = score
                best_lag = lag
        shifts.append(best_lag)
    return shifts


def spike_positions(x: np.ndarray, refractory: int = 20) -> list[int]:
    y = x.astype(np.float64)
    med = float(np.median(y))
    mad = float(np.median(np.abs(y - med))) + 1.0
    threshold = max(12.0 * 1.4826 * mad, 100.0)
    candidates = np.flatnonzero(np.abs(y - med) >= threshold)
    peaks = []
    for index in candidates:
        if not peaks or index - peaks[-1] >= refractory:
            peaks.append(int(index))
        elif abs(y[index] - med) > abs(y[peaks[-1]] - med):
            peaks[-1] = int(index)
    return peaks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--frames", type=int, default=4096)
    parser.add_argument("--reps", type=int, default=8)
    parser.add_argument("--step-ma", type=float, default=10.0)
    parser.add_argument("--zero-samples", type=int, default=32)
    parser.add_argument("--high-samples", type=int, default=512)
    parser.add_argument("--cps", type=int, default=1)
    parser.add_argument("--profile", default=None,
                        choices=("regular", "bursting", "chattering", "fast"))
    parser.add_argument("--adc1-source", default="spike0",
                        choices=("spike0", "mon0", "current"))
    parser.add_argument("--loopback-dac", type=int, default=1, choices=range(1, 4),
                        help="logical DAC channel connected to the ADC1 loopback")
    parser.add_argument("--max-lag", type=int, default=64)
    parser.add_argument("--out-dir", default="captures")
    args = parser.parse_args()

    control = DacControl(args.port)
    captures = []
    try:
        print("STRM:", control.stop_stream())
        print("routes off:", control.cmd("NSRC all off", ok=("DAC xbar", "ERR")))
        print("DAC0 current:", control.cmd("NSRC 0 current", ok=("DAC xbar", "ERR")))
        print(f"DAC{args.loopback_dac} {args.adc1_source}:",
              control.cmd(f"NSRC {args.loopback_dac} {args.adc1_source}",
                          ok=("DAC xbar", "ERR")))
        if args.profile:
            print("profile:", control.cmd(f"NEUR all {args.profile}",
                                           ok=("OK", "ERR")))
            print("neuron I:", control.cmd("NEUR all i 0", ok=("OK", "ERR")))
            print("neuron iconst:", control.cmd("NEUR all iconst 0",
                                                 ok=("OK", "ERR")))
            print("neuron period:", control.cmd("NEUR all period 1",
                                                 ok=("OK", "ERR")))
            print("neuron dt:", control.cmd("NEUR all dt 0x8000",
                                             ok=("OK", "ERR")))
            print("neuron reset:", control.cmd("NEUR all reset", ok=("OK", "ERR")))
            print("pulse:", control.pulse_default())
        reply = control.program_current_step(
            args.cps, args.zero_samples, args.high_samples,
            args.step_ma, hold_last=True)
        print("current:", reply)
        if not reply.startswith("OK CURS"):
            raise RuntimeError("current program failed")
        for rep in range(args.reps):
            capture = control.uart_capture_triggered(args.frames)
            if capture is None:
                raise RuntimeError(f"PCAPT failed at repetition {rep}")
            captures.append(capture)
            print(f"captured {rep + 1}/{args.reps}")
    finally:
        control.cmd("NSRC all off", ok=("DAC xbar", "ERR"))
        control.close()

    stacks = {ch: np.stack([capture[ch] for capture in captures])
              for ch in range(4)}
    expected = args.zero_samples * args.cps * 20
    limit = min(stacks[0].shape[1], max(2048, expected + 1024))
    edges = [strongest_edge(row, limit) for row in stacks[0]]
    shifts = shifts_to_reference(stacks[0], args.max_lag)
    print(f"current expected edge (before analog/capture latency): {expected} ns")
    print("current edge samples:", edges)
    print(f"current edge spread: {min(edges)}..{max(edges)} "
          f"({max(edges) - min(edges)} samples)")
    print("raw correlation lags:", shifts)
    for rep, row in enumerate(stacks[1]):
        peaks = spike_positions(row)
        print(f"ADC1 rep {rep:02d} spike candidates: {peaks[:20]}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"trigger_alignment_{time.strftime('%Y%m%d_%H%M%S')}.npz"
    np.savez_compressed(path, edges=np.asarray(edges), shifts=np.asarray(shifts),
                        **{f"raw_ch{ch}": stack for ch, stack in stacks.items()})
    print("saved", path.resolve())


if __name__ == "__main__":
    main()
