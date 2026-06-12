#!/usr/bin/env python3
"""Real-time 4-channel ADC scope (PyQtGraph) with DAC control + UART capture.

Live Ethernet ADC stream on the left; a control panel on the right:
  - Connection: pick the UART COM port and connect/reconnect.
  - Per channel: source = DDS / BRAM / Neuron, plus a neuron-profile dropdown.
  - BRAM waveform builder: pick a shape (Sine/Triangle/Trapezoid/Square/Saw),
    period (ns), pulse width (ns), and a voltage range (clamped to the DAC's
    allowable range, default 0 V .. max), then program it to a channel.
  - CIC anti-alias (chip 1) toggle, autoscale, rising-edge trigger, Time/FFT.
  - UART Capture: grab an ADC snapshot over UART (CAPT) and pop up the 4
    channels -- works without the Ethernet path.

Prereqs: board programmed + A53 PS-eth app running; UART (default COM10); NIC at
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
import serial.tools.list_ports as list_ports
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

HDR = struct.Struct("<IHHIIIIII")
MAGIC = 0x53514144
VOLTS_PER_COUNT = 1.9 / 65536.0
DAC_FULLSCALE = 32767
DAC_VMAX = DAC_FULLSCALE * VOLTS_PER_COUNT      # ~0.95 V
DAC_VMIN = -DAC_VMAX
PROGRAM_SAMPLES = 16384
CAPT_SYNC = b"\xFE\x10\xCA\xFE"
NEURON_PROFILES = ["regular", "bursting", "chattering", "fast"]
SOURCE_LABELS = ["DDS", "BRAM", "Neuron"]
LABEL_TO_NSRC = {"DDS": "dds", "BRAM": "bram", "Neuron": "izh"}
WAVEFORMS = ["Sine", "Triangle", "Trapezoid", "Square", "Sawtooth"]
CH_COLORS = ["#4FC3F7", "#81C784", "#FFB74D", "#E57373"]


# --------------------------------------------------------------- DAC content
def clamp_s16(v):
    return max(-DAC_FULLSCALE, min(DAC_FULLSCALE, int(round(v))))


def volts_to_counts(v):
    return clamp_s16(v / VOLTS_PER_COUNT)


def pack_pair(s0, s1):
    return ((s1 & 0xFFFF) << 16) | (s0 & 0xFFFF)


def gen_waveform(kind, period_ns, width_ns, vlo, vhi):
    """Build a seamless BRAM loop (<=16384 samples, multiple of 4) of `kind`
    with the given period/width (ns, 1 sample = 1 ns) mapped into [vlo, vhi]."""
    period = max(2, int(period_ns))
    width = max(1, min(int(width_ns), period))
    i = np.arange(period)
    if kind == "Sine":
        shape = 0.5 * (1.0 + np.sin(2.0 * np.pi * i / period))
    elif kind == "Triangle":
        shape = 1.0 - np.abs(2.0 * i / period - 1.0)
    elif kind == "Sawtooth":
        shape = i / period
    elif kind == "Square":
        shape = (i < width).astype(float)
    elif kind == "Trapezoid":
        rise = max(1, (period - width) // 2)
        shape = np.zeros(period)
        shape[:rise] = np.linspace(0.0, 1.0, rise, endpoint=False)
        shape[rise:rise + width] = 1.0
        fs = rise + width
        fe = min(period, fs + rise)
        if fe > fs:
            shape[fs:fe] = np.linspace(1.0, 0.0, fe - fs, endpoint=False)
    else:
        shape = np.zeros(period)
    lo = volts_to_counts(vlo)
    hi = volts_to_counts(vhi)
    one = np.clip(np.round(lo + shape * (hi - lo)), -DAC_FULLSCALE, DAC_FULLSCALE)
    # Fill EXACTLY the full 16384-sample BRAM (8192 words) by tiling the period.
    # A full loop keeps the BRAM frame_count at 4096 (RW3 high = 0x1000), which
    # is the only value that avoids colliding with the IZH config bits in the
    # overloaded RW3. Periods that divide 16384 are seamless; others wrap once
    # per 16 us loop (negligible for viewing).
    full = np.resize(one.astype(int), PROGRAM_SAMPLES)
    words = [pack_pair(full[2 * k], full[2 * k + 1])
             for k in range(PROGRAM_SAMPLES // 2)]
    return words


# --------------------------------------------------------------- UART control
class DacControl:
    def __init__(self, port, baud=115200):
        self.s = serial.Serial(port, baud, timeout=2, write_timeout=3)
        self.lock = threading.Lock()
        self.port = port
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
        # Full-loop frame_count=4096 + program_enable -- the proven value from
        # quad_sine_loopback_check_uart.py (0x1000 high byte avoids the IZH
        # config bits that an arbitrary frame count would corrupt).
        self.cmd("WRTE 3 0x00100060")

    def set_source(self, ch, label):
        return self.cmd(f"NSRC {ch} {LABEL_TO_NSRC[label]}", ok=("DAC source", "ERR"))

    def set_neuron(self, ch, profile):
        return self.cmd(f"NEUR {ch} {profile}", ok=("OK", "NEUR", "ERR"))

    def set_cic(self, on):
        return self.cmd(f"STRM CIC {'on' if on else 'off'}", ok=("OK STRM", "ERR"))

    def base_setup(self, decim, usecic):
        self.cmd("WRTE 2 0x01000018")
        for ch, prof in enumerate(NEURON_PROFILES):
            self.cmd(f"NEUR {ch} {prof}")
        self.cmd("NEUR all period 1")
        self.cmd("NEUR all dt 0x8000")
        self.cmd(f"STRM {decim}{' cic' if usecic else ''}", ok=("OK STRM", "ERR"))

    def uart_capture(self, frames):
        """PCAP <frames> -> 4-channel int16 arrays (over UART, blocking).
        PCAP keeps RW3_DAC_PROGRAM_EN set so BRAM channels keep playing during
        the capture; plain CAPT clears it and BRAM would read as noise."""
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write(f"PCAP {frames}\n".encode("ascii"))
            self.s.flush()
            win = bytearray()
            deadline = time.time() + 15
            while time.time() < deadline:
                b = self.s.read(1)
                if not b:
                    continue
                win += b
                if len(win) > 4:
                    del win[0]
                if bytes(win) == CAPT_SYNC:
                    break
            else:
                return None
            need = frames * 8 * 4
            data = bytearray()
            while len(data) < need:
                chunk = self.s.read(need - len(data))
                if not chunk:
                    break
                data += chunk
        if len(data) < need:
            return None
        arr = np.frombuffer(bytes(data), dtype="<u4").reshape(-1, 8)
        chans = {}
        for ch in range(4):
            w0, w1 = arr[:, 2 * ch], arr[:, 2 * ch + 1]
            s = np.empty(len(arr) * 4, dtype=np.int16)
            s[0::4] = (w0 & 0xFFFF).astype(np.int16)
            s[1::4] = ((w0 >> 16) & 0xFFFF).astype(np.int16)
            s[2::4] = (w1 & 0xFFFF).astype(np.int16)
            s[3::4] = ((w1 >> 16) & 0xFFFF).astype(np.int16)
            chans[ch] = s
        return chans

    def close(self):
        try:
            self.cmd("STRM STOP", ok=("OK STRM", "ERR"))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.s.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------- UDP stream
class StreamTap:
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
        while self.running:
            try:
                self.sock.settimeout(0.2)
                self._process(self.sock.recv(4096))
            except (socket.timeout, BlockingIOError):
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
            return {c: np.concatenate((self.cbuf[c][self.wpos[c]:],
                                       self.cbuf[c][:self.wpos[c]]))
                    for c in range(4)}

    def close(self):
        self.running = False
        try:
            self.sock.sendto(b"STOP", self.board)
        except OSError:
            pass
        self.sock.close()


# --------------------------------------------------------------- GUI
class ScopeWindow(QtWidgets.QMainWindow):
    captured = QtCore.pyqtSignal(object)      # emits {ch: int16[]} from worker

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.dac = None
        self.tap = None
        self.paused = False
        self.autoscale = False
        self.fft_view = False
        self.trigger = True
        self._popup = None
        self.setWindowTitle("DAC scope + control (PyQtGraph)")
        self.resize(1360, 880)

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
            if ch < 3:
                p.getAxis("bottom").setStyle(showValues=False)
            self.plots.append(p)
            self.curves.append(p.plot(pen=pg.mkPen(CH_COLORS[ch], width=1.3)))

        # ---- control panel (scrollable) ----
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumWidth(330)
        root.addWidget(scroll)
        panel = QtWidgets.QWidget()
        scroll.setWidget(panel)
        col = QtWidgets.QVBoxLayout(panel)

        # connection
        conn = QtWidgets.QGroupBox("Connection")
        cg = QtWidgets.QGridLayout(conn)
        self.port_cb = QtWidgets.QComboBox()
        self.refresh_ports()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect)
        refresh = QtWidgets.QPushButton("↻")
        refresh.setMaximumWidth(32)
        refresh.clicked.connect(self.refresh_ports)
        cg.addWidget(QtWidgets.QLabel("COM"), 0, 0)
        cg.addWidget(self.port_cb, 0, 1)
        cg.addWidget(refresh, 0, 2)
        cg.addWidget(self.connect_btn, 1, 0, 1, 3)
        self.conn_lbl = QtWidgets.QLabel("not connected")
        self.conn_lbl.setStyleSheet("color:#E57373;")
        cg.addWidget(self.conn_lbl, 2, 0, 1, 3)
        col.addWidget(conn)

        # per-channel source + neuron profile
        self.src_groups, self.prof_cbs = [], []
        for ch in range(4):
            box = QtWidgets.QGroupBox(f"DAC{ch}")
            g = QtWidgets.QGridLayout(box)
            grp = QtWidgets.QButtonGroup(box)
            for i, lab in enumerate(SOURCE_LABELS):
                rb = QtWidgets.QRadioButton(lab)
                if lab == args.initial:
                    rb.setChecked(True)
                rb.toggled.connect(self._make_src_cb(ch, lab))
                g.addWidget(rb, 0, i)
                grp.addButton(rb)
            self.src_groups.append(grp)
            prof = QtWidgets.QComboBox()
            prof.addItems(NEURON_PROFILES)
            prof.setCurrentIndex(ch % len(NEURON_PROFILES))
            prof.currentTextChanged.connect(self._make_prof_cb(ch))
            g.addWidget(QtWidgets.QLabel("neuron"), 1, 0)
            g.addWidget(prof, 1, 1, 1, 2)
            self.prof_cbs.append(prof)
            col.addWidget(box)

        # BRAM waveform builder
        wf = QtWidgets.QGroupBox("BRAM waveform")
        wg = QtWidgets.QGridLayout(wf)
        self.wf_ch = QtWidgets.QComboBox()
        self.wf_ch.addItems(["ch0", "ch1", "ch2", "ch3", "all"])
        self.wf_kind = QtWidgets.QComboBox()
        self.wf_kind.addItems(WAVEFORMS)
        self.wf_period = QtWidgets.QSpinBox()
        self.wf_period.setRange(2, PROGRAM_SAMPLES)
        self.wf_period.setValue(35)
        self.wf_period.setSuffix(" ns")
        self.wf_width = QtWidgets.QSpinBox()
        self.wf_width.setRange(1, PROGRAM_SAMPLES)
        self.wf_width.setValue(7)
        self.wf_width.setSuffix(" ns")
        self.wf_vlo = QtWidgets.QDoubleSpinBox()
        self.wf_vlo.setRange(DAC_VMIN, DAC_VMAX)
        self.wf_vlo.setDecimals(3)
        self.wf_vlo.setSingleStep(0.05)
        self.wf_vlo.setValue(0.0)
        self.wf_vlo.setSuffix(" V")
        self.wf_vhi = QtWidgets.QDoubleSpinBox()
        self.wf_vhi.setRange(DAC_VMIN, DAC_VMAX)
        self.wf_vhi.setDecimals(3)
        self.wf_vhi.setSingleStep(0.05)
        self.wf_vhi.setValue(round(DAC_VMAX, 3))
        self.wf_vhi.setSuffix(" V")
        self.wf_btn = QtWidgets.QPushButton("Program BRAM")
        self.wf_btn.clicked.connect(self._on_program)
        wg.addWidget(QtWidgets.QLabel("target"), 0, 0)
        wg.addWidget(self.wf_ch, 0, 1)
        wg.addWidget(QtWidgets.QLabel("shape"), 1, 0)
        wg.addWidget(self.wf_kind, 1, 1)
        wg.addWidget(QtWidgets.QLabel("period"), 2, 0)
        wg.addWidget(self.wf_period, 2, 1)
        wg.addWidget(QtWidgets.QLabel("width"), 3, 0)
        wg.addWidget(self.wf_width, 3, 1)
        wg.addWidget(QtWidgets.QLabel("V min"), 4, 0)
        wg.addWidget(self.wf_vlo, 4, 1)
        wg.addWidget(QtWidgets.QLabel("V max"), 5, 0)
        wg.addWidget(self.wf_vhi, 5, 1)
        wg.addWidget(self.wf_btn, 6, 0, 1, 2)
        rng = QtWidgets.QLabel(f"allowed: {DAC_VMIN:.2f} .. {DAC_VMAX:.2f} V")
        rng.setStyleSheet("color:#9fb3c8; font-size:10px;")
        wg.addWidget(rng, 7, 0, 1, 2)
        col.addWidget(wf)

        # display + capture options
        opt = QtWidgets.QGroupBox("Display / capture")
        og = QtWidgets.QGridLayout(opt)
        self.cic_chk = QtWidgets.QCheckBox("CIC anti-alias (ch2/3)")
        self.cic_chk.setChecked(args.cic)
        self.cic_chk.toggled.connect(self._on_cic)
        self.auto_chk = QtWidgets.QCheckBox("Autoscale Y")
        self.auto_chk.toggled.connect(lambda v: setattr(self, "autoscale", v))
        self.trig_chk = QtWidgets.QCheckBox("Trigger")
        self.trig_chk.setChecked(True)
        self.trig_chk.toggled.connect(lambda v: setattr(self, "trigger", v))
        self.rb_time = QtWidgets.QRadioButton("Time")
        self.rb_fft = QtWidgets.QRadioButton("FFT")
        self.rb_time.setChecked(True)
        self.rb_time.toggled.connect(self._on_view)
        self.run_btn = QtWidgets.QPushButton("Pause")
        self.run_btn.clicked.connect(self._on_run)
        self.capt_btn = QtWidgets.QPushButton("UART Capture")
        self.capt_btn.clicked.connect(self._on_capture)
        self.capt_frames = QtWidgets.QSpinBox()
        self.capt_frames.setRange(64, 4096)
        self.capt_frames.setValue(512)
        self.capt_frames.setSuffix(" frames")
        og.addWidget(self.cic_chk, 0, 0, 1, 2)
        og.addWidget(self.auto_chk, 1, 0)
        og.addWidget(self.trig_chk, 1, 1)
        og.addWidget(self.rb_time, 2, 0)
        og.addWidget(self.rb_fft, 2, 1)
        og.addWidget(self.run_btn, 3, 0, 1, 2)
        og.addWidget(self.capt_frames, 4, 0)
        og.addWidget(self.capt_btn, 4, 1)
        col.addWidget(opt)

        col.addStretch(1)
        self.status = QtWidgets.QLabel("Connect to a COM port to begin.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        col.addWidget(self.status)

        self._apply_view_ranges()
        self._set_controls_enabled(False)
        self.captured.connect(self._show_capture)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update)
        self.timer.start(int(1000.0 / args.fps))

    # ---- connection ----
    def refresh_ports(self):
        cur = self.port_cb.currentText() if hasattr(self, "port_cb") else None
        ports = [p.device for p in list_ports.comports()]
        if self.args.port not in ports:
            ports.insert(0, self.args.port)
        self.port_cb.blockSignals(True)
        self.port_cb.clear()
        self.port_cb.addItems(ports)
        target = cur if cur in ports else self.args.port
        if target in ports:
            self.port_cb.setCurrentText(target)
        self.port_cb.blockSignals(False)

    def _on_connect(self):
        port = self.port_cb.currentText()
        self._set_controls_enabled(False)
        self.conn_lbl.setText(f"connecting {port}...")
        self.conn_lbl.setStyleSheet("color:#FFB74D;")
        QtWidgets.QApplication.processEvents()
        try:
            if self.tap:
                self.tap.close()
            if self.dac:
                self.dac.close()
            self.dac = DacControl(port)
            self.dac.base_setup(self.args.decim, self.args.cic)
            for ch in range(4):
                self.dac.set_source(ch, self.args.initial)
            self.tap = StreamTap(self.args.board_ip, self.args.cmd_port,
                                 self.args.local_ip, self.args.local_port,
                                 self.args.window, self.args.rcvbuf)
        except Exception as e:  # noqa: BLE001
            self.conn_lbl.setText(f"failed: {e}")
            self.conn_lbl.setStyleSheet("color:#E57373;")
            self.dac = None
            return
        self.conn_lbl.setText(f"connected {port}")
        self.conn_lbl.setStyleSheet("color:#81C784;")
        self.connect_btn.setText("Reconnect")
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, on):
        for w in (self.wf_btn, self.cic_chk, self.capt_btn):
            w.setEnabled(on)
        for grp in self.src_groups:
            for b in grp.buttons():
                b.setEnabled(on)
        for cb in self.prof_cbs:
            cb.setEnabled(on)

    # ---- callbacks (UART off the GUI thread) ----
    def _bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _make_src_cb(self, ch, label):
        def cb(checked):
            if checked and self.dac:
                self._bg(lambda: self.dac.set_source(ch, label))
        return cb

    def _make_prof_cb(self, ch):
        def cb(profile):
            if self.dac:
                self._bg(lambda: self.dac.set_neuron(ch, profile))
        return cb

    def _on_cic(self, on):
        if self.dac:
            self._bg(lambda: self.dac.set_cic(on))

    def _on_run(self):
        self.paused = not self.paused
        self.run_btn.setText("Run" if self.paused else "Pause")

    def _on_view(self, _checked):
        self.fft_view = self.rb_fft.isChecked()
        self._apply_view_ranges()

    def _on_program(self):
        if not self.dac:
            return
        kind = self.wf_kind.currentText()
        period = self.wf_period.value()
        width = self.wf_width.value()
        vlo, vhi = self.wf_vlo.value(), self.wf_vhi.value()
        target = self.wf_ch.currentText()
        chans = range(4) if target == "all" else [int(target[-1])]
        words = gen_waveform(kind, period, width, vlo, vhi)
        self.status.setText(f"programming {kind} {period}ns to {target}...")

        def work():
            for ch in chans:
                self.dac.prog(ch, words)
        self._bg(work)

    def _on_capture(self):
        if not self.dac:
            return
        frames = self.capt_frames.value()
        self.capt_btn.setEnabled(False)
        self.status.setText(f"UART capturing {frames} frames...")

        def work():
            data = self.dac.uart_capture(frames)
            self.captured.emit(data)
        self._bg(work)

    def _show_capture(self, chans):
        self.capt_btn.setEnabled(True)
        if chans is None:
            self.status.setText("UART capture failed (no sync / timeout).")
            return
        win = pg.GraphicsLayoutWidget()
        win.setWindowTitle("UART ADC capture")
        win.setBackground("#101418")
        win.resize(900, 700)
        for ch in range(4):
            p = win.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            t = np.arange(len(chans[ch]))  # ns at 1 GS/s
            p.plot(t, chans[ch] * VOLTS_PER_COUNT,
                   pen=pg.mkPen(CH_COLORS[ch], width=1.0))
        win.addLabel("UART CAPT snapshot  (x = ns @ 1 GS/s)", row=4, col=0)
        win.show()
        self._popup = win                  # keep a reference so it isn't GC'd
        self.status.setText(f"UART capture: {len(chans[0])} samples/ch.")

    # ---- display ----
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

    def _trig_slice(self, yfull, span):
        n = len(yfull)
        if n < span + 2:
            return yfull[-span:]
        thr = yfull.mean()
        if yfull.std() < 8.0:
            return yfull[-span:]
        lo = max(1, n - 2 * span)
        hi = n - span
        a, b = yfull[lo - 1:hi - 1], yfull[lo:hi]
        cross = np.where((a < thr) & (b >= thr))[0]
        if len(cross) == 0:
            return yfull[-span:]
        idx = lo + cross[-1]
        return yfull[idx:idx + span]

    def _update(self):
        if self.paused or self.tap is None:
            return
        snap = self.tap.snapshot()
        decim = max(1, self.tap.decim)
        fs = 1.0e9 / decim
        span = self.args.time_span
        for ch in range(4):
            full = snap[ch].astype(np.float64)
            if self.fft_view:
                v = full[-span:] * VOLTS_PER_COUNT
                v = v - v.mean()
                w = np.hanning(len(v))
                Y = np.abs(np.fft.rfft(v * w)) / (np.sum(w) / 2.0)
                f = np.fft.rfftfreq(len(v), 1.0 / fs)
                db = 20.0 * np.log10(np.maximum(Y, 1e-9))
                self.curves[ch].setData(f, db)
                if self.autoscale:
                    self.plots[ch].setYRange(db.max() - 100, db.max() + 5)
            else:
                y = self._trig_slice(full, span) if self.trigger else full[-span:]
                t = np.arange(len(y)) / fs
                v = y * VOLTS_PER_COUNT
                self.curves[ch].setData(t, v)
                if self.autoscale:
                    m = max(0.02, np.abs(v).max() * 1.2)
                    self.plots[ch].setYRange(-m, m)
        self.status.setText(
            f"decim={decim}  {1000.0/decim:.2f} MS/s/ch  "
            f"Nyq {500.0/decim:.3f} MHz | pkts={self.tap.packets} "
            f"drops={self.tap.drops} | ch2/3={'CIC' if self.cic_chk.isChecked() else 'keep'}"
        )

    def closeEvent(self, ev):
        self.timer.stop()
        if self.tap:
            self.tap.close()
        if self.dac:
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
    ap.add_argument("--decim", type=int, default=128)
    ap.add_argument("--window", type=int, default=8192)
    ap.add_argument("--time-span", type=int, default=1024)
    ap.add_argument("--rcvbuf", type=lambda x: int(x, 0), default=1 << 20)
    ap.add_argument("--initial", default="BRAM", choices=SOURCE_LABELS)
    ap.add_argument("--cic", action="store_true")
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--autoconnect", action="store_true",
                    help="connect to --port immediately on launch")
    args = ap.parse_args()

    pg.setConfigOptions(antialias=True)
    app = QtWidgets.QApplication([])
    win = ScopeWindow(args)
    win.show()
    if args.autoconnect:
        win._on_connect()
    app.exec_()


if __name__ == "__main__":
    main()
