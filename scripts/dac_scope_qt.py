#!/usr/bin/env python3
"""Real-time 4-channel ADC scope (PyQtGraph) with DAC control + UART capture.

ADC plot on the left; a control panel on the right. The board does NOT stream
on connect -- acquisition is opt-in:
  - Collect Ethernet: one-shot full-rate burst snapshot (BCAP+BRDO) in a popup,
    with a selectable size (MB/chip), saved to disk -- the reliable way to grab
    data.
  - Start/Stop Auto-Sample: takes a fresh small one-shot burst once per second
    and draws it in the main plots (off by default). Auto-samples are NOT saved.

  - Connection: pick the UART COM port and connect/reconnect (no streaming).
  - Per channel (DAC0..3): pick any crossbar source -- Off / DDS / BRAM 0-3 /
    Spike 0-3 / Monitor 0-3 (per-neuron current) / Current source / Tag -- from the source
    dropdown (+ a neuron-profile dropdown), then hit "Program DACn" to commit:
    the dropdowns only stage a choice; the button sends NSRC (+ NEUR when the
    source is a Spike/Monitor of a neuron) and the per-DAC status line confirms
    "OK — <source>" once the board is reconfigured.
  - Neuron params: a/b/c/d/I spinboxes (physical Izhikevich units). Set them
    (or "load profile" to stage a built-in profile), then hit the per-neuron
    "Prog 0..3" button (or "Prog all") to apply to that target -- each NEUR
    write resets + reloads the target, so it runs fresh with exactly these
    values. A status line reports OK / ERR after each program. Collect Ethernet
    (or Auto-Sample) to verify the dynamics.
  - Captures are saved automatically whichever transport you use: each grab
    writes cap_<timestamp>_<src>.npz (+ per-channel CSVs) into --capture-dir
    (default <repo>/captures), where <src> = "uart" (UART Capture) or "eth"
    (Collect Ethernet) so the two are easy to tell apart.
  - BRAM waveform builder: pick a shape (Sine/Triangle/Trapezoid/Square/Saw),
    period (ns), pulse width (ns), and a voltage range (clamped to the DAC's
    allowable range, default 0 V .. max), then program it to a channel.
  - Source editors (two pop-up windows):
      * Current source: shows the waveform programmed into the cur_wave current
        RAM. Presets Sine (5 kHz default) / Constant current / Step (0 -> amp);
        the amplitude is in mA and can NEVER go negative. "Program" loads it
        via CURW; route Current source on a DAC to mirror the injected current
        out.
      * Pulse shape: shows the spike pulse (<=4096 signed DAC samples) and lets you
        drag individual points up/down; "Program pulse" sends it via PULS. The
        shaped pulse is one crossbar input -- route a Spike source to emit it.
  - CIC anti-alias (chip 1) toggle, autoscale, rising-edge trigger, Time/FFT.
  - UART Capture: grab an ADC snapshot over UART (PCAP) and pop up the 4
    channels -- works without the Ethernet path.

Prereqs: board programmed + A53 PS-eth app running; UART (default COM10); NIC at
192.168.2.1/24. See notes/dac_sources_howto.md.

  python scripts/dac_scope_qt.py
"""
from __future__ import annotations

import argparse
import math
import os
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
# 16:4 DAC crossbar sources -- one per-DAC pick, matching firmware NSRC tokens
# (reg17 codes: 0 off, 1 DDS, 2-5 BRAM, 6-9 spike, 10-13 monitor,
# 14 tag, 15 pure injected current source).
SOURCE_LABELS = [
    "Off", "DDS",
    "BRAM 0", "BRAM 1", "BRAM 2", "BRAM 3",
    "Spike 0", "Spike 1", "Spike 2", "Spike 3",
    "Monitor 0", "Monitor 1", "Monitor 2", "Monitor 3",
    "Current source", "Tag",
]
LABEL_TO_NSRC = {
    "Off": "off", "DDS": "dds",
    "BRAM 0": "bram0", "BRAM 1": "bram1", "BRAM 2": "bram2", "BRAM 3": "bram3",
    "Spike 0": "spike0", "Spike 1": "spike1", "Spike 2": "spike2", "Spike 3": "spike3",
    "Monitor 0": "mon0", "Monitor 1": "mon1", "Monitor 2": "mon2", "Monitor 3": "mon3",
    "Current source": "current",
    "Tag": "tag",
}
WAVEFORMS = ["Sine", "Triangle", "Trapezoid", "Square", "Sawtooth"]
CH_COLORS = ["#4FC3F7", "#81C784", "#FFB74D", "#E57373"]
CAPT_FRAME_OPTIONS = [128, 256, 512, 1024, 2048, 4096]   # 4096 = firmware max
# "Collect Ethernet" one-shot burst sizes, BYTES per chip (sent as BCAP <KB>k;
# samples/ch = bytes/4 since each chip = 2 channels x int16).
COLLECT_SIZE_OPTIONS = [
    (64 * 1024,   "64 KB (16k/ch)"),
    (128 * 1024,  "128 KB (32k/ch)"),
    (256 * 1024,  "256 KB (64k/ch)"),
    (512 * 1024,  "512 KB (128k/ch)"),
    (1 << 20,     "1 MB (256k/ch)"),
    (4 << 20,     "4 MB (1M/ch)"),
    (16 << 20,    "16 MB (4M/ch)"),
    (64 << 20,    "64 MB (16M/ch)"),
]
COLLECT_SIZE_DEFAULT_IDX = 0   # 64 KB/chip
# Auto-Sample: repeated one-shot bursts at a fixed cadence (not the cyclic UDP
# stream). Small + fast so each grab comfortably finishes within the interval.
AUTOSAMPLE_INTERVAL_MS = 1000   # one sample per second
AUTOSAMPLE_BYTES = 64 * 1024    # bytes/chip per auto-sample (16k samples/ch)
# Neuron integration timestep (Q16.16): larger dt -> faster simulation.
NEURON_DT_OPTIONS = [
    ("0.25x slow", 0x2000),
    ("0.5x", 0x4000),
    ("1x normal", 0x8000),
    ("2x", 0x10000),
    ("4x fast", 0x20000),
    ("8x faster", 0x40000),
]
NEURON_DT_DEFAULT = 2   # index of "1x normal"

# Real-time Izhikevich parameters (physical units; converted to Q16.16 for the
# NEUR command). param name matches the firmware NEUR param keyword.
#   (param, label, lo, hi, default, step, decimals)
NEURON_PARAM_SPECS = [
    ("a",      "a  recovery rate",  0.0,   0.5,  0.02,  0.005, 3),
    ("b",      "b  sensitivity",    0.0,   0.5,  0.20,  0.01,  2),
    ("c",      "c  reset v (mV)",  -90.0, -40.0, -65.0, 1.0,   1),
    ("d",      "d  reset u",        0.0,  15.0,  8.0,   0.25,  2),
    ("iconst", "I  drive",          0.0,  40.0,  10.0,  0.5,   1),
]
# Physical values per built-in profile (mirror sw neuron_profiles).
NEURON_PROFILE_VALUES = {
    "regular":    dict(a=0.02, b=0.20, c=-65.0, d=8.0, iconst=10.0),
    "bursting":   dict(a=0.02, b=0.20, c=-55.0, d=4.0, iconst=10.0),
    "chattering": dict(a=0.02, b=0.20, c=-50.0, d=2.0, iconst=10.0),
    "fast":       dict(a=0.10, b=0.20, c=-65.0, d=2.0, iconst=10.0),
}

# --- programmable current source (cur_wave player, firmware CURW) -------------
# Current sources are UNIPOLAR: they can NEVER go negative. We map physical
# milliamps to the neuron model's Q16.16 current unit 1:1 -- i.e. 1 mA == 1.0
# Izhikevich "I" unit -- so 10 mA equals the drive the built-in profiles use to
# spike (iconst~10). The only host-side ceiling is the positive signed Q16.16
# range; larger values would wrap into negative current in the HDL.
MA_TO_Q16 = 65536.0            # 1 mA -> Q16.16 LSBs (1 mA == 1.0 I-unit)
CUR_Q16_POS_MAX = 0x7FFFFFFF
CUR_MAX_MA = CUR_Q16_POS_MAX / MA_TO_Q16
CUR_PLAYER_CLK_HZ = 50.0e6     # the player advances in the 50 MHz neuron clock
CUR_WAVE_MAX = 1024            # cur_wave BRAM depth (firmware CUR_WAVE_DEPTH)
CUR_SINE_SAMPLES = CUR_WAVE_MAX  # use the whole current BRAM when it helps
CUR_SINE_SAMPLES_MIN = 16      # min samples (keeps a high-freq sine recognizable)
CUR_FREQ_REQUEST_MIN_HZ = 1.0e-3
CUR_FREQ_REQUEST_MAX_HZ = CUR_PLAYER_CLK_HZ
CUR_SINE_HW_MIN_HZ = CUR_PLAYER_CLK_HZ / (65535.0 * CUR_SINE_SAMPLES)
CUR_SINE_HW_MAX_HZ = CUR_PLAYER_CLK_HZ / CUR_SINE_SAMPLES_MIN
CUR_SOURCE_PRESETS = ["Sine", "Constant current", "Step 0 -> constant"]
# Some DAC->ADC loopback setups are AC-coupled with a high-pass corner near this
# value. The injected current still drives the neurons below it; use a DC-coupled
# readout path if the analog loopback attenuates low-frequency current readback.
CUR_LOOPBACK_AC_CORNER_HZ = 200e3

