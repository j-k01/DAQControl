#!/usr/bin/env python3
"""UART PCAP comparator for DAC source switching."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import serial

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import capture_plot_adc_uart as cap  # noqa: E402
import ethernet_burst_switch_check as ethchk  # noqa: E402


def fingerprint(x: np.ndarray, fs_hz: float = 1.0e9) -> dict:
    x = np.asarray(x, dtype=np.float64)
    n = min(len(x), 65536)
    y = x[:n] - np.mean(x[:n])
    nfft = 1 << int(np.floor(np.log2(max(2, n))))
    w = np.hanning(nfft)
    mag = np.abs(np.fft.rfft(y[:nfft] * w))
    k = int(np.argmax(mag[1:]) + 1) if mag.size > 1 else 0
    return {
        "min": int(np.min(x)),
        "max": int(np.max(x)),
        "rms": float(np.sqrt(np.mean(y * y))),
        "dom_mhz": float(k * fs_hz / nfft / 1.0e6),
    }


def capture_uart(port: serial.Serial, frames: int) -> dict[int, np.ndarray]:
    _presync, frame_words = cap.capture_frames(port, "PCAP", frames)
    captures = cap.split_frame_captures(frame_words)
    streams = cap.build_converter_streams(captures)
    return {ch: streams[f"adc_ch{ch}"] for ch in range(4)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--frames", type=int, default=4096)
    ap.add_argument("--settle", type=float, default=0.2)
    args = ap.parse_args()

    with serial.Serial(args.port, args.baud, timeout=5, write_timeout=5) as ser:
        time.sleep(0.2)

        def cmd_checked(cmd: str) -> str:
            reply = ethchk.uart_cmd(ser, cmd, ("OK", "DAC xbar", "ERR"), timeout=5.0)
            if reply.startswith("ERR") or not reply:
                raise RuntimeError(f"{cmd} failed: {reply or '(no reply)'}")
            return reply

        cases = [
            ("dds_a1", lambda: cmd_checked("NSRC all dds")),
            ("dds_a2", lambda: None),
            ("bram_sine1", lambda: (ethchk.program_bram_set(ser, 0),
                                     cmd_checked("NSRC all bram"))),
            ("bram_sine2", lambda: None),
            ("dds_b1", lambda: cmd_checked("NSRC all dds")),
            ("dds_b2", lambda: None),
            ("bram_square1", lambda: (ethchk.program_bram_set(ser, 1),
                                       cmd_checked("NSRC all bram"))),
            ("bram_square2", lambda: None),
        ]

        for label, setup in cases:
            print(f"\n== {label} ==")
            setup()
            time.sleep(args.settle)
            chans = capture_uart(ser, args.frames)
            for ch in range(4):
                f = fingerprint(chans[ch])
                print(
                    f"  ch{ch}: min={f['min']:6d} max={f['max']:6d} "
                    f"rms={f['rms']:8.1f} dom={f['dom_mhz']:8.3f} MHz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
