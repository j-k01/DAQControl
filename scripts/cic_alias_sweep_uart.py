#!/usr/bin/env python3
"""A/B test the chip-1 CIC anti-alias decimator vs chip-0 keep-1-of-D.

The new gateware gives chip 1 (ADC ch2/ch3) a runtime choice between the old
keep-1-of-D decimator and a CIC anti-alias decimator (boxcar-4 -> 3-stage CIC,
fixed D=128). Chip 0 (ch0/ch1) is always keep-1-of-D. With `STRM 128 cic` both
chips run at the SAME 7.8125 MS/s output rate (Nyquist 3.906 MHz), so feeding
the identical tone to all four channels is a clean controlled A/B:

  - tone below 3.906 MHz  : both paths reproduce it (control case)
  - tone above 3.906 MHz  : keep-1-of-D ALIASES it into the band at full
                            amplitude; the CIC attenuates it first, so its
                            aliased residual is much smaller -> the dB gap is
                            the anti-alias rejection.

We use BRAM (not DDS) because BRAM is per-channel: the exact same integer-cycle
sine is loaded on all four channels, so ch0/ch1 (keep) and ch2/ch3 (CIC) see an
identical input. For each test frequency we capture one window of the live
stream, take the spectrum of each channel, and report the in-band peak. The
rejection is peak(keep) - peak(CIC) in dB at frequencies above Nyquist.

Prereqs: board programmed with the CIC gateware + A53 PS-eth app running; UART
on COM10; NIC at 192.168.2.1/24. Writes captures/cic_alias_sweep.png.

  python scripts/cic_alias_sweep_uart.py
  python scripts/cic_alias_sweep_uart.py --freqs 1.0,2.5,5.0,7.8125,11.0
"""
from __future__ import annotations

import argparse
import math
import os
import socket
import struct
import threading
import time

import serial
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HDR = struct.Struct("<IHHIIIIII")
MAGIC = 0x53514144
PROGRAM_SAMPLES = 16384
SAMPLE_RATE_HZ = 1.0e9          # raw ADC/DAC sample rate
DEFAULT_DECIM = 128             # CIC fixed factor; run keep at the same D


def clamp_s16(v):
    return max(-32768, min(32767, int(round(v))))


def pack_pair(s0, s1):
    return ((s1 & 0xFFFF) << 16) | (s0 & 0xFFFF)