# --- programmable spike pulse shape (izh_spike_shaper, firmware PULS) ----------
PULSE_MAX_SAMPLES = 4096       # firmware SPIKE_MAX_SAMPLES
# original 7 ns trapezoid boot default (firmware spike_shape_init_default)
PULSE_DEFAULT = [0x1800, 0x3000, 0x6000, 0x6000, 0x6000, 0x3000, 0x1800]


def ma_to_q16_u32(ma):
    """Physical milliamps -> Q16.16 as a 32-bit word; clamped non-negative
    and to positive signed Q16.16 (current sources can never go negative)."""
    ma = max(0.0, float(ma))
    q = int(round(ma * MA_TO_Q16))
    q = max(0, min(CUR_Q16_POS_MAX, q))
    return q & 0xFFFFFFFF


def format_rate(freq_hz):
    if freq_hz <= 0:
        return "DC/one-shot"
    if freq_hz < 1.0e3:
        return f"{freq_hz:.3g} Hz"
    if freq_hz < 1.0e6:
        return f"{freq_hz/1.0e3:.3g} kHz"
    return f"{freq_hz/1.0e6:.3g} MHz"


def choose_current_timing(freq_hz, n_max=CUR_SINE_SAMPLES,
                          n_min=CUR_SINE_SAMPLES_MIN):
    """Pick sample count and dwell for the 50 MHz current player.

    The player period is count * cycles_per_sample clk_50 ticks.  Search the
    legal current-BRAM sizes and choose the closest frequency, preferring more
    waveform samples when the error ties.
    """
    freq_hz = float(freq_hz)
    if not math.isfinite(freq_hz) or freq_hz <= 0.0:
        freq_hz = CUR_SINE_HW_MIN_HZ
    target_ticks = CUR_PLAYER_CLK_HZ / freq_hz
    max_n = int(max(n_min, min(n_max, round(target_ticks))))
    best = None
    for n in range(int(n_min), max_n + 1):
        cps = int(round(target_ticks / n))
        cps = max(1, min(65535, cps))
        actual = CUR_PLAYER_CLK_HZ / (cps * n)
        err = abs(actual - freq_hz)
        if best is None or err < best[0] or (err == best[0] and n > best[1]):
            best = (err, n, cps, actual)
    _, n, cps, actual = best
    return n, cps, actual


def gen_current_wave(kind, amp_ma, freq_hz, n_max=CUR_SINE_SAMPLES,
                     n_min=CUR_SINE_SAMPLES_MIN):
    """Build a non-negative current waveform for the cur_wave player.

    Returns (samples_ma, cps, actual_hz): per-sample amplitudes in mA (all >= 0),
    the player's cycles-per-sample divisor, and the resulting loop frequency. The
    player loops len(ys) samples advancing every cps clk_50 cycles, so
    f = 50 MHz / (cps * len)."""
    amp_ma = max(0.0, min(CUR_MAX_MA, float(amp_ma)))
    if kind.startswith("Constant"):
        return np.asarray([amp_ma]), 1, 0.0
    if kind.startswith("Step"):
        # Programmed with CURW hold mode: play the 0-to-amp edge once, then hold
        # the final constant-current sample instead of wrapping into a square.
        ys = np.zeros(64)
        ys[16:] = amp_ma
        return ys, 1, 0.0
    n, cps, actual = choose_current_timing(freq_hz, n_max=n_max, n_min=n_min)
    i = np.arange(n)
    ys = 0.5 * amp_ma * (1.0 - np.cos(2.0 * np.pi * i / n))
    return ys, cps, actual


def izh_to_q16(v):
    """Physical Izhikevich value -> signed Q16.16 as a 32-bit word."""
    return int(round(v * 65536.0)) & 0xFFFFFFFF


def save_capture(capture_dir, kind, chans, fs_hz, **meta):
    """Save one capture's 4 channels into the captures subdirectory.

    Every capture is saved regardless of transport; the source is encoded as a
    filename ending (kind = "uart" or "eth"):
      <capture_dir>/cap_<YYYYmmdd_HHMMSS>_<kind>.npz       raw int16 ch0..ch3
      <capture_dir>/cap_<YYYYmmdd_HHMMSS>_<kind>_ch*.csv   per-channel time,volts
    Returns the .npz path (str), or None on failure.
    """
    try:
        os.makedirs(capture_dir, exist_ok=True)
        stem = f"cap_{time.strftime('%Y%m%d_%H%M%S')}_{kind}"
        arrays = {f"ch{ch}": np.asarray(chans[ch], dtype=np.int16)
                  for ch in range(4)}
        npz_path = os.path.join(capture_dir, stem + ".npz")
        np.savez_compressed(npz_path,
                            fs_hz=np.float64(fs_hz), kind=kind, **arrays,
                            **{k: np.asarray(v) for k, v in meta.items()})
        for ch in range(4):
            y = arrays[f"ch{ch}"].astype(np.float64) * VOLTS_PER_COUNT
            t = np.arange(len(y)) / fs_hz
            np.savetxt(os.path.join(capture_dir, f"{stem}_ch{ch}.csv"),
                       np.column_stack((t, y)), delimiter=",",
                       header="time_s,volts", comments="")
        return npz_path
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------- DAC content
def clamp_s16(v):
    return max(-DAC_FULLSCALE, min(DAC_FULLSCALE, int(round(v))))


def volts_to_counts(v):
    return clamp_s16(v / VOLTS_PER_COUNT)


def pack_pair(s0, s1):
    return ((s1 & 0xFFFF) << 16) | (s0 & 0xFFFF)


BRAM_FRAME_SAMPLES = 4   # 1 BRAM "frame" = 4 samples (RW3[31:8] = loop frames)


