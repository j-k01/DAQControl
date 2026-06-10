#!/usr/bin/env python3
"""Program a trapezoidal pulse train into DAC0 BRAM over the PL UART.

Default waveform (the 35 ns spike-train bring-up case):
  - 35 ns period at 1 GSPS  -> 35 samples/period
  - 7 ns trapezoidal pulse  -> 7 samples: 2 ns ramp up, 3 ns flat top,
                               2 ns ramp down (the hardware IZH spike profile)
  - 28 ns at baseline before the next pulse

The DAC program BRAM stores 32-bit little-endian words, two chronological
signed 16-bit samples per word. The program length is chosen as the largest
even u32 word count (<= 8192) whose sample count is an exact multiple of the
period, so the pulse train tiles seamlessly when the BRAM program loops.
For the 35 ns default: 8190 words = 16380 samples = 468 whole periods.
"""

from __future__ import annotations

import argparse
import re
import struct
import time
from typing import Iterable

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "pyserial is required to open the UART. Run this from a Python "
        "environment that has pyserial installed."
    ) from exc


MAX_PROGRAM_WORDS = 8192
DAC_NORMAL_RW2 = 0x01000018  # sample_map=0, tx_lane=3; source selection is NSRC.

# 7-sample trapezoid used by the hardware IZH spike pulse shaper at 1 GSPS.
IZH_7NS_PROFILE = (0.25, 0.5, 1.0, 1.0, 1.0, 0.5, 0.25)


def parse_int(text: str) -> int:
    return int(text, 0)


def clamp_s16(value: float) -> int:
    rounded = int(round(value))
    return max(-32768, min(32767, rounded))


def pack_pair(sample0: int, sample1: int) -> int:
    return (sample0 & 0xFFFF) | ((sample1 & 0xFFFF) << 16)


def ns_to_samples(label: str, duration_ns: float, sample_rate_hz: float) -> int:
    exact = duration_ns * sample_rate_hz / 1.0e9
    samples = int(round(exact))
    if samples < 1 or abs(exact - samples) > 1e-6:
        raise SystemExit(
            f"{label} of {duration_ns:g} ns is not a whole number of samples "
            f"at {sample_rate_hz / 1e6:g} MS/s (= {exact:g} samples)"
        )
    return samples


def pulse_profile(width_samples: int) -> tuple[float, ...]:
    if width_samples == 7:
        return IZH_7NS_PROFILE
    # Generic trapezoid: ~25% ramp up, 50% flat top, ~25% ramp down,
    # matching the "trapulse" shape in program_dac0_waveform_uart.py.
    profile = []
    midpoint = max(1, width_samples - 1)
    for i in range(width_samples):
        normalized = i / midpoint
        if normalized < 0.25:
            profile.append(normalized / 0.25)
        elif normalized < 0.75:
            profile.append(1.0)
        else:
            profile.append((1.0 - normalized) / 0.25)
    return tuple(profile)


def make_period(period_samples: int, width_samples: int, amplitude: int, offset: int) -> list[int]:
    profile = pulse_profile(width_samples)
    samples = [clamp_s16(offset + amplitude * p) for p in profile]
    samples.extend(clamp_s16(offset) for _ in range(period_samples - width_samples))
    return samples


def seamless_word_count(period_samples: int, max_words: int) -> int:
    """Largest even u32 word count whose sample count tiles whole periods."""
    max_samples = 2 * max_words
    for periods in range(max_samples // period_samples, 0, -1):
        total = periods * period_samples
        if total % 2 == 0:
            return total // 2
    raise SystemExit(f"period of {period_samples} samples cannot fit in {max_words} words")


def read_line(port: serial.Serial) -> str:
    line = port.readline()
    if not line:
        raise TimeoutError("timed out waiting for UART line")
    return line.decode("ascii", errors="replace").strip()


def wait_prefix(port: serial.Serial, prefix: str) -> str:
    while True:
        line = read_line(port)
        if line.startswith(prefix):
            return line


def send_wait(port: serial.Serial, command: str, prefix: str = "OK") -> str:
    port.write((command + "\n").encode("ascii"))
    port.flush()
    return wait_prefix(port, prefix)


def upload_dac0_program(port: serial.Serial, words: Iterable[int]) -> int:
    word_list = list(words)
    if not word_list or len(word_list) > MAX_PROGRAM_WORDS:
        raise ValueError(f"program word count must be 1..{MAX_PROGRAM_WORDS}")
    if len(word_list) & 1:
        raise ValueError("program word count must be even so DAC frames are 64-bit aligned")

    port.write(f"PROG 0 {len(word_list)}\n".encode("ascii"))
    port.flush()
    print(wait_prefix(port, "PGRD"))
    port.write(struct.pack(f"<{len(word_list)}I", *word_list))
    port.flush()
    print(wait_prefix(port, "OK PROG"))
    return len(word_list) // 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10", help="PL UART port.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--period-ns", type=float, default=35.0, help="Pulse repetition period.")
    parser.add_argument("--pulse-width-ns", type=float, default=7.0, help="Trapezoid width.")
    parser.add_argument("--sample-rate-mhz", type=float, default=1000.0, help="DAC sample rate.")
    parser.add_argument("--amplitude", type=parse_int, default=0x6000, help="Signed DAC counts.")
    parser.add_argument("--offset", type=parse_int, default=0, help="Baseline in signed DAC counts.")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    sample_rate_hz = args.sample_rate_mhz * 1.0e6
    period_samples = ns_to_samples("period", args.period_ns, sample_rate_hz)
    width_samples = ns_to_samples("pulse width", args.pulse_width_ns, sample_rate_hz)
    if width_samples >= period_samples:
        raise SystemExit(
            f"pulse width ({width_samples} samples) must be shorter than the "
            f"period ({period_samples} samples)"
        )
    gap_ns = (period_samples - width_samples) * 1.0e9 / sample_rate_hz

    word_count = seamless_word_count(period_samples, MAX_PROGRAM_WORDS)
    period = make_period(period_samples, width_samples, args.amplitude, args.offset)
    samples = period * (2 * word_count // period_samples)
    words = [pack_pair(samples[2 * i], samples[2 * i + 1]) for i in range(word_count)]
    frame_count = word_count // 2
    rw3_run = ((frame_count << 8) & 0xFFFFFF00) | 0x60

    print(
        f"DAC0 trapezoid pulse train: period={args.period_ns:g} ns "
        f"({period_samples} samples), pulse={args.pulse_width_ns:g} ns "
        f"({width_samples} samples), gap={gap_ns:g} ns, "
        f"amp={args.amplitude} offset={args.offset}"
    )
    print(
        f"program: {word_count} u32 words = {len(samples)} samples = "
        f"{len(samples) // period_samples} whole periods (seamless BRAM loop)"
    )

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=args.timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        frame_count = upload_dac0_program(port, words)
        print(send_wait(port, "NSRC all dds", "DAC source"))
        print(send_wait(port, "NSRC 0 bram", "DAC source"))
        print(send_wait(port, f"WRTE 2 0x{DAC_NORMAL_RW2:08X}"))
        print(send_wait(port, f"WRTE 3 0x{rw3_run | 0x08:08X}"))
        print(send_wait(port, f"WRTE 3 0x{rw3_run:08X}"))
        port.write(b"STAT\n")
        time.sleep(0.5)
        print(port.read_all().decode("ascii", errors="replace"))


if __name__ == "__main__":
    main()