def sine_words(freq_mhz, amplitude):
    """Integer-cycle sine over the 16384-sample loop (seamless wrap)."""
    cycles = max(1, round(freq_mhz * PROGRAM_SAMPLES / 1000.0))  # 1 GS/s
    ph = 2.0 * math.pi * cycles / PROGRAM_SAMPLES
    s = [clamp_s16(amplitude * math.sin(ph * i)) for i in range(PROGRAM_SAMPLES)]
    actual = cycles * 1000.0 / PROGRAM_SAMPLES                   # MHz, snapped
    words = [pack_pair(s[2 * i], s[2 * i + 1]) for i in range(PROGRAM_SAMPLES // 2)]
    return words, actual


class DacControl:
    def __init__(self, port, baud=115200):
        self.s = serial.Serial(port, baud, timeout=2, write_timeout=3)
        self.lock = threading.Lock()
        time.sleep(0.2)

    def _readuntil(self, prefixes):
        deadline = time.time() + 4
        while time.time() < deadline:
            line = self.s.readline().decode("ascii", errors="replace").strip()
            if line.startswith(tuple(prefixes)):
                return line
        return ""

    def cmd(self, c, ok=("OK", "DAC source", "STRM")):
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write((c + "\n").encode("ascii"))
            self.s.flush()
            return self._readuntil(ok)

    def prog(self, ch, words):
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write(f"PROG {ch} {len(words)}\n".encode("ascii"))
            self.s.flush()
            self._readuntil(("PGRD",))
            self.s.write(struct.pack(f"<{len(words)}I", *words))
            self.s.flush()
            self._readuntil((f"OK PROG ch={ch}",))

    def close(self):
        try:
            self.cmd("STRM STOP", ok=("OK STRM", "ERR"))
        except Exception:  # noqa: BLE001
            pass
        self.s.close()


class StreamTap:
    """Collect a fixed number of samples per channel from the live stream."""

    def __init__(self, board_ip, cmd_port, local_ip, local_port):
        self.lock = threading.Lock()
        self.bufs = {i: [] for i in range(4)}
        self.collecting = False
        self.target = 0
        self.decim = DEFAULT_DECIM
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)
        self.sock.bind((local_ip, local_port))
        self.sock.settimeout(1.0)
        self.board = (board_ip, cmd_port)
        self.sock.sendto(b"STRM", self.board)
        self.expected = {0: None, 1: None}
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < HDR.size:
                continue
            magic, _v, hdr, seq, chip, _o, count, _d, dec = HDR.unpack_from(data)
            if magic != MAGIC or chip > 1:
                continue
            exp = self.expected[chip]
            if exp is not None and seq < exp:
                continue
            self.expected[chip] = seq + 1
            self.decim = dec
            payload = data[hdr:hdr + count]
            payload = payload[: len(payload) - (len(payload) % 16)]
            sm = np.frombuffer(payload, dtype="<i2").reshape(-1, 8)
            base = chip * 2
            with self.lock:
                if not self.collecting:
                    continue
                for ch, col in ((base, sm[:, :4].ravel()),
                                (base + 1, sm[:, 4:].ravel())):
                    if len(self.bufs[ch]) < self.target:
                        self.bufs[ch].append(col)

    def capture(self, nsamp, timeout=4.0):
        with self.lock:
            self.bufs = {i: [] for i in range(4)}
            self.target = max(1, nsamp // 4 + 1)
            self.collecting = True
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                done = all(len(self.bufs[c]) >= self.target for c in range(4))
            if done:
                break
            time.sleep(0.02)
        with self.lock:
            self.collecting = False
            out = {}
            for c in range(4):
                arr = np.concatenate(self.bufs[c]) if self.bufs[c] else np.zeros(1)
                out[c] = arr[:nsamp].astype(np.float64)
            return out

    def close(self):
        self.running = False
        try:
            self.sock.sendto(b"STOP", self.board)
        except OSError:
            pass
        self.sock.close()


def spectrum(x, fs_hz):
    x = x - x.mean()
    n = len(x)
    if n < 8:
        return np.array([0.0]), np.array([-200.0])
    w = np.hanning(n)
    X = np.fft.rfft(x * w)
    mag = np.abs(X) / (np.sum(w) / 2.0)
    freqs = np.fft.rfftfreq(n, 1.0 / fs_hz)
    db = 20.0 * np.log10(np.maximum(mag, 1e-9))
    return freqs, db


def peak_in_band(freqs, db, fmin_hz=20e3):
    sel = freqs >= fmin_hz
    if not np.any(sel):
        return 0.0, -200.0
    i = np.argmax(db[sel])
    fsel = freqs[sel]
    return fsel[i], db[sel][i]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--local-ip", default="192.168.2.1")
    ap.add_argument("--local-port", type=int, default=5005)
    ap.add_argument("--amplitude", type=lambda x: int(x, 0), default=0x5000)
    ap.add_argument("--nsamp", type=int, default=8192)
    # Default sweep: two control tones below Nyquist (3.906 MHz), then several
    # above it where keep-1-of-D aliases and the CIC should reject.
    ap.add_argument("--freqs", default="1.0,3.0,5.0,7.8125,11.0,15.625")
    ap.add_argument("--out", default="captures/cic_alias_sweep.png")
    args = ap.parse_args()

    test_freqs = [float(x) for x in args.freqs.split(",")]
    fs_out = SAMPLE_RATE_HZ / DEFAULT_DECIM
    nyq = fs_out / 2.0

    dac = DacControl(args.port)
    print("Configuring DAC + stream (STRM 128 cic)...")
    dac.cmd("WRTE 2 0x01000018")
    dac.cmd(f"STRM {DEFAULT_DECIM} cic", ok=("OK STRM", "ERR"))
    tap = StreamTap(args.board_ip, args.cmd_port, args.local_ip, args.local_port)
    time.sleep(0.5)

    rows = []
    spectra = []
    for f in test_freqs:
        words, actual = sine_words(f, args.amplitude)
        for ch in range(4):
            dac.prog(ch, words)
        dac.cmd("NSRC all bram", ok=("DAC source", "ERR"))
        time.sleep(0.3)
        caps = tap.capture(args.nsamp)
        # ch0,ch1 = chip0 keep-1-of-D; ch2,ch3 = chip1 CIC
        keep_specs = [spectrum(caps[c], fs_out) for c in (0, 1)]
        cic_specs = [spectrum(caps[c], fs_out) for c in (2, 3)]
        keep_pk = max(peak_in_band(*s)[1] for s in keep_specs)
        cic_pk = max(peak_in_band(*s)[1] for s in cic_specs)
        fk, _ = peak_in_band(*keep_specs[0])
        above = actual > nyq / 1e6
        rej = keep_pk - cic_pk
        rows.append((actual, above, fk / 1e6, keep_pk, cic_pk, rej))
        spectra.append((actual, keep_specs[0], cic_specs[0]))
        tag = "ALIAS" if above else "pass "
        print(f"  f={actual:8.4f} MHz [{tag}]  keep_peak={keep_pk:7.1f} dB  "
              f"cic_peak={cic_pk:7.1f} dB  rejection={rej:6.1f} dB  "
              f"(keep peak bin {fk/1e6:.3f} MHz)")

    tap.close()
    dac.close()

    # ---- plot: per-frequency keep vs CIC spectra + rejection summary -------
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    n = len(spectra)
    fig, axes = plt.subplots(n, 1, figsize=(11, 2.0 * n + 1), squeeze=False)
    for i, (actual, ks, cs) in enumerate(spectra):
        ax = axes[i][0]
        ax.plot(ks[0] / 1e6, ks[1], lw=0.8, label="ch0 keep-1-of-D")
        ax.plot(cs[0] / 1e6, cs[1], lw=0.8, label="ch2 CIC")
        ax.axvline(nyq / 1e6, color="k", ls=":", lw=0.8)
        ax.set_ylim(-90, 5)
        ax.set_ylabel("dB")
        ax.set_title(f"input {actual:.4f} MHz "
                     f"({'above' if actual > nyq/1e6 else 'below'} Nyquist "
                     f"{nyq/1e6:.3f} MHz)", fontsize=9)
        ax.grid(True, alpha=0.3)
        if i == 0:
            ax.legend(fontsize=8, loc="upper right")
    axes[-1][0].set_xlabel("frequency [MHz]  (dotted = decimated Nyquist)")
    fig.suptitle("CIC anti-alias (chip1 ch2) vs keep-1-of-D (chip0 ch0), "
                 f"D={DEFAULT_DECIM}, fs_out={fs_out/1e6:.3f} MS/s", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    fig.savefig(args.out, dpi=110)
    print(f"\nwrote {args.out}")

    above_rows = [r for r in rows if r[1]]
    if above_rows:
        avg = sum(r[5] for r in above_rows) / len(above_rows)
        print(f"mean alias rejection above Nyquist: {avg:.1f} dB "
              f"({len(above_rows)} tones)")


if __name__ == "__main__":
    main()
