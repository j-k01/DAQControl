#!/usr/bin/env python3
"""Program four DISTINCT clean sine tones, one per DAC channel, via natural
per-channel BRAM playback (no software byte-preimage -- the HDL source mux
does the JESD lane mapping, same path the DDS uses).

Frequencies are forced onto the BRAM loop grid (integer cycles per loop) so
the spectrum is clean, and default to sub-2 MHz so they also survive the
decimated stream without aliasing.

  python scripts/program_four_distinct_sines.py            # 0.305/0.61/0.915/1.22 MHz
  python scripts/program_four_distinct_sines.py --transform bswap   # if a channel is byte-swapped
"""
from __future__ import annotations

import argparse
import math
import struct
import time

import serial

WORDS = 8192            # u32 words per channel -> 16384 samples, 16.384 us loop
SAMPLES = WORDS * 2
FS_MHZ = 1000.0


def clamp_s16(v):
    return max(-32768, min(32767, int(round(v))))


def grid_freq(approx_mhz):
    """Nearest integer-cycles-per-loop frequency to approx_mhz."""
    k = max(1, round(approx_mhz * SAMPLES / FS_MHZ))
    return k * FS_MHZ / SAMPLES, k


def sine_samples(k, amp):
    return [clamp_s16(amp * math.sin(2.0 * math.pi * k * i / SAMPLES))
            for i in range(SAMPLES)]


def apply_transform(s, mode):
    if mode == "bswap":
        return [((v & 0x00FF) << 8) | ((v & 0xFF00) >> 8) for v in
                [x & 0xFFFF for x in s]]
    if mode == "rev4":
        out = list(s)
        for i in range(0, len(out), 4):
            out[i:i + 4] = out[i + 3:i - 1 if i else None:-1] if i else out[3::-1]
        # simpler explicit reversal in groups of 4
        out = list(s)
        for i in range(0, len(out) - 3, 4):
            out[i], out[i + 1], out[i + 2], out[i + 3] = \
                out[i + 3], out[i + 2], out[i + 1], out[i]
        return out
    return s


def pack_u32(samples):
    return [((samples[2 * i + 1] & 0xFFFF) << 16) | (samples[2 * i] & 0xFFFF)
            for i in range(len(samples) // 2)]


def wait_prefix(port, prefix, echo=False):
    while True:
        line = port.readline().decode("ascii", errors="replace").strip()
        if echo and line:
            print(line)
        if line.startswith(prefix):
            return line
        if line.startswith("ERR"):
            raise RuntimeError(line)


def send_wait(port, cmd, prefix="OK", echo=False):
    port.write((cmd + "\n").encode("ascii"))
    port.flush()
    return wait_prefix(port, prefix, echo)


def dpwr(port, ch, words):
    port.write(f"DPWR {ch} 0 {len(words)}\n".encode("ascii"))
    port.flush()
    wait_prefix(port, f"DPWR ch={ch}")
    port.write(struct.pack(f"<{len(words)}I", *words))
    port.flush()
    wait_prefix(port, f"OK DPWR ch={ch}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--freqs", default="0.305,0.61,0.915,1.22",
                    help="approx MHz per channel ch0,ch1,ch2,ch3")
    ap.add_argument("--amp", type=lambda x: int(x, 0), default=0x5000)
    ap.add_argument("--rw2", type=lambda x: int(x, 0), default=0x01000018)
    ap.add_argument("--transform", choices=["none", "bswap", "rev4"],
                    default="none")
    args = ap.parse_args()

    approx = [float(x) for x in args.freqs.split(",")]
    assert len(approx) == 4, "need four comma-separated frequencies"

    with serial.Serial(args.port, args.baud, timeout=4.0, write_timeout=4.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        send_wait(port, f"WRTE 2 0x{args.rw2:08X}")
        for ch, a in enumerate(approx):
            f, k = grid_freq(a)
            s = apply_transform(sine_samples(k, args.amp), args.transform)
            dpwr(port, ch, pack_u32(s))
            print(f"ch{ch}: {f:.4f} MHz ({k} cycles/loop), transform={args.transform}")
        send_wait(port, "NSRC all bram", prefix="DAC source")
        # restart all BRAM loops (full loop = loop_frames 0): pulse restart bit
        send_wait(port, "WRTE 3 0x00000068")
        send_wait(port, "WRTE 3 0x00000060")
        print("OK: four distinct tones playing from BRAM")


if __name__ == "__main__":
    main()
