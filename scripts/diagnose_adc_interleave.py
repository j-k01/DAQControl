#!/usr/bin/env python3
"""Capture full-rate ADC samples and quantify deterministic mod-4 offsets.

The ADS54J60 uses four time-interleaved cores per channel.  This diagnostic
keeps the raw sample order and reports each sample phase independently, plus
the residual after removing only the four phase means.  It is intentionally a
read-only capture tool: DAC routing and ADC SPI state are not changed.
"""

from __future__ import annotations

import argparse
import time
from typing import Optional

import numpy as np
import serial


SYNC = b"\xFE\x10\xCA\xFE"
VOLTS_PER_COUNT = 1.9 / 65536.0


def capture(port_name: str, baud: int, frames: int, timeout: float) -> list[np.ndarray]:
    need = frames * 8 * 4
    data = bytearray()
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
        while len(data) < need:
            chunk = port.read(need - len(data))
            if not chunk:
                break
            data += chunk
    if len(data) != need:
        raise RuntimeError(f"short PCAP read: {len(data)}/{need} bytes")

    words = np.frombuffer(data, dtype="<u4").reshape(-1, 8)
    channels: list[np.ndarray] = []
    for channel in range(4):
        word0 = words[:, 2 * channel]
        word1 = words[:, 2 * channel + 1]
        samples = np.empty(frames * 4, dtype=np.int16)
        samples[0::4] = (word0 & 0xFFFF).astype(np.int16)
        samples[1::4] = ((word0 >> 16) & 0xFFFF).astype(np.int16)
        samples[2::4] = (word1 & 0xFFFF).astype(np.int16)
        samples[3::4] = ((word1 >> 16) & 0xFFFF).astype(np.int16)
        channels.append(samples)
    return channels


def summarize(channel: int, raw: np.ndarray, tone_hz: Optional[float] = None,
              sample_rate_hz: float = 1e9) -> None:
    x = raw.astype(np.float64)
    phase_means = np.array([x[phase::4].mean() for phase in range(4)])
    phase_std = np.array([x[phase::4].std() for phase in range(4)])
    centered_offsets = phase_means - phase_means.mean()
    corrected = x.copy()
    for phase in range(4):
        corrected[phase::4] -= phase_means[phase]

    window = np.hanning(x.size)
    spectrum = np.abs(np.fft.rfft((x - x.mean()) * window))
    fs4_bin = x.size // 4
    fs2_bin = x.size // 2
    coherent_gain = window.sum() / 2.0
    fs4_counts = spectrum[fs4_bin] / coherent_gain
    fs2_counts = spectrum[fs2_bin] / coherent_gain

    print(f"ch{channel}: n={x.size} mean={x.mean():+.3f} counts "
          f"rms={x.std():.3f} min={int(x.min())} max={int(x.max())}")
    print("  phase means [counts]: " + " ".join(f"{v:+.3f}" for v in phase_means))
    print("  phase offsets [counts]: " + " ".join(f"{v:+.3f}" for v in centered_offsets))
    print("  phase offsets [mV]:     " +
          " ".join(f"{1000.0 * v * VOLTS_PER_COUNT:+.4f}" for v in centered_offsets))
    print("  phase std [counts]:     " + " ".join(f"{v:.3f}" for v in phase_std))
    print(f"  raw fs/4={fs4_counts:.3f} counts, fs/2={fs2_counts:.3f} counts; "
          f"post-mod4 rms={corrected.std():.3f} counts "
          f"({1000.0 * corrected.std() * VOLTS_PER_COUNT:.4f} mV)")
    if tone_hz is not None:
        index = np.arange(x.size, dtype=np.float64)
        angle = 2.0 * np.pi * tone_hz * index / sample_rate_hz
        design = np.column_stack((np.sin(angle), np.cos(angle), np.ones(x.size)))
        fit = design @ np.linalg.lstsq(design, x, rcond=None)[0]
        residual = x - fit
        signal_rms = float(np.std(fit))
        residual_rms = float(np.std(residual))
        snr = 20.0 * np.log10(signal_rms / residual_rms)
        print(f"  tone fit: signal={signal_rms:.3f} residual={residual_rms:.3f} "
              f"counts, SNR={snr:.2f} dB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--frames", type=int, default=4096, choices=range(1, 4097))
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--tone-mhz", type=float, default=None,
                        help="also fit this full-rate tone and report residual")
    args = parser.parse_args()

    for rep in range(args.reps):
        if args.reps > 1:
            print(f"capture {rep + 1}/{args.reps}")
        for channel, samples in enumerate(
                capture(args.port, args.baud, args.frames, args.timeout)):
            tone_hz = args.tone_mhz * 1e6 if args.tone_mhz is not None else None
            summarize(channel, samples, tone_hz=tone_hz)


if __name__ == "__main__":
    main()
