#!/usr/bin/env python3
"""Real-time 4-channel ADC scope (PyQtGraph) with live DAC source control.

A smooth, native-Qt replacement for dac_source_scope.py (which used matplotlib +
plt.pause and felt clunky). PyQtGraph updates curves with setData instead of
redrawing the whole figure, so this runs at 60 fps with real Qt widgets.

Controls (right panel):
  - Per channel: DDS / BRAM / Neuron radio -> NSRC over UART (instant switch).
  - CIC anti-alias (chip 1 = ch2/ch3): live toggle vs keep-1-of-D (STRM CIC).
    Chip 0 (ch0/ch1) is always keep-1-of-D, so it's a built-in A/B: drive the
    same source above the decimated Nyquist and watch ch0/ch1 alias while
    ch2/ch3 stay clean. (Run at decim=128 so both chips share one timebase.)
  - Run/Pause, Autoscale Y, and a Time / Spectrum (FFT) view switch.

At startup it readies all three DAC sources so switching is instant (BRAM sines
per channel, DDS tone, Izhikevich neuron profiles). UART commands run on worker
threads so the GUI never blocks.

Prereqs: board programmed + A53 PS-eth app running; UART on COM10; NIC at
192.168.2.1/24. See notes/dac_sources_howto.md.

  python scripts/dac_scope_qt.py
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import threading
import time

import numpy as np
import serial
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

HDR = struct.Struct("<IHHIIIIII")
MAGIC = 0x53514144
VOLTS_PER_COUNT = 1.9 / 65536.0
PROGRAM_SAMPLES = 16384
NEURON_PROFILES = ["regular", "bursting", "chattering", "fast"]
SOURCE_LABELS = ["DDS", "BRAM", "Neuron"]
LABEL_TO_NSRC = {"DDS": "dds", "BRAM": "bram", "Neuron": "izh"}
CH_COLORS = ["#4FC3F7", "#81C784", "#FFB74D", "#E57373"]


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

    def set_source(self, ch, label):
        return self.cmd(f"NSRC {ch} {LABEL_TO_NSRC[label]}", ok=("DAC source", "ERR"))

    def set_cic(self, on):
        return self.cmd(f"STRM CIC {'on' if on else 'off'}", ok=("OK STRM", "ERR"))

    def setup(self, decim, dds_step, amplitude, freqs_mhz, usecic):
        self.cmd("WRTE 2 0x01000018")
        self.cmd(f"WRTE 3 0x{(dds_step & 0xFFFFFF) << 8:08X}")
        for ch, f in enumerate(freqs_mhz):
            self.prog(ch, sine_words(f, amplitude))
        for ch, prof in enumerate(NEURON_PROFILES):
            self.cmd(f"NEUR {ch} {prof}")
        self.cmd("NEUR all period 1")
        self.cmd("NEUR all dt 0x8000")
        self.cmd(f"STRM {decim}{' cic' if usecic else ''}", ok=("OK STRM", "ERR"))

    def close(self):
        try:
            self.cmd("STRM STOP", ok=("OK STRM", "ERR"))
        except Exception:  # noqa: BLE001
            pass
        self.s.close()


# ---------------------------------------------------------------- UDP stream
class StreamTap:
    """Latency-bounded scope receiver.

    A scope only needs *recent* samples, not every one. So we (a) keep the OS
    receive buffer small (~16 ms) — when Python falls behind, the kernel drops
    the OLDEST queued packets instead of building a half-second backlog that you
    then stare at; and (b) on every wakeup we drain ALL packets the kernel has
    queued and only render the newest, so the display stays current even if a
    burst arrives between frames. Samples land in per-channel circular buffers
    (only the new samples are written each packet — no full-window memmove)."""

    def __init__(self, board_ip, cmd_port, local_ip, local_port, window,
                 rcvbuf=1 << 20):
        self.window = window
        self.cbuf = {i: np.zeros(window, dtype=np.int16) for i in range(4)}
        self.wpos = {i: 0 for i in range(4)}
        self.expected = {0: None, 1: None}
        self.lock = threading.Lock()
        self.decim = 128
        self.packets = 0
        self.drops = 0
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Small rcvbuf == low latency: bound the kernel backlog to ~16 ms.
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.sock.bind((local_ip, local_port))
        self.sock.settimeout(0.2)
        self.board = (board_ip, cmd_port)
        self.sock.sendto(b"STRM", self.board)
        threading.Thread(target=self._rx, daemon=True).start()

    def _write(self, ch, col):
        n = len(col)
        cap = self.window
        if n >= cap:
            self.cbuf[ch][:] = col[-cap:]
            self.wpos[ch] = 0
            return
        w = self.wpos[ch]
        end = w + n
        if end <= cap:
            self.cbuf[ch][w:end] = col
        else:
            first = cap - w
            self.cbuf[ch][w:] = col[:first]
            self.cbuf[ch][:n - first] = col[first:]
        self.wpos[ch] = end % cap

    def _process(self, data):
        if len(data) < HDR.size:
            return
        magic, _v, hdr, seq, chip, _o, count, _d, dec = HDR.unpack_from(data)
        if magic != MAGIC or chip > 1:
            return
        exp = self.expected[chip]
        if exp is not None and seq != exp:
            self.drops += max(0, seq - exp)
        if exp is not None and seq < exp:
            return
        self.expected[chip] = seq + 1
        self.decim = dec
        self.packets += 1
        payload = data[hdr:hdr + count]
        payload = payload[: len(payload) - (len(payload) % 16)]
        sm = np.frombuffer(payload, dtype="<i2").reshape(-1, 8)
        base = chip * 2
        with self.lock:
            self._write(base, sm[:, :4].ravel())
            self._write(base + 1, sm[:, 4:].ravel())

    def _rx(self):
        self.sock.setblocking(False)
        while self.running:
            # Block (with timeout) for the first packet, then drain everything
            # else the kernel already has queued so we end up at the newest.
            try:
                self.sock.settimeout(0.2)
                self._process(self.sock.recv(4096))
            except socket.timeout:
                continue
            except BlockingIOError:
                continue
            except OSError:
                break
            self.sock.setblocking(False)
            while self.running:
                try:
                    self._process(self.sock.recv(4096))
                except (BlockingIOError, socket.timeout):
                    break
                except OSError:
                    return

    def snapshot(self):
        with self.lock:
            out = {}
            for c in range(4):
                w = self.wpos[c]
                b = self.cbuf[c]
                out[c] = np.concatenate((b[w:], b[:w]))  # oldest -> newest
            return out

    def close(self):
        self.running = False
        try:
            self.sock.sendto(b"STOP", self.board)
        except OSError:
            pass
        self.sock.close()


# ---------------------------------------------------------------- GUI
class ScopeWindow(QtWidgets.QMainWindow):
    def __init__(self, dac, tap, args):
        super().__init__()
        self.dac = dac
        self.tap = tap
        self.args = args
        self.paused = False
        self.autoscale = False
        self.fft_view = False
        self.setWindowTitle("DAC source live scope (PyQtGraph)")
        self.resize(1280, 820)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        # ---- plots ----
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("#101418")
        root.addWidget(self.glw, stretch=4)
        self.plots, self.curves = [], []
        for ch in range(4):
            p = self.glw.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            p.setMouseEnabled(x=True, y=True)
            if ch < 3:
                p.getAxis("bottom").setStyle(showValues=False)
            curve = p.plot(pen=pg.mkPen(CH_COLORS[ch], width=1.3))
            self.plots.append(p)
            self.curves.append(curve)
        self.plots[-1].setLabel("bottom", "time", units="s")

        # ---- controls ----
        panel = QtWidgets.QWidget()
        panel.setMaximumWidth(280)
        root.addWidget(panel, stretch=1)
        col = QtWidgets.QVBoxLayout(panel)

        self.src_groups = []
        for ch in range(4):
            box = QtWidgets.QGroupBox(f"DAC{ch} source")
            hb = QtWidgets.QHBoxLayout(box)
            grp = QtWidgets.QButtonGroup(box)
            for i, lab in enumerate(SOURCE_LABELS):
                rb = QtWidgets.QRadioButton(lab)
                if lab == args.initial:
                    rb.setChecked(True)
                rb.toggled.connect(self._make_src_cb(ch, lab, rb))
                hb.addWidget(rb)
                grp.addButton(rb)
            self.src_groups.append(grp)
            col.addWidget(box)

        self.cic_chk = QtWidgets.QCheckBox("CIC anti-alias  (chip1: ch2/ch3)")
        self.cic_chk.setChecked(args.cic)
        self.cic_chk.toggled.connect(self._on_cic)
        col.addWidget(self.cic_chk)

        self.auto_chk = QtWidgets.QCheckBox("Autoscale Y")
        self.auto_chk.toggled.connect(lambda v: setattr(self, "autoscale", v))
        col.addWidget(self.auto_chk)

        view_box = QtWidgets.QGroupBox("View")
        vh = QtWidgets.QHBoxLayout(view_box)
        self.rb_time = QtWidgets.QRadioButton("Time")
        self.rb_fft = QtWidgets.QRadioButton("Spectrum")
        self.rb_time.setChecked(True)
        self.rb_time.toggled.connect(self._on_view)
        vh.addWidget(self.rb_time)
        vh.addWidget(self.rb_fft)
        col.addWidget(view_box)

        self.run_btn = QtWidgets.QPushButton("Pause")
        self.run_btn.clicked.connect(self._on_run)
        col.addWidget(self.run_btn)

        col.addStretch(1)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        col.addWidget(self.status)

        self._apply_view_ranges()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update)
        self.timer.start(int(1000.0 / args.fps))

    # -- control callbacks (UART runs off the GUI thread) --
    def _bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _make_src_cb(self, ch, label, rb):
        def cb(checked):
            if checked:
                self._bg(lambda: self.dac.set_source(ch, label))
        return cb

    def _on_cic(self, on):
        self._bg(lambda: self.dac.set_cic(on))

    def _on_run(self):
        self.paused = not self.paused
        self.run_btn.setText("Run" if self.paused else "Pause")

    def _on_view(self, _checked):
        self.fft_view = self.rb_fft.isChecked()
        self._apply_view_ranges()

    def _apply_view_ranges(self):
        for ch, p in enumerate(self.plots):
            p.enableAutoRange("y", False)
            if self.fft_view:
                p.setLabel("left", f"ch{ch}", units="dB")
                p.setYRange(-90, 5)
                p.setLabel("bottom", "frequency", units="Hz")
            else:
                p.setLabel("left", f"ch{ch}", units="V")
                p.setYRange(-0.95, 0.95)
                if ch == 3:
                    p.setLabel("bottom", "time", units="s")

    def _update(self):
        if self.paused:
            return
        snap = self.tap.snapshot()
        decim = max(1, self.tap.decim)
        fs = 1.0e9 / decim
        span = self.args.time_span
        for ch in range(4):
            y = snap[ch][-span:].astype(np.float64)
            if self.fft_view:
                v = y * VOLTS_PER_COUNT
                v = v - v.mean()
                w = np.hanning(len(v))
                Y = np.abs(np.fft.rfft(v * w)) / (np.sum(w) / 2.0)
                f = np.fft.rfftfreq(len(v), 1.0 / fs)
                db = 20.0 * np.log10(np.maximum(Y, 1e-9))
                self.curves[ch].setData(f, db)
                if self.autoscale:
                    self.plots[ch].setYRange(db.max() - 100, db.max() + 5)
            else:
                t = np.arange(len(y)) / fs
                v = y * VOLTS_PER_COUNT
                self.curves[ch].setData(t, v)
                if self.autoscale:
                    m = max(0.02, np.abs(v).max() * 1.2)
                    self.plots[ch].setYRange(-m, m)
        self.status.setText(
            f"decim={decim}   {1000.0/decim:.2f} MS/s/ch   "
            f"Nyquist {500.0/decim:.3f} MHz\n"
            f"packets={self.tap.packets}   drops={self.tap.drops}\n"
            f"ch0/ch1 = keep-1-of-D   ch2/ch3 = "
            f"{'CIC' if self.cic_chk.isChecked() else 'keep-1-of-D'}"
        )

    def closeEvent(self, ev):
        self.timer.stop()
        self.tap.close()
        self.dac.close()
        super().closeEvent(ev)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--local-ip", default="192.168.2.1")
    ap.add_argument("--local-port", type=int, default=5005)
    ap.add_argument("--decim", type=int, default=128,
                    help="decimation D; use 128 so chip0 keep matches chip1 CIC")
    ap.add_argument("--dds-step", type=lambda x: int(x, 0), default=4096)
    ap.add_argument("--amplitude", type=lambda x: int(x, 0), default=0x5000)
    ap.add_argument("--freqs", default="0.122,0.183,0.244,0.305")
    ap.add_argument("--window", type=int, default=8192)
    ap.add_argument("--time-span", type=int, default=1024)
    ap.add_argument("--rcvbuf", type=lambda x: int(x, 0), default=1 << 20,
                    help="UDP socket buffer bytes; small = low latency (default 1 MB)")
    ap.add_argument("--initial", default="BRAM", choices=SOURCE_LABELS)
    ap.add_argument("--cic", action="store_true", help="start with chip1 CIC on")
    ap.add_argument("--fps", type=float, default=60.0)
    args = ap.parse_args()

    freqs = [float(x) for x in args.freqs.split(",")]
    dac = DacControl(args.port)
    print("Readying DAC sources...")
    dac.setup(args.decim, args.dds_step, args.amplitude, freqs, args.cic)
    for ch in range(4):
        dac.set_source(ch, args.initial)
    tap = StreamTap(args.board_ip, args.cmd_port, args.local_ip,
                    args.local_port, args.window, args.rcvbuf)

    pg.setConfigOptions(antialias=True)
    app = QtWidgets.QApplication([])
    win = ScopeWindow(dac, tap, args)
    win.show()
    app.exec_()


if __name__ == "__main__":
    main()
