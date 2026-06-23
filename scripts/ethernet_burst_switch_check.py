#!/usr/bin/env python3
"""Exercise Ethernet burst capture while switching DAC sources.

This is a hardware diagnostic for stale or crossed-up Ethernet readout:

  1. Route all DACs to DDS and collect a BCAP/BRDO Ethernet burst.
  2. Program all four DAC BRAMs with distinct waveforms, route all DACs to BRAM,
     and collect another burst.
  3. Repeat DDS/BRAM transitions with a second BRAM pattern set.

Each capture is saved to an NPZ file and summarized with simple per-channel
fingerprints. Consecutive captures that remain nearly identical across a source
change are flagged as possible stale DDR/UDP readout.
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import time
from pathlib import Path

import numpy as np
import serial

from burst_capture import Reassembler, decode_chip, parse_brdo_request, uart_cmd


VOLTS_PER_COUNT = 1.9 / 65536.0
DAC_FULLSCALE = 32767
PROGRAM_SAMPLES = 4096
BRAM_FRAME_SAMPLES = 4
ADC_FS_HZ = 1.0e9


def clamp_s16(v: float) -> int:
    return max(-DAC_FULLSCALE, min(DAC_FULLSCALE, int(round(v))))


def volts_to_counts(v: float) -> int:
    return clamp_s16(v / VOLTS_PER_COUNT)


def pack_pair(s0: int, s1: int) -> int:
    return ((s1 & 0xFFFF) << 16) | (s0 & 0xFFFF)


def read_uart_until(ser: serial.Serial, prefixes: tuple[str, ...],
                    timeout: float = 10.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode("ascii", errors="replace").strip()
        if line.startswith(prefixes):
            return line
    return ""


def make_wave(ch: int, variant: int) -> list[int]:
    """Return packed DAC BRAM words for one channel.

    All variants use the same 4096-sample loop length so the global RW3 loop
    frame count is valid for every DAC BRAM at once.
    """
    i = np.arange(PROGRAM_SAMPLES, dtype=np.float64)
    period = [64, 128, 256, 512][ch]
    phase = (i % period) / period
    amp = [0.18, 0.26, 0.34, 0.42][ch]

    if variant == 0:
        shape = np.sin(2.0 * np.pi * phase)
    elif variant == 1:
        shape = np.where(phase < 0.5, 1.0, -1.0)
    else:
        shape = 2.0 * np.abs(2.0 * phase - 1.0) - 1.0

    samples = np.asarray([volts_to_counts(float(amp * x)) for x in shape],
                         dtype=np.int32)
    return [pack_pair(int(samples[2 * k]), int(samples[2 * k + 1]))
            for k in range(PROGRAM_SAMPLES // 2)]


def program_bram_channel(ser: serial.Serial, ch: int, words: list[int]) -> str:
    ser.reset_input_buffer()
    ser.write(f"PROG {ch} {len(words)}\n".encode("ascii"))
    ser.flush()
    ack = ""
    deadline = time.time() + 5.0
    while time.time() < deadline:
        line = ser.readline().decode("ascii", errors="replace").strip()
        if line.startswith(("PGRD", "ERR")):
            ack = line
            break
    if not ack.startswith("PGRD"):
        return ack or "ERR no PGRD reply"
    ser.write(struct.pack(f"<{len(words)}I", *words))
    ser.flush()
    return read_uart_until(ser, (f"OK PROG ch={ch}", "ERR"), timeout=10.0)


def program_bram_set(ser: serial.Serial, variant: int) -> None:
    for ch in range(4):
        reply = program_bram_channel(ser, ch, make_wave(ch, variant))
        if not reply.startswith("OK PROG"):
            raise RuntimeError(f"PROG ch{ch} failed: {reply or '(no reply)'}")

    loop_frames = PROGRAM_SAMPLES // BRAM_FRAME_SAMPLES
    rw3 = ((loop_frames & 0xFFFFFF) << 8) | 0x60
    reply = uart_cmd(ser, f"WRTE 3 0x{rw3:08X}", ("OK", "ERR"), timeout=4.0)
    if reply.startswith("ERR") or not reply:
        raise RuntimeError(f"WRTE loop frame count failed: {reply or '(no reply)'}")


def collect_burst(args, ser: serial.Serial, label: str) -> dict:
    bpc = args.kb * 1024
    asm = Reassembler(args.board_ip, args.cmd_port, args.local_ip,
                      args.local_port, bpc, rcvbuf=args.rcvbuf)
    try:
        if args.wait_brst_ready:
            if not asm.register(timeout=2.0):
                raise RuntimeError("BRST registration timed out")
        else:
            asm.sock.sendto(b"BRST", asm.board)
            time.sleep(args.brst_settle)

        kb = bpc // 1024
        bcap = uart_cmd(ser, f"BCAP {kb}k", ("OK BCAP", "ERR"), timeout=30.0)
        if not bcap.startswith("OK BCAP"):
            raise RuntimeError(f"BCAP failed: {bcap or '(no reply)'}")

        brdo = uart_cmd(ser, "BRDO", ("OK BRDO", "ERR"), timeout=10.0)
        req = parse_brdo_request(brdo)
        if not brdo.startswith("OK BRDO") or req is None:
            raise RuntimeError(f"BRDO failed: {brdo or '(no request id)'}")
        asm.set_request_id(req)

        deadline = time.time() + max(args.timeout, (2.0 * bpc / 70.0e6) + 2.0)
        while time.time() < deadline and not asm.complete():
            time.sleep(0.05)
        if not asm.complete():
            raise RuntimeError(
                f"UDP drain timeout req={req} "
                f"chip0={100 * asm.coverage(0):.1f}% "
                f"chip1={100 * asm.coverage(1):.1f}%")

        chans = {}
        chans.update(decode_chip(asm.buf[0], 0))
        chans.update(decode_chip(asm.buf[1], 2))
        chans["_cov"] = min(asm.coverage(0), asm.coverage(1))
        chans["_request"] = req
        chans["_label"] = label
        return chans
    finally:
        asm.close()


def channel_fingerprint(x: np.ndarray) -> dict:
    x = x.astype(np.float64)
    n = min(len(x), 65536)
    y = x[:n] - np.mean(x[:n])
    if n >= 1024:
        nfft = 1 << int(np.floor(np.log2(n)))
        w = np.hanning(nfft)
        mag = np.abs(np.fft.rfft(y[:nfft] * w))
        if mag.size > 1:
            k = int(np.argmax(mag[1:]) + 1)
            freq_mhz = (k * ADC_FS_HZ / nfft) / 1.0e6
        else:
            freq_mhz = 0.0
    else:
        freq_mhz = 0.0
    return {
        "min": int(np.min(x)),
        "max": int(np.max(x)),
        "mean": float(np.mean(x)),
        "rms": float(np.sqrt(np.mean(y * y))),
        "dom_mhz": float(freq_mhz),
    }


def corrcoef(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b), 65536)
    if n < 2:
        return 0.0
    x = a[:n].astype(np.float64)
    y = b[:n].astype(np.float64)
    x -= x.mean()
    y -= y.mean()
    den = np.sqrt(np.sum(x * x) * np.sum(y * y))
    return 0.0 if den == 0.0 else float(np.sum(x * y) / den)


def save_capture(outdir: Path, label: str, chans: dict) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{label}.npz"
    np.savez_compressed(
        path,
        ch0=chans[0], ch1=chans[1], ch2=chans[2], ch3=chans[3],
        coverage=np.float64(chans.get("_cov", 0.0)),
        request=np.uint32(chans.get("_request", 0)),
        label=np.asarray(label),
    )
    return path


def run_case(args, ser: serial.Serial, label: str, setup) -> dict:
    print(f"\n== {label} ==")
    setup()
    time.sleep(args.settle)
    chans = collect_burst(args, ser, label)
    if args.no_save:
        print(f"captured req={chans['_request']}  coverage={100 * chans['_cov']:.1f}%")
    else:
        path = save_capture(args.outdir, label, chans)
        print(f"saved {path}  req={chans['_request']}  coverage={100 * chans['_cov']:.1f}%")
    for ch in range(4):
        f = channel_fingerprint(chans[ch])
        print(
            f"  ch{ch}: min={f['min']:6d} max={f['max']:6d} "
            f"rms={f['rms']:8.1f} dom={f['dom_mhz']:8.3f} MHz")
    return chans


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=os.environ.get("DAQ_PORT", "COM10"))
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--local-ip", default="192.168.2.1")
    ap.add_argument("--local-port", type=int, default=5005)
    ap.add_argument("--kb", type=int, default=64, help="KB per ADC chip per burst")
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--settle", type=float, default=0.2)
    ap.add_argument("--brst-settle", type=float, default=0.3)
    ap.add_argument("--wait-brst-ready", action="store_true",
                    help="Require BRST_READY before issuing BCAP/BRDO.")
    ap.add_argument("--rcvbuf", type=int, default=256 << 20)
    ap.add_argument("--outdir", type=Path,
                    default=Path("captures") / time.strftime("eth_switch_%Y%m%d_%H%M%S"))
    ap.add_argument("--no-save", action="store_true",
                    help="Analyze captures in memory without writing NPZ files.")
    args = ap.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=5, write_timeout=5)
    time.sleep(0.2)
    previous = None
    failures = 0

    def cmd_checked(cmd: str) -> str:
        reply = uart_cmd(ser, cmd, ("OK", "DAC xbar", "ERR"), timeout=5.0)
        if reply.startswith("ERR") or not reply:
            raise RuntimeError(f"{cmd} failed: {reply or '(no reply)'}")
        return reply

    cases = [
        ("dds_a1", lambda: cmd_checked("NSRC all dds")),
        ("dds_a2", lambda: None),
        ("bram_sine1", lambda: (program_bram_set(ser, 0), cmd_checked("NSRC all bram"))),
        ("bram_sine2", lambda: None),
        ("dds_b1", lambda: cmd_checked("NSRC all dds")),
        ("dds_b2", lambda: None),
        ("bram_square1", lambda: (program_bram_set(ser, 1), cmd_checked("NSRC all bram"))),
        ("bram_square2", lambda: None),
        ("bram_triangle1", lambda: (program_bram_set(ser, 2), cmd_checked("NSRC all bram"))),
        ("bram_triangle2", lambda: None),
    ]

    try:
        cmd_checked("STRM STOP")
        for label, setup in cases:
            chans = run_case(args, ser, label, setup)
            if previous is not None:
                corrs = [corrcoef(previous[ch], chans[ch]) for ch in range(4)]
                print("  corr vs previous:", " ".join(f"ch{ch}={corrs[ch]:+.4f}"
                                                       for ch in range(4)))
                if all(abs(c) > 0.995 for c in corrs):
                    print("  WARN: all channels nearly identical to previous capture")
                    failures += 1
            previous = chans
    finally:
        ser.close()

    print(f"\nsummary: {failures} stale-capture warnings")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