def gen_waveform(kind, period_ns, width_ns, vlo, vhi):
    """Build a SEAMLESS BRAM loop and its frame count.

    1 sample = 1 ns @ 1 GS/s, so `period_ns` is the period in samples. We tile an
    EXACT integer number of periods so the loop joins end-to-end with no
    discontinuity, and size the loop to a whole number of BRAM frames (4 samples
    each). The BRAM then loops only those frames (RW3[31:8]), so the played tone
    is exactly 1e9/period Hz with zero wrap glitch -- regardless of whether the
    period divides 16384.

    Returns (words, loop_frames): the packed 32-bit words to PROG, and the
    RW3[31:8] loop frame count to play them back seamlessly.
    """
    period = max(2, int(period_ns))
    period = min(period, PROGRAM_SAMPLES)
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
    one = np.clip(np.round(lo + shape * (hi - lo)),
                  -DAC_FULLSCALE, DAC_FULLSCALE).astype(int)

    # Tile the largest integer number of whole periods that fits the BRAM and
    # lands on a 4-sample frame boundary -> seamless loop, exact frequency.
    reps = max(1, PROGRAM_SAMPLES // period)
    loop = reps * period
    while reps > 1 and (loop % BRAM_FRAME_SAMPLES) != 0:
        reps -= 1
        loop = reps * period
    loop -= loop % BRAM_FRAME_SAMPLES          # final guard (>=4, multiple of 4)
    if loop < BRAM_FRAME_SAMPLES:
        loop = BRAM_FRAME_SAMPLES
    samples = np.resize(one, loop)
    words = [pack_pair(int(samples[2 * k]), int(samples[2 * k + 1]))
             for k in range(loop // 2)]
    loop_frames = loop // BRAM_FRAME_SAMPLES
    return words, loop_frames


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

    def cmd(self, c, ok=("OK", "DAC xbar", "STRM")):
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write((c + "\n").encode("ascii"))
            self.s.flush()
            return self._readuntil(ok)

    def prog(self, ch, words, loop_frames=4096):
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write(f"PROG {ch} {len(words)}\n".encode("ascii"))
            self.s.flush()
            self._readuntil(("PGRD",))
            self.s.write(struct.pack(f"<{len(words)}I", *words))
            self.s.flush()
            self._readuntil((f"OK PROG ch={ch}",))
        # RW3[31:8] = BRAM loop frame count (1 frame = 4 samples), [6] = program
        # enable. Setting the loop to exactly the programmed integer-period span
        # plays the waveform seamlessly. (loop_frames=4096 -> 0x00100060, the old
        # full-BRAM value.) The IZH config no longer shares RW3, so an arbitrary
        # frame count is safe now.
        rw3 = ((loop_frames & 0xFFFFFF) << 8) | 0x60
        self.cmd(f"WRTE 3 0x{rw3:08X}")

    def set_source(self, ch, label):
        return self.cmd(f"NSRC {ch} {LABEL_TO_NSRC[label]}", ok=("DAC xbar", "ERR"))

    def set_neuron(self, ch, profile):
        return self.cmd(f"NEUR {ch} {profile}", ok=("OK", "NEUR", "ERR"))

    def set_neuron_dt(self, dt_hex):
        # Integration timestep (Q16.16) for all neurons: larger = faster sim.
        return self.cmd(f"NEUR all dt 0x{dt_hex:X}", ok=("OK", "NEUR", "ERR"))

    def status_lines(self, timeout=1.5):
        """Send STAT and collect the multi-line response. Used to verify the
        port is actually the DAQ board (it answers with RW/RO regs + 'decoded:')."""
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write(b"STAT\n")
            self.s.flush()
            lines = []
            deadline = time.time() + timeout
            while time.time() < deadline:
                ln = self.s.readline().decode("ascii", errors="replace").strip()
                if ln:
                    lines.append(ln)
            return lines

    def set_cic(self, on):
        return self.cmd(f"STRM CIC {'on' if on else 'off'}", ok=("OK STRM", "ERR"))

    def base_setup(self):
        """Board init only -- does NOT start the live stream (opt-in)."""
        self.cmd("WRTE 2 0x01000018")
        for ch, prof in enumerate(NEURON_PROFILES):
            self.cmd(f"NEUR {ch} {prof}")
        self.cmd("NEUR all period 1")
        self.cmd("NEUR all dt 0x8000")

    def start_stream(self, decim, usecic):
        return self.cmd(f"STRM {decim}{' cic' if usecic else ''}",
                        ok=("OK STRM", "ERR"))

    def stop_stream(self):
        return self.cmd("STRM STOP", ok=("OK STRM", "ERR"))

    def uart_capture(self, frames):
        """PCAP <frames> -> 4-channel snapshot. PCAP keeps RW3_DAC_PROGRAM_EN
        set so BRAM channels keep playing during the capture (plain CAPT clears
        it and BRAM would read as noise)."""
        return self._capture(f"PCAP {frames}", frames)

    def set_neuron_param(self, target, param, q16):
        """Live single-param update: writes the config bank and pulses the
        reload. target = 'all' or 0..3; param in a/b/c/d/i/iconst/dt/period."""
        return self.cmd(f"NEUR {target} {param} 0x{q16 & 0xFFFFFFFF:08X}",
                        ok=("OK", "NEUR", "ERR"))

    def program_current(self, samples_ma, cps, hold_last=False):
        """Load an arbitrary current waveform into cur_wave and run the player
        (firmware CURW). samples_ma is an iterable of non-negative mA; cps sets
        the per-sample dwell. hold_last=True plays the samples once and then
        holds the final sample instead of looping. Returns the board's reply
        line ('OK CURW ...' on success)."""
        words = [ma_to_q16_u32(v) for v in samples_ma]
        n = len(words)
        mode = " hold" if hold_last else ""
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write(f"CURW {cps} {n}{mode}\n".encode("ascii"))
            self.s.flush()
            ack = self._readuntil(("CWRD", "ERR"))
            if not ack.startswith("CWRD"):
                return ack or ""
            self.s.write(struct.pack(f"<{n}I", *words))
            self.s.flush()
            return self._readuntil(("OK CURW", "ERR"))

    def stop_current(self):
        """Stop the current player (i_external held; CURP off)."""
        return self.cmd("CURP off", ok=("CURP", "ERR"))

    def program_pulse(self, counts):
        """Set the spike-pulse shape to signed DAC counts via binary PULS."""
        vals = [max(-32768, min(32767, int(round(v)))) for v in counts]
        n = len(vals)
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write(f"PULS bin {n}\n".encode("ascii"))
            self.s.flush()
            ack = self._readuntil(("PBRD", "ERR"))
            if not ack.startswith("PBRD"):
                return ack or ""
            self.s.write(struct.pack(f"<{n}h", *vals))
            self.s.flush()
            return self._readuntil(("PULS", "ERR"))

    def pulse_default(self):
        """Reload the boot-default spike shape (original 7 ns trapezoid)."""
        return self.cmd("PULS default", ok=("PULS", "ERR"))

    def _capture(self, cmd_str, frames):
        """Send a capture command, wait for the FE10CAFE sync, read
        frames*8*4 bytes, and decode to 4-channel int16 arrays (over UART)."""
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write((cmd_str + "\n").encode("ascii"))
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


# ------------------------------------------------------- pulse-shape editor
class PulseEditor(pg.GraphItem):
    """Draggable spike-pulse editor: N nodes at x = 0..N-1, y = DAC counts (full
    signed s16). Drag a node up/down to reshape the pulse; x stays locked to the
    sample index. on_change(ys) fires after every drag. Based on pyqtgraph's
    draggable-GraphItem pattern."""

    def __init__(self, on_change=None):
        self.dragPoint = None
        self.dragIndex = 0
        self.on_change = on_change
        self.ys = np.zeros(1)
        pg.GraphItem.__init__(self)

    def set_values(self, ys):
        self.ys = np.clip(np.asarray(ys, dtype=float),
                          -DAC_FULLSCALE, DAC_FULLSCALE)
        self._push()

    def values(self):
        return [int(round(v)) for v in self.ys]

    def _push(self):
        n = len(self.ys)
        pos = np.column_stack((np.arange(n, dtype=float), self.ys))
        if n > 1:
            adj = np.column_stack((np.arange(n - 1), np.arange(1, n))).astype(int)
        else:
            adj = np.empty((0, 2), dtype=int)
        self.setData(pos=pos, adj=adj, size=14, symbol='o', pxMode=True,
                     pen=pg.mkPen('#4FC3F7', width=2),
                     symbolBrush=pg.mkBrush('#FFB74D'))

    def setData(self, **kwds):
        self.data = kwds
        if 'pos' in self.data:
            npts = self.data['pos'].shape[0]
            self.data['data'] = np.empty(npts, dtype=[('index', int)])
            self.data['data']['index'] = np.arange(npts)
        self.updateGraph()

    def updateGraph(self):
        pg.GraphItem.setData(self, **self.data)

    def mouseDragEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            ev.ignore()
            return
        if ev.isStart():
            pts = self.scatter.pointsAt(ev.buttonDownPos())
            if len(pts) == 0:
                ev.ignore()
                return
            self.dragPoint = pts[0]
            self.dragIndex = int(pts[0].data()[0])
            ev.accept()
        elif ev.isFinish():
            self.dragPoint = None
            return
        else:
            if self.dragPoint is None:
                ev.ignore()
                return
            y = max(-DAC_FULLSCALE, min(DAC_FULLSCALE, float(ev.pos().y())))
            self.ys[self.dragIndex] = y       # x stays pinned to the index
            self._push()
            if self.on_change:
                self.on_change(self.ys)
            ev.accept()


class PulseShapeWindow(QtWidgets.QWidget):
    """Window showing the spike pulse shape (<=4096 signed DAC samples), editable
    by dragging the points. Programs the shaper via PULS; the shaped pulse is one
    of the crossbar inputs (route a Spike source on a DAC to emit it)."""
    done = QtCore.pyqtSignal(bool, str)

    def __init__(self, parent_scope):
        super().__init__()
        self.scope = parent_scope
        self.setWindowTitle("Spike pulse shape editor")
        self.resize(720, 460)
        lay = QtWidgets.QVBoxLayout(self)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#101418")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "DAC", units="counts")
        self.plot.setLabel("bottom", "sample (1 ns @ 1 GS/s)")
        self.plot.setMouseEnabled(x=False, y=False)   # so drags move points only
        self.plot.setYRange(-DAC_FULLSCALE, DAC_FULLSCALE)
        self.plot.addLine(y=0, pen=pg.mkPen('#37474F'))
        self.editor = PulseEditor(on_change=lambda ys: self._info())
        self.plot.addItem(self.editor)
        lay.addWidget(self.plot, stretch=1)

        ctl = QtWidgets.QHBoxLayout()
        ctl.addWidget(QtWidgets.QLabel("samples"))
        self.len_spin = QtWidgets.QSpinBox()
        self.len_spin.setRange(1, PULSE_MAX_SAMPLES)
        self.len_spin.setValue(len(PULSE_DEFAULT))
        self.len_spin.valueChanged.connect(self._on_len)
        ctl.addWidget(self.len_spin)
        self.default_btn = QtWidgets.QPushButton("Load trapezoid")
        self.default_btn.clicked.connect(self._on_default)
        ctl.addWidget(self.default_btn)
        self.zero_btn = QtWidgets.QPushButton("Flatten to 0")
        self.zero_btn.clicked.connect(self._on_zero)
        ctl.addWidget(self.zero_btn)
        ctl.addStretch(1)
        self.prog_btn = QtWidgets.QPushButton("Program pulse")
        self.prog_btn.clicked.connect(self._on_prog)
        ctl.addWidget(self.prog_btn)
        lay.addLayout(ctl)

        self.info = QtWidgets.QLabel()
        self.info.setStyleSheet("color:#9fb3c8; font-size:11px;")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        self.done.connect(self._on_done)
        self.editor.set_values(PULSE_DEFAULT)
        self._info()

    def _on_len(self, n):
        ys = list(self.editor.ys)
        ys = ys[:n] if n <= len(ys) else ys + [0.0] * (n - len(ys))
        self.editor.set_values(ys)
        self._info()

    def _on_default(self):
        self.len_spin.blockSignals(True)
        self.len_spin.setValue(len(PULSE_DEFAULT))
        self.len_spin.blockSignals(False)
        self.editor.set_values(PULSE_DEFAULT)
        self._info()

    def _on_zero(self):
        self.editor.set_values(np.zeros(self.len_spin.value()))
        self._info()

    def _info(self):
        ys = self.editor.values()
        nb = (len(ys) + 3) // 4
        pk = max((abs(v) for v in ys), default=0)
        self.info.setText(
            f"{len(ys)} samples ({len(ys)} ns), nbeats={nb}, peak |{pk}| counts "
            f"({pk * VOLTS_PER_COUNT:.3f} V).  Drag points up/down to edit; "
            f"route a Spike source on a DAC to emit this pulse.")

    def _on_prog(self):
        dac = self.scope.dac
        if not dac:
            self.info.setText("connect a board first")
            return
        counts = self.editor.values()
        self.prog_btn.setEnabled(False)

        def work():
            r = dac.program_pulse(counts)
            self.done.emit(bool(r and not r.startswith("ERR")), r or "(no reply)")
        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, ok, reply):
        self.prog_btn.setEnabled(True)
        self.info.setText(("OK — " if ok else "ERR — ") + (reply or "").strip())


class CurrentSourceWindow(QtWidgets.QWidget):
    """Window showing the current-source waveform programmed into cur_wave RAM.
    Presets: Sine (5 kHz default), Constant current, Step 0 -> constant.
    Amplitude is in mA and can NEVER go negative. Programs the player via CURW;
    route Current source on a DAC to mirror the injected current out for the scope."""
    done = QtCore.pyqtSignal(bool, str)

    def __init__(self, parent_scope):
        super().__init__()
        self.scope = parent_scope
        self._ys = np.zeros(2)
        self._cps = 1
        self.setWindowTitle("Current source (cur_wave player)")
        self.resize(720, 480)
        lay = QtWidgets.QVBoxLayout(self)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#101418")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "current", units="A")   # base unit; values in mA*1e-3
        self.plot.setLabel("bottom", "time", units="s")
        self.curve = self.plot.plot(pen=pg.mkPen('#81C784', width=1.5),
                                    fillLevel=0.0, brush=(129, 199, 132, 60))
        lay.addWidget(self.plot, stretch=1)

        grid = QtWidgets.QGridLayout()
        grid.addWidget(QtWidgets.QLabel("preset"), 0, 0)
        self.kind_cb = QtWidgets.QComboBox()
        self.kind_cb.addItems(CUR_SOURCE_PRESETS)
        self.kind_cb.currentIndexChanged.connect(self._refresh)
        grid.addWidget(self.kind_cb, 0, 1)
        grid.addWidget(QtWidgets.QLabel("amplitude"), 1, 0)
        self.amp_spin = QtWidgets.QDoubleSpinBox()
        self.amp_spin.setRange(0.0, CUR_MAX_MA)            # NEVER negative
        self.amp_spin.setDecimals(3)                       # finely controllable
        self.amp_spin.setSingleStep(0.05)
        self.amp_spin.setValue(10.0)
        self.amp_spin.setSuffix(" mA")
        self.amp_spin.valueChanged.connect(self._refresh)
        grid.addWidget(self.amp_spin, 1, 1)
        grid.addWidget(QtWidgets.QLabel("frequency"), 2, 0)
        self.freq_spin = QtWidgets.QDoubleSpinBox()
        self.freq_spin.setRange(CUR_FREQ_REQUEST_MIN_HZ, CUR_FREQ_REQUEST_MAX_HZ)
        self.freq_spin.setDecimals(3)
        self.freq_spin.setSingleStep(100.0)
        self.freq_spin.setValue(5000.0)
        self.freq_spin.setSuffix(" Hz")
        self.freq_spin.valueChanged.connect(self._refresh)
        grid.addWidget(self.freq_spin, 2, 1)
        lay.addLayout(grid)

        row = QtWidgets.QHBoxLayout()
        self.stop_btn = QtWidgets.QPushButton("Stop player")
        self.stop_btn.clicked.connect(self._on_stop)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        self.prog_btn = QtWidgets.QPushButton("Program current source")
        self.prog_btn.clicked.connect(self._on_prog)
        row.addWidget(self.prog_btn)
        lay.addLayout(row)

        self.info = QtWidgets.QLabel()
        self.info.setStyleSheet("color:#9fb3c8; font-size:11px;")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        self.done.connect(self._on_done)
        self._refresh()

    def _refresh(self, *_):
        kind = self.kind_cb.currentText()
        amp = self.amp_spin.value()
        freq = self.freq_spin.value()
        # Frequency is meaningful only for periodic sine playback.
        self.freq_spin.setEnabled(kind.startswith("Sine"))
        ys, cps, actual = gen_current_wave(kind, amp, freq)
        self._ys, self._cps = ys, cps
        self._kind = kind
        self._actual = actual
        n = len(ys)
        dt = cps / CUR_PLAYER_CLK_HZ
        if kind.startswith("Step"):
            plot_ys = np.concatenate((ys, np.repeat(ys[-1], max(16, n))))
            t = np.arange(plot_ys.size) * dt
        else:
            reps = 3                               # show a few loops for context
            plot_ys = np.tile(ys, reps)
            t = np.arange(plot_ys.size) * dt
        # plot in Amps (mA * 1e-3) so the axis SI-prefixes to mA cleanly
        self.curve.setData(t, plot_ys * 1e-3)
        ymax_ma = max(0.5, float(ys.max()) * 1.15)
        self.plot.setYRange(-0.05 * ymax_ma * 1e-3, ymax_ma * 1e-3)
        # If the physical DAC->ADC loopback is AC-coupled, it may attenuate low
        # frequencies even though the neuron input and DAC source are correct.
        if kind.startswith("Sine") and actual < CUR_LOOPBACK_AC_CORNER_HZ:
            tail = (f"  Note: {format_rate(actual)} is below the ~"
                    f"{CUR_LOOPBACK_AC_CORNER_HZ/1e3:.0f} kHz loopback AC corner; "
                    f"use the Current source DAC route or a DC-coupled readout "
                    f"to inspect it cleanly.")
        elif kind.startswith("Step"):
            tail = "  Programs once, then holds the final constant-current sample."
        elif kind.startswith("Constant"):
            tail = "  Programs a one-sample loop, so the injected current is DC."
        else:
            tail = "  Route Current source on a DAC to view the injected waveform."
        rate_text = format_rate(actual)
        if actual > 0 and (actual <= CUR_SINE_HW_MIN_HZ * 1.001 or
                           actual >= CUR_SINE_HW_MAX_HZ * 0.999):
            rate_text += " (nearest hardware rate)"
        self.info.setText(
            f"{kind}: {n} samples, cps={cps} -> {rate_text}, "
            f"peak {ys.max():.3f} mA (1 mA = 1.0 I-unit, unipolar 0+).{tail}")
        self.scope._set_current_preview(kind, ys, cps, actual, programmed=False)
    def _on_prog(self):
        dac = self.scope.dac
        if not dac:
            self.info.setText("connect a board first")
            return
        ys, cps = self._ys, self._cps
        self.prog_btn.setEnabled(False)

        def work():
            r = dac.program_current(ys, cps, hold_last=self._kind.startswith("Step"))
            self.done.emit(bool(r and r.startswith("OK")), r or "(no reply)")
        threading.Thread(target=work, daemon=True).start()

    def _on_stop(self):
        dac = self.scope.dac
        if not dac:
            self.info.setText("connect a board first")
            return

        def work():
            r = dac.stop_current()
            self.done.emit(bool(r and not r.startswith("ERR")), r or "(no reply)")
        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, ok, reply):
        self.prog_btn.setEnabled(True)
        self.info.setText(("OK — " if ok else "ERR — ") + (reply or "").strip())


        if ok:
            self.scope._set_current_preview(
                getattr(self, "_kind", self.kind_cb.currentText()),
                self._ys, self._cps, getattr(self, "_actual", 0.0),
                programmed=True)


# --------------------------------------------------------------- GUI
class ScopeWindow(QtWidgets.QMainWindow):
    captured = QtCore.pyqtSignal(object)      # emits {ch: int16[]} from worker
    collected = QtCore.pyqtSignal(object)     # emits {ch: int16[]} burst-over-eth
    stat_result = QtCore.pyqtSignal(bool, str)  # board-verify result from worker
    neuron_done = QtCore.pyqtSignal(str, bool)  # (target, ok) program-neuron result
    dac_done = QtCore.pyqtSignal(int, bool, str)  # (ch, ok, detail) program-DAC result
    autosampled = QtCore.pyqtSignal(object)   # emits {ch: int16[], '_cov'} each auto-sample

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
        self._cur_win = None        # CurrentSourceWindow (lazily created)
        self._pulse_win = None      # PulseShapeWindow (lazily created)
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

        # ---- control panel ----------------------------------------------------
        PANEL_W = 430
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(PANEL_W)
        root.addWidget(scroll)
        panel = QtWidgets.QWidget()
        scroll.setWidget(panel)
        outer = QtWidgets.QVBoxLayout(panel)

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
        self.stat_btn = QtWidgets.QPushButton("STAT (verify board)")
        self.stat_btn.clicked.connect(self._on_stat)
        cg.addWidget(QtWidgets.QLabel("COM"), 0, 0)
        cg.addWidget(self.port_cb, 0, 1)
        cg.addWidget(refresh, 0, 2)
        cg.addWidget(self.connect_btn, 1, 0, 1, 3)
        cg.addWidget(self.stat_btn, 2, 0, 1, 3)
        self.conn_lbl = QtWidgets.QLabel("not connected")
        self.conn_lbl.setStyleSheet("color:#E57373;")
        self.conn_lbl.setWordWrap(True)
        cg.addWidget(self.conn_lbl, 3, 0, 1, 3)
        outer.addWidget(conn)

        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs, stretch=1)

        neuron_tab = QtWidgets.QWidget()
        neuron_lay = QtWidgets.QVBoxLayout(neuron_tab)
        xbar_tab = QtWidgets.QWidget()
        xbar_lay = QtWidgets.QVBoxLayout(xbar_tab)
        capture_tab = QtWidgets.QWidget()
        capture_lay = QtWidgets.QVBoxLayout(capture_tab)
        wave_tab = QtWidgets.QWidget()
        wave_lay = QtWidgets.QVBoxLayout(wave_tab)
        self.tabs.addTab(neuron_tab, "Neuron")
        self.tabs.addTab(xbar_tab, "XBAR")
        self.tabs.addTab(capture_tab, "Capture")
        self.tabs.addTab(wave_tab, "Waveforms")

        left = xbar_lay
        right = neuron_lay

        cur_box = QtWidgets.QGroupBox("Current source")
        cur_lay = QtWidgets.QVBoxLayout(cur_box)
        self.cur_preview = pg.PlotWidget()
        self.cur_preview.setFixedHeight(120)
        self.cur_preview.setBackground("#101418")
        self.cur_preview.showGrid(x=True, y=True, alpha=0.18)
        self.cur_preview.setLabel("left", "mA")
        self.cur_preview.setLabel("bottom", "s")
        self.cur_preview_curve = self.cur_preview.plot(
            pen=pg.mkPen("#81C784", width=1.3),
            fillLevel=0.0, brush=(129, 199, 132, 45))
        cur_lay.addWidget(self.cur_preview)
        self.cur_preview_info = QtWidgets.QLabel("no current source staged")
        self.cur_preview_info.setStyleSheet("color:#9fb3c8; font-size:11px;")
        self.cur_preview_info.setWordWrap(True)
        cur_lay.addWidget(self.cur_preview_info)
        self.cur_preview_btn = QtWidgets.QPushButton("Open current editor")
        self.cur_preview_btn.setToolTip("Program the cur_wave current source "
                                        "(sine / zero / step; mA, never negative)")
        self.cur_preview_btn.clicked.connect(self._open_current_window)
        cur_lay.addWidget(self.cur_preview_btn)
        right.addWidget(cur_box)

        # per-channel source + neuron profile. The radio/profile only STAGE a
        # selection; nothing reaches the board until the per-DAC "Program"
        # button commits it (NSRC + NEUR), then the status line confirms.
        self.src_cbs, self.prof_cbs = [], []
        self.dac_btns, self.dac_status = [], []
        initial = args.initial if args.initial in SOURCE_LABELS else "DDS"
        for ch in range(4):
            box = QtWidgets.QGroupBox(f"DAC{ch}")
            g = QtWidgets.QGridLayout(box)
            src = QtWidgets.QComboBox()
            src.addItems(SOURCE_LABELS)
            src.setCurrentText(initial)
            src.setToolTip("16:4 DAC crossbar (reg17): route any source to this "
                           "DAC. One combo means one legal source per output.")
            src.currentIndexChanged.connect(self._refresh_xbar_preview)
            src.currentIndexChanged.connect(self._refresh_xbar_profile_enable)
            g.addWidget(QtWidgets.QLabel("source"), 0, 0)
            g.addWidget(src, 0, 1, 1, 2)
            self.src_cbs.append(src)
            prof = QtWidgets.QComboBox()
            prof.addItems(NEURON_PROFILES)
            prof.setCurrentIndex(ch % len(NEURON_PROFILES))
            prof.currentIndexChanged.connect(self._refresh_xbar_preview)
            g.addWidget(QtWidgets.QLabel("profile"), 1, 0)
            g.addWidget(prof, 1, 1, 1, 2)
            self.prof_cbs.append(prof)
            btn = QtWidgets.QPushButton("Confirm route")
            btn.clicked.connect(self._make_program_dac_cb(ch))
            g.addWidget(btn, 2, 0, 1, 3)
            self.dac_btns.append(btn)
            st = QtWidgets.QLabel("not programmed")
            st.setStyleSheet("color:#9fb3c8; font-size:11px;")
            st.setWordWrap(True)
            g.addWidget(st, 3, 0, 1, 3)
            self.dac_status.append(st)
            left.addWidget(box)

        xprev = QtWidgets.QGroupBox("All DAC outputs")
        xprev_lay = QtWidgets.QGridLayout(xprev)
        self.xbar_preview = []
        for ch in range(4):
            src_lbl = QtWidgets.QLabel(initial)
            src_lbl.setAlignment(QtCore.Qt.AlignCenter)
            src_lbl.setStyleSheet("background:#1b242d; color:#d7e3ef; "
                                  "border:1px solid #334657; padding:6px;")
            arrow = QtWidgets.QLabel("=>")
            arrow.setAlignment(QtCore.Qt.AlignCenter)
            dac_lbl = QtWidgets.QLabel(f"DAC{ch}")
            dac_lbl.setAlignment(QtCore.Qt.AlignCenter)
            dac_lbl.setStyleSheet(f"background:{CH_COLORS[ch]}; color:#101418; "
                                  "font-weight:bold; padding:6px;")
            xprev_lay.addWidget(src_lbl, ch, 0)
            xprev_lay.addWidget(arrow, ch, 1)
            xprev_lay.addWidget(dac_lbl, ch, 2)
            self.xbar_preview.append(src_lbl)
        left.addWidget(xprev)
        left.addStretch(1)

        # neuron simulation speed (all neurons)
        nb = QtWidgets.QGroupBox("Neuron sim speed (all)")
        ng = QtWidgets.QHBoxLayout(nb)
        self.dt_cb = QtWidgets.QComboBox()
        self.dt_cb.addItems([lbl for lbl, _ in NEURON_DT_OPTIONS])
        self.dt_cb.setCurrentIndex(NEURON_DT_DEFAULT)
        self.dt_cb.currentIndexChanged.connect(self._on_dt)
        ng.addWidget(QtWidgets.QLabel("dt"))
        ng.addWidget(self.dt_cb)
        right.addWidget(nb)

        prof_box = QtWidgets.QGroupBox("Per-neuron profiles")
        prof_grid = QtWidgets.QGridLayout(prof_box)
        self.neuron_profile_cbs = {}
        self.neuron_profile_btns = {}
        self.neuron_status = {}
        for n in range(4):
            key = str(n)
            prof_grid.addWidget(QtWidgets.QLabel(f"neuron {n}"), n, 0)
            cb = QtWidgets.QComboBox()
            cb.addItems(NEURON_PROFILES)
            cb.setCurrentIndex(n % len(NEURON_PROFILES))
            prof_grid.addWidget(cb, n, 1)
            btn = QtWidgets.QPushButton("Program")
            btn.clicked.connect(self._make_prog_profile_cb(key))
            prof_grid.addWidget(btn, n, 2)
            st = QtWidgets.QLabel("-")
            st.setStyleSheet("color:#9fb3c8; font-size:10px;")
            prof_grid.addWidget(st, n, 3)
            self.neuron_profile_cbs[key] = cb
            self.neuron_profile_btns[key] = btn
            self.neuron_status[key] = st
        right.addWidget(prof_box)

        # live Izhikevich parameters: tweak a/b/c/d/I and the neuron is
        # reprogrammed (config bank + reload pulse) immediately, so the loopback
        # spike pattern responds in real time.
        # Set up the params (or load a profile), then hit "Program neurons" to
        # apply them in one shot: each NEUR write resets + reloads the target,
        # so after Program the neuron runs fresh with exactly these values.
        pb = QtWidgets.QGroupBox("Neuron params")
        pg_ = QtWidgets.QGridLayout(pb)
        pg_.addWidget(QtWidgets.QLabel("load"), 0, 0)
        self.np_loadprof = QtWidgets.QComboBox()
        self.np_loadprof.addItems(["load profile…"] + NEURON_PROFILES)
        self.np_loadprof.currentIndexChanged.connect(self._on_load_profile_values)
        pg_.addWidget(self.np_loadprof, 0, 1, 1, 2)
        self.np_spins = {}
        for row, (param, label, lo, hi, dflt, step, dec) in enumerate(
                NEURON_PARAM_SPECS, start=1):
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(dec)
            sp.setValue(dflt)
            pg_.addWidget(QtWidgets.QLabel(label), row, 0)
            pg_.addWidget(sp, row, 1, 1, 2)
            self.np_spins[param] = sp
        # one explicit Program button per neuron (+ all) so it's unambiguous
        # which target gets the current spinbox values; each button reports
        # OK/ERR back in its own status line.
        brow = len(NEURON_PARAM_SPECS) + 1
        btn_box = QtWidgets.QHBoxLayout()
        btn_box.setSpacing(3)
        self.np_btns = {}
        for tgt in ["0", "1", "2", "3", "all"]:
            b = QtWidgets.QPushButton(tgt)
            b.setMaximumWidth(46)
            b.setToolTip(f"Program neuron {tgt} with the params above")
            b.clicked.connect(self._make_prog_neuron_cb(tgt))
            btn_box.addWidget(b)
            self.np_btns[tgt] = b
        pg_.addWidget(QtWidgets.QLabel("program →"), brow, 0)
        pg_.addLayout(btn_box, brow, 1, 1, 2)
        self.np_status = QtWidgets.QLabel("—")
        self.np_status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        pg_.addWidget(self.np_status, brow + 1, 0, 1, 3)
        right.addWidget(pb)

        # Spike pulse source editor; the current-source editor lives beside the
        # neuron controls because it directly drives i_external.
        ed = QtWidgets.QGroupBox("Source editors")
        eg = QtWidgets.QVBoxLayout(ed)
        self.pulse_win_btn = QtWidgets.QPushButton("Pulse shape…")
        self.pulse_win_btn.setToolTip("Show/edit the spike pulse shape by "
                                      "dragging points (<=4096 signed samples)")
        self.pulse_win_btn.clicked.connect(self._open_pulse_window)
        eg.addWidget(self.pulse_win_btn)
        wave_lay.addWidget(ed)

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
        wave_lay.addWidget(wf)

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
        self.capt_frames = QtWidgets.QComboBox()
        self.capt_frames.addItems([f"{n} frames" for n in CAPT_FRAME_OPTIONS])
        self.capt_frames.setCurrentText("512 frames")
        # one-shot burst-over-Ethernet snapshot (BCAP+BRDO): fresh full-rate
        # capture of the selected MB/chip, far more reliable than the cyclic
        # continuous stream. Size is selectable via the combo.
        self.collect_mb_cb = QtWidgets.QComboBox()
        self.collect_mb_cb.addItems([lbl for _, lbl in COLLECT_SIZE_OPTIONS])
        self.collect_mb_cb.setCurrentIndex(COLLECT_SIZE_DEFAULT_IDX)
        self.collect_btn = QtWidgets.QPushButton("Collect Ethernet")
        self.collect_btn.clicked.connect(self._on_collect_eth)
        # Auto-Sample: opt-in repeated one-shot bursts (1/s) shown in the main
        # plots; NOT saved and NOT the cyclic UDP stream.
        self.stream_btn = QtWidgets.QPushButton("Start Auto-Sample")
        self.stream_btn.setCheckable(True)
        self.stream_btn.clicked.connect(self._on_autosample_toggle)
        og.addWidget(self.cic_chk, 0, 0, 1, 2)
        og.addWidget(self.auto_chk, 1, 0)
        og.addWidget(self.trig_chk, 1, 1)
        og.addWidget(self.rb_time, 2, 0)
        og.addWidget(self.rb_fft, 2, 1)
        og.addWidget(self.run_btn, 3, 0, 1, 2)
        og.addWidget(self.capt_frames, 4, 0)
        og.addWidget(self.capt_btn, 4, 1)
        og.addWidget(self.collect_mb_cb, 5, 0)
        og.addWidget(self.collect_btn, 5, 1)
        og.addWidget(self.stream_btn, 6, 0, 1, 2)
        capture_lay.addWidget(opt)

        left.addStretch(1)
        right.addStretch(1)
        capture_lay.addStretch(1)
        wave_lay.addStretch(1)
        self.status = QtWidgets.QLabel("Connect to a COM port to begin.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        outer.addWidget(self.status)

        self._refresh_xbar_profile_enable()
        self._refresh_xbar_preview()
        ys, cps, actual = gen_current_wave("Sine", 10.0, 5000.0)
        self._set_current_preview("Sine", ys, cps, actual, programmed=False)
        self._apply_view_ranges()
        self._set_controls_enabled(False)
        self.captured.connect(self._show_capture)
        self.collected.connect(self._on_collected)
        self.stat_result.connect(self._show_stat)
        self.neuron_done.connect(self._on_neuron_done)
        self.dac_done.connect(self._on_dac_done)
        self.autosampled.connect(self._on_autosampled)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update)
        self.timer.start(int(1000.0 / args.fps))
        # Auto-Sample cadence timer (started by the button)
        self._autosample_busy = False
        self.autosample_timer = QtCore.QTimer(self)
        self.autosample_timer.setInterval(AUTOSAMPLE_INTERVAL_MS)
        self.autosample_timer.timeout.connect(self._on_autosample_tick)

    def _set_current_preview(self, kind, ys, cps, actual, programmed=False):
        if not hasattr(self, "cur_preview_curve"):
            return
        ys = np.asarray(ys, dtype=np.float64)
        if ys.size == 0:
            ys = np.zeros(1)
        dt = max(1, int(cps)) / CUR_PLAYER_CLK_HZ
        if kind.startswith("Step"):
            plot_ys = np.concatenate((ys, np.repeat(ys[-1], max(16, ys.size))))
        else:
            reps = 3 if ys.size > 1 else 16
            plot_ys = np.tile(ys, reps)
        t = np.arange(plot_ys.size) * dt
        self.cur_preview_curve.setData(t, plot_ys)
        ymax = max(1.0, float(ys.max()) * 1.15)
        self.cur_preview.setYRange(-0.05 * ymax, ymax)
        state = "programmed" if programmed else "staged"
        freq = "one-shot hold" if kind.startswith("Step") else (
            "DC" if actual <= 0 else format_rate(actual))
        self.cur_preview_info.setText(
            f"{state}: {kind}, {ys.size} samples, cps={int(cps)}, "
            f"{freq}, peak {float(ys.max()):.3f} mA")

    def _refresh_xbar_profile_enable(self, *_):
        if not hasattr(self, "src_cbs"):
            return
        controls_on = getattr(self, "_controls_enabled", True)
        for ch, cb in enumerate(self.prof_cbs):
            label = self.src_cbs[ch].currentText()
            cb.setEnabled(controls_on and
                          (label.startswith("Spike ") or
                           label.startswith("Monitor ")))

    def _refresh_xbar_preview(self, *_):
        if not hasattr(self, "xbar_preview"):
            return
        for ch, lbl in enumerate(self.xbar_preview):
            src = self.src_cbs[ch].currentText()
            prof = self.prof_cbs[ch].currentText()
            if src.startswith("Spike ") or src.startswith("Monitor "):
                src = f"{src}\n{prof}"
            lbl.setText(src)

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
        # tear down any previous session
        if self.tap:
            try:
                self.tap.close()
            except Exception:  # noqa: BLE001
                pass
            self.tap = None
        if self.dac:
            try:
                self.dac.close()
            except Exception:  # noqa: BLE001
                pass
            self.dac = None
        # ---- UART (required): everything in the panel works over this ----
        try:
            self.dac = DacControl(port)
            self.dac.base_setup()
            for ch in range(4):
                self.dac.set_source(ch, self.args.initial)
        except Exception as e:  # noqa: BLE001
            self.conn_lbl.setText(f"UART connect failed: {e}")
            self.conn_lbl.setStyleSheet("color:#E57373;")
            self.dac = None
            return
        self.connect_btn.setText("Reconnect")
        self._set_controls_enabled(True)
        # reflect the source each DAC was set to on connect
        for ch in range(4):
            self.dac_status[ch].setText(f"OK — {self.args.initial}")
            self.dac_status[ch].setStyleSheet("color:#81C784; font-size:11px;")
        # Acquisition is OPT-IN: nothing samples until the user presses
        # Auto-Sample. Use "Collect Ethernet" for a single saved snapshot.
        self.tap = None
        self.autosample_timer.stop()
        self._autosample_busy = False
        if self.stream_btn.isChecked():
            self.stream_btn.blockSignals(True)
            self.stream_btn.setChecked(False)
            self.stream_btn.blockSignals(False)
        self.stream_btn.setText("Start Auto-Sample")
        self.conn_lbl.setText(f"connected {port} (idle)")
        self.conn_lbl.setStyleSheet("color:#81C784;")
        self.status.setText("Connected. Use Collect Ethernet for a saved "
                            "snapshot, or Auto-Sample for 1/s live view.")

    def _set_controls_enabled(self, on):
        self._controls_enabled = on
        for w in (self.wf_btn, self.cic_chk, self.capt_btn, self.collect_btn,
                  self.collect_mb_cb, self.stream_btn, self.dt_cb,
                  self.np_loadprof, self.cur_preview_btn):
            w.setEnabled(on)
        for b in self.np_btns.values():
            b.setEnabled(on)
        for b in self.neuron_profile_btns.values():
            b.setEnabled(on)
        for cb in self.neuron_profile_cbs.values():
            cb.setEnabled(on)
        for sp in self.np_spins.values():
            sp.setEnabled(on)
        for cb in self.src_cbs:
            cb.setEnabled(on)
        for cb in self.prof_cbs:
            cb.setEnabled(on)
        for b in self.dac_btns:
            b.setEnabled(on)
        self._refresh_xbar_profile_enable()

    def _show_stat(self, is_daq, health):
        port = self.dac.port if self.dac else self.port_cb.currentText()
        if is_daq:
            self.conn_lbl.setText(f"DAQ board OK on {port}  [{health}]")
            self.conn_lbl.setStyleSheet("color:#81C784;")
        else:
            self.conn_lbl.setText("no DAQ response on this port "
                                  "(wrong COM port / board down)")
            self.conn_lbl.setStyleSheet("color:#E57373;")

    # ---- callbacks (UART off the GUI thread) ----
    def _bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _make_program_dac_cb(self, ch):
        def cb():
            self._program_dac(ch)
        return cb

    # ---- programmable-source editor windows ----
    def _open_current_window(self):
        if self._cur_win is None:
            self._cur_win = CurrentSourceWindow(self)
        self._cur_win.show()
        self._cur_win.raise_()
        self._cur_win.activateWindow()

    def _open_pulse_window(self):
        if self._pulse_win is None:
            self._pulse_win = PulseShapeWindow(self)
        self._pulse_win.show()
        self._pulse_win.raise_()
        self._pulse_win.activateWindow()

    def _program_dac(self, ch):
        """Commit DACn's staged selection: route the picked source (NSRC) and,
        when that source is a spike/current-monitor of a neuron, also program
        that neuron's profile (NEUR); report OK/ERR on the per-DAC status line."""
        if not self.dac:
            return
        label = self.src_cbs[ch].currentText()
        profile = self.prof_cbs[ch].currentText()
        # Spike/Monitor sources name a neuron index -> (re)program that neuron.
        neuron_idx = None
        if label.startswith("Spike ") or label.startswith("Monitor "):
            neuron_idx = int(label.split()[-1])
        self.dac_btns[ch].setEnabled(False)
        self.dac_status[ch].setText(f"programming {label}…")
        self.dac_status[ch].setStyleSheet("color:#FFB74D; font-size:11px;")

        def work():
            ok = True
            r = self.dac.set_source(ch, label)
            if not r or r.startswith("ERR"):
                ok = False
            detail = label
            if neuron_idx is not None:
                r2 = self.dac.set_neuron(neuron_idx, profile)
                if not r2 or r2.startswith("ERR"):
                    ok = False
                detail = f"{label} (neuron {neuron_idx}: {profile})"
            self.dac_done.emit(ch, ok, detail)
        self._bg(work)

    def _on_dac_done(self, ch, ok, detail):
        self.dac_btns[ch].setEnabled(True)
        if ok:
            self.dac_status[ch].setText(f"OK — {detail}")
            self.dac_status[ch].setStyleSheet("color:#81C784; font-size:11px;")
        else:
            self.dac_status[ch].setText(f"ERR — {detail} not set")
            self.dac_status[ch].setStyleSheet("color:#E57373; font-size:11px;")

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
        words, loop_frames = gen_waveform(kind, period, width, vlo, vhi)
        loop_samples = loop_frames * BRAM_FRAME_SAMPLES
        freq_hz = 1.0e9 / period
        self.status.setText(
            f"programming {kind} {freq_hz/1e3:.3f} kHz ({period} ns) to {target}: "
            f"seamless {loop_samples} smp / {loop_samples // period} periods")

        def work():
            for ch in chans:
                self.dac.prog(ch, words, loop_frames)
        self._bg(work)

    def _on_dt(self, idx):
        if self.dac:
            dt = NEURON_DT_OPTIONS[idx][1]
            self._bg(lambda: self.dac.set_neuron_dt(dt))

    # ---- neuron parameters (explicit Program button) ----
    def _on_load_profile_values(self, idx):
        """Stage a profile's values into the spinboxes (does NOT program -- hit
        'Program neurons' to apply)."""
        if idx <= 0:
            return
        name = self.np_loadprof.itemText(idx)
        vals = NEURON_PROFILE_VALUES.get(name)
        if not vals:
            return
        for p, sp in self.np_spins.items():
            sp.setValue(vals[p])
        self.np_loadprof.blockSignals(True)
        self.np_loadprof.setCurrentIndex(0)
        self.np_loadprof.blockSignals(False)
        self.status.setText(f"staged profile '{name}' -- press Program neurons")

    def _make_prog_neuron_cb(self, target):
        def cb():
            self._program_neuron(target)
        return cb

    def _make_prog_profile_cb(self, target):
        def cb():
            self._program_neuron_profile(target)
        return cb

    def _program_neuron_profile(self, target):
        if not self.dac:
            return
        profile = self.neuron_profile_cbs[target].currentText()
        btn = self.neuron_profile_btns.get(target)
        if btn:
            btn.setEnabled(False)
        st = self.neuron_status.get(target)
        if st:
            st.setText("...")
            st.setStyleSheet("color:#FFB74D; font-size:10px;")
        self.status.setText(f"programming neuron {target} profile {profile}")

        def work():
            r = self.dac.set_neuron(target, profile)
            ok = bool(r and not r.startswith("ERR"))
            self.neuron_done.emit(target, ok)
        self._bg(work)

    def _program_neuron(self, target):
        if not self.dac:
            return
        vals = [(p, self.np_spins[p].value()) for p, *_ in NEURON_PARAM_SPECS]
        labels = ", ".join(f"{p}={v:g}" for p, v in vals)
        btn = self.np_btns.get(target)
        if btn:
            btn.setEnabled(False)
        self.np_status.setText(f"programming neuron {target}…")
        self.np_status.setStyleSheet("color:#FFB74D; font-size:11px;")
        self.status.setText(f"programming neuron {target}: {labels}")

        def work():
            ok = True
            for param, val in vals:
                r = self.dac.set_neuron_param(target, param, izh_to_q16(val))
                if not r or r.startswith("ERR"):
                    ok = False
            self.neuron_done.emit(target, ok)
        self._bg(work)

    def _on_neuron_done(self, target, ok):
        btn = self.np_btns.get(target)
        if btn:
            btn.setEnabled(True)
        pbtn = self.neuron_profile_btns.get(target)
        if pbtn:
            pbtn.setEnabled(True)
        if target == "all":
            for label in self.neuron_status.values():
                label.setText("OK" if ok else "ERR")
                label.setStyleSheet(("color:#81C784;" if ok else "color:#E57373;") +
                                    " font-size:10px;")
        else:
            pst = self.neuron_status.get(target)
            if pst:
                pst.setText("OK" if ok else "ERR")
                pst.setStyleSheet(("color:#81C784;" if ok else "color:#E57373;") +
                                  " font-size:10px;")
        if ok:
            self.np_status.setText(f"neuron {target}: OK — programmed")
            self.np_status.setStyleSheet("color:#81C784; font-size:11px;")
        else:
            self.np_status.setText(f"neuron {target}: ERR (no/!OK response)")
            self.np_status.setStyleSheet("color:#E57373; font-size:11px;")

    def _on_stat(self):
        if not self.dac:
            self.conn_lbl.setText("connect first")
            self.conn_lbl.setStyleSheet("color:#E57373;")
            return
        self.conn_lbl.setText("checking board...")
        self.conn_lbl.setStyleSheet("color:#FFB74D;")

        def work():
            lines = self.dac.status_lines()
            blob = "\n".join(lines)
            is_daq = any(l.startswith("RW0") for l in lines) and "decoded:" in blob
            health = [k for k in ("qpll_locked", "tx_ready", "rx_ready")
                      if k in blob]
            self.stat_result.emit(is_daq, ", ".join(health) or "link not ready")
        self._bg(work)

    def _on_capture(self):
        if not self.dac:
            return
        frames = CAPT_FRAME_OPTIONS[self.capt_frames.currentIndex()]
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
        sub = save_capture(self.args.capture_dir, "uart", chans, 1.0e9)
        where = f"  -> {sub}" if sub else "  (save FAILED)"
        self.status.setText(f"UART capture: {len(chans[0])} samples/ch.{where}")

    # ---- one-shot burst capture over Ethernet (BCAP + BRDO) ----
    def _on_collect_eth(self):
        if not self.dac:
            return
        self.collect_btn.setEnabled(False)
        # auto-sample and a manual collect both drive the DMA + UDP port, so
        # pause auto-sampling for the duration and resume it afterwards.
        self._resume_autosample = self.autosample_timer.isActive()
        if self._resume_autosample:
            self.autosample_timer.stop()
        # free the UDP socket the live stream holds so the burst readout can use it
        self._resume_after_collect = self.tap is not None
        if self.tap:
            self.tap.close()
            self.tap = None
        nbytes, lbl = COLLECT_SIZE_OPTIONS[self.collect_mb_cb.currentIndex()]
        self._last_collect_bytes = nbytes
        self.status.setText(f"collecting {lbl} burst over Ethernet...")
        self._bg(lambda: self.collected.emit(self._burst_collect(nbytes)))

    def _burst_collect(self, nbytes):
        """One-shot BCAP+BRDO -> {ch: int16[], '_cov': fraction} or {'_err': str}.
        Fires a fresh full-rate capture of `nbytes` bytes/chip (sent as
        BCAP <KB>k) and drains it over UDP on the same local port the live
        stream uses (now released). Reuses burst_capture.Reassembler for
        offset-bitmap (dedup'd) coverage."""
        try:
            from burst_capture import Reassembler, decode_chip, parse_brdo_request
        except Exception as exc:  # noqa: BLE001
            return {"_err": f"burst_capture import failed: {exc}"}
        bpc = nbytes
        kb = nbytes // 1024
        asm = None
        try:
            asm = Reassembler(self.args.board_ip, self.args.cmd_port,
                              self.args.local_ip, self.args.local_port, bpc)
        except OSError as exc:
            return {"_err": (f"UDP bind failed on {self.args.local_ip}:"
                             f"{self.args.local_port}: {exc}")}
        try:
            # ensure the DMA is free: a cyclic stream and a one-shot burst
            # can't share the DMA, so stop streaming before BCAP (no-op if off).
            self.dac.stop_stream()
            if not asm.register(timeout=2.0):
                return {"_err": "BRST registration timed out (no BRST_READY from A53)"}
            bcap = self.dac.cmd(f"BCAP {kb}k", ok=("OK BCAP", "ERR"))
            if not bcap.startswith("OK BCAP"):
                return {"_err": f"BCAP failed: {bcap or '(no UART reply)'}"}
            brdo = self.dac.cmd("BRDO", ok=("OK BRDO", "ERR"))
            req = parse_brdo_request(brdo)
            if not brdo.startswith("OK BRDO") or req is None:
                return {"_err": f"BRDO failed: {brdo or '(no UART reply)'}"}
            asm.set_request_id(req)
            deadline = time.time() + max(8.0, (2.0 * bpc / 70.0e6) + 2.0)
            while time.time() < deadline:
                if asm.complete():
                    break
                time.sleep(0.05)
            if not asm.complete():
                return {
                    "_err": (f"UDP drain timed out for request {req}: "
                             f"chip0 {100 * asm.coverage(0):.1f}%, "
                             f"chip1 {100 * asm.coverage(1):.1f}% coverage")
                }
            chans = {}
            chans.update(decode_chip(asm.buf[0], 0))
            chans.update(decode_chip(asm.buf[1], 2))
            chans["_cov"] = min(asm.coverage(0), asm.coverage(1))
        except Exception as exc:  # noqa: BLE001
            return {"_err": f"Ethernet collect exception: {exc}"}
        finally:
            if asm is not None:
                asm.close()
        return chans

    def _on_collected(self, chans):
        self.collect_btn.setEnabled(True)
        # resume auto-sampling if it was running before the manual collect
        if getattr(self, "_resume_autosample", False) and self.dac \
                and self.stream_btn.isChecked():
            self._autosample_busy = False
            self.autosample_timer.start()
        self._resume_autosample = False
        # resume the live stream only if it was running before the collect
        if getattr(self, "_resume_after_collect", False) and self.dac:
            self.dac.start_stream(self.args.decim, self.args.cic)
            try:
                self.tap = StreamTap(self.args.board_ip, self.args.cmd_port,
                                     self.args.local_ip, self.args.local_port,
                                     self.args.window, self.args.rcvbuf)
            except OSError:
                self.tap = None
        if chans is None:
            self.status.setText("Collect Ethernet failed (no result).")
            return
        if isinstance(chans, dict) and "_err" in chans:
            self.status.setText(f"Collect Ethernet failed: {chans['_err']}")
            return
        cov = chans.pop("_cov", 1.0)
        self._show_burst(chans, cov)

    # ---- auto-sample: a fresh one-shot burst every second, into the main
    #      plots, NOT saved (and NOT the cyclic UDP stream) ----
    def _on_autosample_toggle(self, checked):
        if not self.dac:
            self.stream_btn.setChecked(False)
            return
        if checked:
            self.stream_btn.setText("Stop Auto-Sample")
            self.status.setText("auto-sampling (1/s)...")
            self._autosample_busy = False
            self.autosample_timer.start()
            self._on_autosample_tick()        # take the first sample now
        else:
            self.autosample_timer.stop()
            self.stream_btn.setText("Start Auto-Sample")
            self.status.setText("auto-sample stopped")

    def _on_autosample_tick(self):
        # skip if the board's gone or the previous grab hasn't returned yet
        if not self.dac or self._autosample_busy:
            return
        self._autosample_busy = True
        self._bg(lambda: self.autosampled.emit(
            self._burst_collect(AUTOSAMPLE_BYTES)))

    def _on_autosampled(self, chans):
        self._autosample_busy = False
        if not self.stream_btn.isChecked():
            return                            # stopped while a grab was in flight
        if chans is None:
            self.status.setText("auto-sample: no result (retrying)")
            return
        if isinstance(chans, dict) and "_err" in chans:
            self.status.setText(f"auto-sample: {chans['_err']} (retrying)")
            return
        cov = chans.pop("_cov", 1.0)
        # show a 4x-wider window than the default scope span (the burst holds
        # plenty of samples; this just displays more of them)
        self._render_main(chans, 1.0e9, span=4 * self.args.time_span)
        self.status.setText(
            f"auto-sample 1/s: {len(chans[0])} samples/ch @ 1 GS/s  "
            f"coverage {100 * cov:.0f}%  (not saved)")

    def _render_main(self, chans, fs, span=None):
        """Draw a captured {ch: int16[]} set into the 4 main plots, honoring
        the Time/FFT and Autoscale toggles (full rate, no decimation)."""
        if span is None:
            span = self.args.time_span
        for ch in range(4):
            full = chans[ch].astype(np.float64)
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

    def _show_burst(self, chans, cov):
        nbytes = getattr(self, "_last_collect_bytes", 1 << 20)
        size_lbl = (f"{nbytes >> 20} MB" if nbytes >= (1 << 20)
                    else f"{nbytes >> 10} KB")
        win = pg.GraphicsLayoutWidget()
        win.setWindowTitle(f"Ethernet burst -- {size_lbl}/chip")
        win.setBackground("#101418")
        win.resize(1000, 720)
        for ch in range(4):
            p = win.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            p.setDownsampling(auto=True, mode="peak")   # big arrays render smoothly
            p.setClipToView(True)
            y = chans[ch].astype(np.float32) * VOLTS_PER_COUNT
            p.plot(np.arange(len(y)), y, pen=pg.mkPen(CH_COLORS[ch], width=1.0))
        win.addLabel(f"BCAP {size_lbl}/chip @ 1 GS/s  (x = ns; "
                     f"coverage {100 * cov:.1f}%)", row=4, col=0)
        win.show()
        self._popup = win
        sub = save_capture(self.args.capture_dir, "eth", chans, 1.0e9,
                           coverage=cov, bytes_per_chip=nbytes)
        where = f"  -> {sub}" if sub else "  (save FAILED)"
        self.status.setText(f"Ethernet burst: {len(chans[0])} samples/ch, "
                            f"coverage {100 * cov:.1f}%.{where}")

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
        self.autosample_timer.stop()
        for w in (self._cur_win, self._pulse_win):
            if w is not None:
                w.close()
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
    ap.add_argument("--initial", default="DDS", choices=SOURCE_LABELS)
    ap.add_argument("--cic", action="store_true")
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--capture-dir",
                    default=os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "captures"),
                    help="directory captures are saved under (one subdir each)")
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
