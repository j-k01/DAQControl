#!/usr/bin/env python3
"""Interactive real-time ADC scope with per-channel DAC source selection.

Streams the decimated ADC over Ethernet and shows all four channels live.
A radio button per channel switches that DAC's source between DDS / BRAM /
Neuron (via NSRC over UART) so you watch the ADC change in real time.

At startup it readies all three sources so switching is instant:
  - BRAM:   a distinct low sine per channel (PROG), 0.30/0.61/0.92/1.22 MHz
  - DDS:    a single ~0.24 MHz tone (one frequency for all channels, by HW)
  - Neuron: RS/IB/CH/FS Izhikevich profiles, sped up so spikes are visible

All frequencies are below the decimated Nyquist (3.906 MS/s at D=256) so the
stream shows the true waveform, not an alias.

Prereqs: board programmed + A53 PS-eth app running; UART on COM10; NIC at
192.168.2.1/24. See notes/dac_sources_howto.md.

  python scripts/dac_source_scope.py
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import threading
import time

import serial
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import RadioButtons

HDR = struct.Struct("<IHHIIIIII")
MAGIC = 0x53514144
VOLTS_PER_COUNT = 1.9 / 65536.0
PROGRAM_SAMPLES = 16384
NEURON_PROFILES = ["regular", "bursting", "chattering", "fast"]
SOURCE_LABELS = ["DDS", "BRAM", "Neuron"]
LABEL_TO_NSRC = {"DDS": "dds", "BRAM": "bram", "Neuron": "izh"}


# ---------------------------------------------------------------- DAC content
def clamp_s16(v):
    return max(-32768, min(32767, int(round(v))))


def pack_pair(s0, s1):
    return ((s1 & 0xFFFF) << 16) | (s0 & 0xFFFF)


def sine_words(freq_mhz, amplitude):
    cycles = max(1, round(freq_mhz * PROGRAM_SAMPLES / 1000.0))  # 1 GS/s
    ph = 2.0 * math.pi * cycles / PROGRAM_SAMPLES
    s = [clamp_s16(amplitude * math.sin(ph * i)) for i in range(PROGRAM_SAMPLES)]
    return [pack_pair(s[2 * i], s[2 * i + 1]) for i in range(PROGRAM_SAMPLES // 2)]


# ---------------------------------------------------------------- UART control
class DacControl:
    def __init__(self, port, baud=115200):
        self.s = serial.Serial(port, baud, timeout=2, write_timeout=3)
        self.lock = threading.Lock()
        time.sleep(0.2)

    def _readuntil(self, prefixes, echo=False):
        deadline = time.time() + 4
        while time.time() < deadline:
            line = self.s.readline().decode("ascii", errors="replace").strip()
            if echo and line:
                print("   <", line)
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

    def set_source(self, ch, label):
        # NSRC handles source mux + program_enable; never poke RW3.
        return self.cmd(f"NSRC {ch} {LABEL_TO_NSRC[label]}", ok=("DAC source", "ERR"))

    def setup(self, decim, dds_step, amplitude, freqs_mhz):
        print("Readying DAC sources (this takes a few seconds)...")
        self.cmd("WRTE 2 0x01000018")
        # DDS phase-inc lives in RW3[31:8]; this same field is the BRAM frame
        # count, and 0x..00 there => full-loop. Set it once, then only NSRC.
        self.cmd(f"WRTE 3 0x{(dds_step & 0xFFFFFF) << 8:08X}")
        for ch, f in enumerate(freqs_mhz):
            self.prog(ch, sine_words(f, amplitude))
            print(f"   BRAM ch{ch}: {f:.3f} MHz programmed")
        for ch, prof in enumerate(NEURON_PROFILES):
            self.cmd(f"NEUR {ch} {prof}")
        self.cmd("NEUR all period 1")
        self.cmd("NEUR all dt 0x8000")
        self.cmd(f"STRM {decim}", ok=("OK STRM", "ERR"))
        print("Sources ready.")

    def close(self):
        try:
            self.cmd("STRM STOP", ok=("OK STRM", "ERR"))
        except Exception:  # noqa: BLE001
            pass
        self.s.close()


# ---------------------------------------------------------------- UDP stream
class StreamTap:
    def __init__(self, board_ip, cmd_port, local_ip, local_port, window):
        self.window = window
        self.chans = {i: np.zeros(window, dtype=np.int16) for i in range(4)}
        self.expected = {0: None, 1: None}
        self.lock = threading.Lock()
        self.decim = 256
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)
        self.sock.bind((local_ip, local_port))
        self.sock.settimeout(1.0)
        self.board = (board_ip, cmd_port)
        self.sock.sendto(b"STRM", self.board)
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
                continue  # drop reordered, keep buffers continuous
            self.expected[chip] = seq + 1
            self.decim = dec
            payload = data[hdr:hdr + count]
            payload = payload[: len(payload) - (len(payload) % 16)]
            sm = np.frombuffer(payload, dtype="<i2").reshape(-1, 8)
            base = chip * 2
            with self.lock:
                for ch, col in ((base, sm[:, :4].ravel()), (base + 1, sm[:, 4:].ravel())):
                    buf = self.chans[ch]
                    n = len(col)
                    if n >= self.window:
                        buf[:] = col[-self.window:]
                    else:
                        buf[:-n] = buf[n:]
                        buf[-n:] = col

    def snapshot(self):
        with self.lock:
            return {c: b.copy() for c, b in self.chans.items()}

    def close(self):
        self.running = False
        try:
            self.sock.sendto(b"STOP", self.board)
        except OSError:
            pass
        self.sock.close()


# ---------------------------------------------------------------- GUI
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--local-ip", default="192.168.2.1")
    ap.add_argument("--local-port", type=int, default=5005)
    ap.add_argument("--decim", type=int, default=256)
    ap.add_argument("--dds-step", type=lambda x: int(x, 0), default=4096)
    ap.add_argument("--amplitude", type=lambda x: int(x, 0), default=0x5000)
    # Keep tones well below the decimated Nyquist (1.95 MHz at D=256) so the
    # stream has >=12 samples/cycle and renders as smooth sines. Higher tones
    # are real but look jagged at the decimated rate (few samples/cycle); for
    # those, lower the decimation (--decim) or add the anti-alias decimator.
    ap.add_argument("--freqs", default="0.122,0.183,0.244,0.305")
    ap.add_argument("--window", type=int, default=4096)
    ap.add_argument("--time-span", type=int, default=512)
    ap.add_argument("--initial", default="BRAM", choices=SOURCE_LABELS)
    ap.add_argument("--fps", type=float, default=15.0)
    args = ap.parse_args()

    freqs = [float(x) for x in args.freqs.split(",")]
    dac = DacControl(args.port)
    dac.setup(args.decim, args.dds_step, args.amplitude, freqs)
    for ch in range(4):
        dac.set_source(ch, args.initial)
    tap = StreamTap(args.board_ip, args.cmd_port, args.local_ip,
                    args.local_port, args.window)

    fig = plt.figure(figsize=(13, 8))
    fig.canvas.manager.set_window_title("DAC source live scope")
    lines, radios = [], []
    for ch in range(4):
        ax = fig.add_axes([0.06, 0.08 + (3 - ch) * 0.225, 0.66, 0.19])
        (ln,) = ax.plot(np.zeros(args.time_span), lw=0.8)
        ax.set_ylabel(f"ch{ch} [V]")
        ax.set_ylim(-0.95, 0.95)
        ax.grid(True, alpha=0.3)
        if ch < 3:
            ax.set_xticklabels([])
        lines.append(ln)

        rax = fig.add_axes([0.76, 0.08 + (3 - ch) * 0.225, 0.2, 0.19])
        rax.set_title(f"DAC{ch} source", fontsize=9)
        rb = RadioButtons(rax, SOURCE_LABELS,
                          active=SOURCE_LABELS.index(args.initial))

        def make_cb(channel):
            def cb(label):
                dac.set_source(channel, label)
            return cb
        rb.on_clicked(make_cb(ch))
        radios.append(rb)

    lines[-1].axes.set_xlabel("time [us]")
    closed = {"f": False}
    fig.canvas.mpl_connect("close_event", lambda _e: closed.update(f=True))

    try:
        while not closed["f"]:
            snap = tap.snapshot()
            dt_us = tap.decim / 1000.0
            t = np.arange(args.time_span) * dt_us
            for ch, ln in enumerate(lines):
                ln.set_data(t, snap[ch][-args.time_span:] * VOLTS_PER_COUNT)
                ln.axes.set_xlim(0, t[-1])
            fig.suptitle(f"live ADC stream  decim={tap.decim} "
                         f"({1000.0/tap.decim:.2f} MS/s/ch)  "
                         f"radio = DAC source per channel", fontsize=10)
            plt.pause(1.0 / args.fps)
    except KeyboardInterrupt:
        pass
    finally:
        tap.close()
        dac.close()


if __name__ == "__main__":
    main()
