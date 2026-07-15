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
    dropdown (+ a neuron-profile dropdown), then hit "Confirm route" to commit:
    the dropdowns only stage a choice; the button sends NSRC (+ the neuron
    profile when the source is a Spike/Monitor) and the per-DAC status line
    confirms "OK — <source>" once the board is reconfigured. The XBAR tab draws
    the 16:4 crossbar as lines from each source to its DAC; a route is drawn
    SOLID only once it is actually applied (a staged-but-unconfirmed pick shows
    as a faint dashed line), so the picture never claims a switch the board
    hasn't taken.
  - Neuron params: a/b/c/d/I spinboxes (physical Izhikevich units). Set them
    (or "load profile" to stage a built-in OR custom profile), then hit the
    per-neuron "0..3" button (or "all") to apply to that target -- each NEUR
    write resets + reloads the target, so it runs fresh with exactly these
    values. "Save…" stores the current a/b/c/d/I as a named CUSTOM profile
    (persisted to ~/.daq_neuron_profiles.json) that then appears in every
    profile dropdown. The Per-neuron profiles box shows each neuron's running
    profile ("▶ <name>"). Collect Ethernet (or Auto-Sample) to verify dynamics.
  - Captures are saved automatically whichever transport you use: each grab
    writes cap_<timestamp>_<src>.npz (+ per-channel CSVs) into --capture-dir
    (default <repo>/captures), where <src> = "uart" (UART Capture) or "eth"
    (Collect Ethernet) so the two are easy to tell apart.
  - BRAM waveform builder: pick a shape (Sine/Triangle/Trapezoid/Square/Saw),
    period (ns), pulse width (ns), and a voltage range (clamped to the DAC's
    allowable range, default 0 V .. max), then program it to a channel.
  - Source editors (two pop-up windows):
      * Current source: shows the waveform programmed into the cur_wave current
        RAM. Presets Sine (5 kHz default) / Square (duty cycle) / Constant
        current / Step; the
        amplitude is in mA and can NEVER go negative. "Program" loads it via
        CURW or CURS; route Current source on a DAC to mirror the injected
        current out.
      * Pulse shape: shows the spike pulse (<=4096 signed DAC samples) and lets you
        drag individual points up/down; "Program pulse" sends it via PULS. The
        shaped pulse is one crossbar input -- route a Spike source to emit it.
  - CIC anti-alias (chip 1) toggle, autoscale, rising-edge trigger, Time/FFT.
  - Legacy mod-4 baseline removal: optional display-time diagnostic for old
    captures or genuine small per-core offsets. Corrected FPGA images assemble
    LMFS=4211 transport bytes directly and do not need it to remove the former
    +/-7 mV artifact. Saved .npz files always keep the RAW samples.
  - UART Capture: grab an ADC snapshot over UART (PCAP) and pop up the 4
    channels -- works without the Ethernet path. UART Capture, Collect Ethernet
    and Auto-Sample sit in an always-visible Capture bar below the tabs, so they
    are reachable no matter which tab is selected.

Prereqs: board programmed + A53 PS-eth app running; UART (default COM10); NIC at
192.168.2.1/24. See notes/dac_sources_howto.md.

  python scripts/dac_scope_qt.py
"""
from __future__ import annotations

import argparse
import json
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
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

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
]
COLLECT_SIZE_DEFAULT_IDX = 0   # 64 KB/chip
# Auto-Sample: repeated one-shot bursts at a fixed cadence (not the cyclic UDP
# stream). Small + fast so each grab comfortably finishes within the interval.
AUTOSAMPLE_INTERVAL_MS = 1000   # one sample per second
AUTOSAMPLE_BYTES = 64 * 1024    # bytes/chip per auto-sample (16k samples/ch)
# Neuron integration timestep (Q16.16): larger dt -> faster simulation.
# The hex is the per-step dt in Q16.16 ms, so dt_ms = value / 65536; "1x
# normal" (0x8000) = 0.5 ms, the classic Izhikevich integration step.
NEURON_DT_OPTIONS = [
    ("0.25x slow", 0x2000),
    ("0.5x", 0x4000),
    ("1x normal", 0x8000),
    ("2x", 0x10000),
    ("4x fast", 0x20000),
    ("8x faster", 0x40000),
]
NEURON_DT_DEFAULT = 2   # index of "1x normal"


def neuron_dt_ms(dt_hex):
    """Q16.16 timestep -> integration dt in milliseconds."""
    return dt_hex / 65536.0


def neuron_dt_label(label, dt_hex):
    """Dropdown text: speed label plus the actual integration dt in ms."""
    return f"{label}  (dt = {neuron_dt_ms(dt_hex):.3g} ms)"

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

# --- user-defined neuron profiles (persisted across sessions) -----------------
# Custom a/b/c/d/I sets the user saves from the GUI. Stored as JSON in the home
# directory so they survive restarts without polluting the repo. Built-in names
# (NEURON_PROFILES) are reserved and cannot be shadowed by a custom profile.
PROFILES_PATH = os.path.join(os.path.expanduser("~"), ".daq_neuron_profiles.json")
PROFILE_KEYS = ("a", "b", "c", "d", "iconst")


def load_custom_profiles():
    """Return {name: {a,b,c,d,iconst}} from PROFILES_PATH (empty on any error)."""
    try:
        with open(PROFILES_PATH, "r") as f:
            raw = json.load(f)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    if isinstance(raw, dict):
        for name, v in raw.items():
            if (isinstance(v, dict) and all(k in v for k in PROFILE_KEYS)
                    and name not in NEURON_PROFILES):
                try:
                    out[str(name)] = {k: float(v[k]) for k in PROFILE_KEYS}
                except (TypeError, ValueError):
                    pass
    return out


def save_custom_profiles(profiles):
    """Persist {name: {a,b,c,d,iconst}} to PROFILES_PATH (best effort)."""
    try:
        with open(PROFILES_PATH, "w") as f:
            json.dump(profiles, f, indent=2)
    except Exception:  # noqa: BLE001
        pass

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
CUR_SOURCE_PRESETS = ["Sine", "Square", "Constant current", "Step"]
CUR_DAC_GAIN_Q8_8_ONE = 0x0100
CUR_DAC_GAIN_MAX = 0xFFFF / 256.0
# Some DAC->ADC loopback setups are AC-coupled with a high-pass corner near this
# value. The injected current still drives the neurons below it; use a DC-coupled
# readout path if the analog loopback attenuates low-frequency current readback.
CUR_LOOPBACK_AC_CORNER_HZ = 200e3

# --- programmable spike pulse shape (izh_spike_shaper, firmware PULS) ----------
PULSE_MAX_SAMPLES = 4096       # firmware SPIKE_MAX_SAMPLES
# boot default (firmware spike_shape_init_default): INVERTED trapezoid,
# 30 ns flat top at negative full-scale with 5-sample ramps = 40 samples
PULSE_DEFAULT_LEN = 40
PULSE_DEFAULT_RAMP = 5
PULSE_DEFAULT_PEAK = -DAC_FULLSCALE


def build_trapezoid_pulse(n_samples, peak_counts=PULSE_DEFAULT_PEAK,
                          ramp_samples=None, invert=False):
    """Build a trapezoid spike pulse: ramp, hold, ramp. A negative
    peak_counts (or invert=True) makes the pulse downward-going."""
    n = max(1, min(PULSE_MAX_SAMPLES, int(n_samples)))
    peak = int(round(peak_counts))
    if invert:
        peak = -abs(peak)
    peak = max(-DAC_FULLSCALE, min(DAC_FULLSCALE, peak))
    if n == 1:
        return [peak]
    if ramp_samples is None:
        ramp = max(1, n // 4)
    else:
        ramp = max(1, int(ramp_samples))
    ramp = min(ramp, max(1, n // 2))
    hold = max(0, n - 2 * ramp)
    if hold == 0 and n > 2:
        ramp = max(1, (n - 1) // 2)
        hold = n - 2 * ramp

    ys = []
    # Use non-zero ramp endpoints, matching the historical 7-sample default:
    # ramp=2, peak=0x6000 -> 0x1800, 0x3000, then the flat top.
    denom = ramp + 2
    for i in range(ramp):
        ys.append(round(peak * (i + 1) / denom))
    ys.extend([peak] * hold)
    for i in range(ramp):
        ys.append(round(peak * (ramp - i) / denom))

    if len(ys) < n:
        insert_at = min(ramp, len(ys))
        ys[insert_at:insert_at] = [peak] * (n - len(ys))
    return [clamp_s16(v) for v in ys[:n]]


def ma_to_q16_u32(ma):
    """Physical milliamps -> Q16.16 as a 32-bit word; clamped non-negative
    and to positive signed Q16.16 (current sources can never go negative)."""
    ma = max(0.0, float(ma))
    q = int(round(ma * MA_TO_Q16))
    q = max(0, min(CUR_Q16_POS_MAX, q))
    return q & 0xFFFFFFFF


def gain_to_q8_8(gain):
    gain = max(0.0, min(CUR_DAC_GAIN_MAX, float(gain)))
    return max(0, min(0xFFFF, int(round(gain * 256.0))))


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
                     n_min=CUR_SINE_SAMPLES_MIN, step_zero=16,
                     step_high=48, step_cps=1, square_duty=50.0):
    """Build a non-negative current waveform for the cur_wave player.

    Returns (samples_ma, cps, actual_hz): per-sample amplitudes in mA (all >= 0),
    the player's cycles-per-sample divisor, and the resulting loop frequency. The
    player loops len(ys) samples advancing every cps clk_50 cycles, so
    f = 50 MHz / (cps * len)."""
    amp_ma = max(0.0, min(CUR_MAX_MA, float(amp_ma)))
    if kind.startswith("Constant"):
        return np.asarray([amp_ma]), 1, 0.0
    if kind.startswith("Step"):
        # Hold mode plays the 0-to-amp edge once, then holds the final
        # constant-current sample instead of wrapping into a square.
        zc = max(0, min(CUR_WAVE_MAX, int(step_zero)))
        hc = max(0, min(CUR_WAVE_MAX - zc, int(step_high)))
        if zc + hc <= 0:
            hc = 1
        ys = np.concatenate((np.zeros(zc), np.full(hc, amp_ma)))
        return ys, max(1, min(65535, int(step_cps))), 0.0
    n, cps, actual = choose_current_timing(freq_hz, n_max=n_max, n_min=n_min)
    i = np.arange(n)
    if kind.startswith("Square"):
        # Unipolar square: amp for the high fraction of the period, 0 for the
        # rest. Duty resolution is 1/n of a period; clamp so both levels keep
        # at least one sample (a 0%/100% request is just DC — use Constant).
        duty = max(0.0, min(100.0, float(square_duty)))
        high = max(1, min(n - 1, int(round(n * duty / 100.0))))
        ys = np.where(i < high, amp_ma, 0.0)
        return ys, cps, actual
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


def deinterleave_baseline(x):
    """Remove a mod-4 phase baseline from a full-rate capture.

    The ADC time-interleaves 4 cores per channel (sample i -> core i mod 4);
    Residual per-core DC offsets can produce a deterministic square pattern
    (spurs at DC / fs4 / fs2). Subtracting each mod-4 phase's mean removes the
    four constants -- samples keep
    their order/count and everything except true DC and the phase-locked
    fs/4 / fs/2 component passes through untouched. Display-time only; saved
    captures stay raw. Valid ONLY for full-rate data (any decimation breaks
    the sample-index <-> core association)."""
    y = np.asarray(x, dtype=np.float64).copy()
    for k in range(4):
        y[k::4] -= y[k::4].mean()
    return y


# --------------------------------------------------------------- DAC content
def clamp_s16(v):
    return max(-DAC_FULLSCALE, min(DAC_FULLSCALE, int(round(v))))


# firmware boot-default spike shape, mirrored for the pulse editor (defined
# here because build_trapezoid_pulse needs clamp_s16 at call time)
PULSE_DEFAULT = build_trapezoid_pulse(PULSE_DEFAULT_LEN, PULSE_DEFAULT_PEAK,
                                      PULSE_DEFAULT_RAMP)


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

    def _readuntil(self, prefixes, timeout=4.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.s.readline().decode("ascii", errors="replace").strip()
            if line.startswith(tuple(prefixes)):
                return line
        return ""

    def cmd(self, c, ok=("OK", "DAC xbar", "STRM"), timeout=4.0):
        """Long-running firmware commands (e.g. BCPT, whose reps each wait for
        the current player's next injection-window start) must pass a timeout
        sized to the command, or the reply is misreported as missing while the
        MB is still busy."""
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write((c + "\n").encode("ascii"))
            self.s.flush()
            return self._readuntil(ok, timeout=timeout)

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

    def uart_capture_triggered(self, frames):
        """PCAPT <frames> -> 4-channel snapshot armed to the exact start of the
        current-injection window (one-shot). Firmware arms the ADC capture, then
        restarts the current source; the player's sample-0 pulse fires the capture
        in hardware, so repeated PCAPT bursts are phase-identical -> averageable.
        Requires the current source to be configured first (CURS/CURP/CURW)."""
        return self._capture(f"PCAPT {frames}", frames)

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

    def program_step_for_capture(self, frames, amp_ma, baseline_frac=0.15,
                                 settle_frac=0.10, cps=1):
        """One-shot current step [0..][amp..][0..] sized so the high region fills
        the ADC capture window.  The window is frames*4 ADC samples at ~1 GS/s
        (1 ns each); each current sample lasts cps*20 ns, so W = frames*4/(cps*20)
        current samples span the window.  Uses CURW (not CURS) so the pulse can
        return to 0 for settling.  Returns (reply, meta)."""
        cps = max(1, int(cps))
        W = max(8, round(frames * 4 / (cps * 20.0)))       # samples spanning window
        b = max(1, round(baseline_frac * W))               # zeros before (step edge here)
        h = (W - b) + max(2, round(0.10 * W))              # high fills rest + 10% past window
        s = max(2, round(settle_frac * W))                 # settle zeros after (past window)
        if b + h + s > CUR_WAVE_MAX:
            h = max(1, CUR_WAVE_MAX - b - s)               # trim high to fit the BRAM
        samples = [0.0] * b + [float(amp_ma)] * h + [0.0] * s
        reply = self.program_current(samples, cps, hold_last=True)
        meta = {"cps": cps, "b": b, "h": h, "s": s,
                "step_ns": b * cps * 20.0, "cap_ns": frames * 4.0,
                "high_ns": h * cps * 20.0}
        return reply, meta

    def set_current_gain(self, gain):
        """Set DAC-only current-source visibility gain (firmware CURG, Q8.8).

        This does not change i_external into the neurons; it only scales the
        pure current-source DAC mirror after the observation CDC."""
        raw = gain_to_q8_8(gain)
        return self.cmd(f"CURG 0x{raw:04X}", ok=("OK CURG", "ERR"))

    def program_current_step(self, cps, zero_count, high_count, amp_ma,
                             hold_last=True):
        """Program a 0 -> amp step via firmware CURS using the existing current
        BRAM/player. hold_last=False repeats the zero/high pattern."""
        zc = max(0, min(CUR_WAVE_MAX, int(zero_count)))
        hc = max(0, min(CUR_WAVE_MAX - zc, int(high_count)))
        if zc + hc <= 0:
            hc = 1
        cps = max(1, min(65535, int(cps)))
        amp_q16 = ma_to_q16_u32(amp_ma)
        mode = "hold" if hold_last else "loop"
        return self.cmd(
            f"CURS {cps} {zc} {hc} 0x{amp_q16:08X} {mode}",
            ok=("OK CURS", "ERR"))

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
        """Reload the boot-default spike shape (inverted 30 ns trapezoid)."""
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

        ctl.addWidget(QtWidgets.QLabel("ramp"))
        self.ramp_spin = QtWidgets.QSpinBox()
        self.ramp_spin.setRange(1, max(1, len(PULSE_DEFAULT) // 2))
        self.ramp_spin.setValue(PULSE_DEFAULT_RAMP)
        self.ramp_spin.valueChanged.connect(self._info)
        ctl.addWidget(self.ramp_spin)

        ctl.addWidget(QtWidgets.QLabel("peak"))
        self.peak_spin = QtWidgets.QSpinBox()
        self.peak_spin.setRange(0, DAC_FULLSCALE)
        self.peak_spin.setValue(abs(PULSE_DEFAULT_PEAK))
        self.peak_spin.valueChanged.connect(self._info)
        ctl.addWidget(self.peak_spin)

        self.invert_chk = QtWidgets.QCheckBox("invert")
        self.invert_chk.setChecked(PULSE_DEFAULT_PEAK < 0)
        self.invert_chk.setToolTip("Downward-going pulse: 0 to -peak instead "
                                   "of 0 to +peak.")
        self.invert_chk.stateChanged.connect(self._info)
        ctl.addWidget(self.invert_chk)

        self.trap_btn = QtWidgets.QPushButton("Build trapezoid")
        self.trap_btn.clicked.connect(self._on_trapezoid)
        ctl.addWidget(self.trap_btn)
        self.default_btn = QtWidgets.QPushButton("Load default (inv 30 ns)")
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
        self._sync_trapezoid_controls(len(PULSE_DEFAULT))
        self.editor.set_values(PULSE_DEFAULT)
        self._info()

    def _sync_trapezoid_controls(self, n):
        ramp_max = max(1, int(n) // 2)
        self.ramp_spin.blockSignals(True)
        self.ramp_spin.setMaximum(ramp_max)
        if self.ramp_spin.value() > ramp_max:
            self.ramp_spin.setValue(ramp_max)
        self.ramp_spin.blockSignals(False)

    def _on_len(self, n):
        ys = list(self.editor.ys)
        ys = ys[:n] if n <= len(ys) else ys + [0.0] * (n - len(ys))
        self._sync_trapezoid_controls(n)
        self.editor.set_values(ys)
        self._info()

    def _on_trapezoid(self):
        ys = build_trapezoid_pulse(self.len_spin.value(),
                                   self.peak_spin.value(),
                                   self.ramp_spin.value(),
                                   invert=self.invert_chk.isChecked())
        self.editor.set_values(ys)
        self._info()

    def _on_default(self):
        self.len_spin.blockSignals(True)
        self.len_spin.setValue(len(PULSE_DEFAULT))
        self.len_spin.blockSignals(False)
        self._sync_trapezoid_controls(len(PULSE_DEFAULT))
        self.ramp_spin.setValue(PULSE_DEFAULT_RAMP)
        self.peak_spin.setValue(abs(PULSE_DEFAULT_PEAK))
        self.invert_chk.setChecked(PULSE_DEFAULT_PEAK < 0)
        self.editor.set_values(PULSE_DEFAULT)
        self._info()

    def _on_zero(self):
        self.editor.set_values(np.zeros(self.len_spin.value()))
        self._info()

    def _info(self):
        ys = self.editor.values()
        nb = (len(ys) + 3) // 4
        pk = max((abs(v) for v in ys), default=0)
        ramp = min(self.ramp_spin.value(), max(1, len(ys) // 2))
        hold = max(0, len(ys) - 2 * ramp)
        self.info.setText(
            f"{len(ys)} samples ({len(ys)} ns), nbeats={nb}, peak |{pk}| counts "
            f"({pk * VOLTS_PER_COUNT:.3f} V), trapezoid ramp/hold/ramp = "
            f"{ramp}/{hold}/{ramp}.  Drag points up/down to edit; "
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
    Presets: Sine (5 kHz default), Square (programmable duty), Constant current,
    Step.
    Amplitude is in mA and can NEVER go negative. Programs the player via CURW/CURS;
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

        grid.addWidget(QtWidgets.QLabel("DAC mirror gain"), 2, 0)
        self.gain_spin = QtWidgets.QDoubleSpinBox()
        self.gain_spin.setRange(0.0, CUR_DAC_GAIN_MAX)
        self.gain_spin.setDecimals(3)
        self.gain_spin.setSingleStep(0.25)
        self.gain_spin.setValue(20.0)
        self.gain_spin.setSuffix(" x")
        self.gain_spin.valueChanged.connect(self._refresh)
        grid.addWidget(self.gain_spin, 2, 1)

        grid.addWidget(QtWidgets.QLabel("frequency"), 3, 0)
        self.freq_spin = QtWidgets.QDoubleSpinBox()
        self.freq_spin.setRange(CUR_FREQ_REQUEST_MIN_HZ, CUR_FREQ_REQUEST_MAX_HZ)
        self.freq_spin.setDecimals(3)
        self.freq_spin.setSingleStep(100.0)
        self.freq_spin.setValue(5000.0)
        self.freq_spin.setSuffix(" Hz")
        self.freq_spin.valueChanged.connect(self._refresh)
        grid.addWidget(self.freq_spin, 3, 1)

        grid.addWidget(QtWidgets.QLabel("square duty cycle"), 4, 0)
        self.duty_spin = QtWidgets.QDoubleSpinBox()
        self.duty_spin.setRange(0.1, 99.9)
        self.duty_spin.setDecimals(1)
        self.duty_spin.setSingleStep(5.0)
        self.duty_spin.setValue(50.0)
        self.duty_spin.setSuffix(" %")
        self.duty_spin.valueChanged.connect(self._refresh)
        grid.addWidget(self.duty_spin, 4, 1)

        grid.addWidget(QtWidgets.QLabel("step zero samples"), 5, 0)
        self.step_zero_spin = QtWidgets.QSpinBox()
        self.step_zero_spin.setRange(0, CUR_WAVE_MAX - 1)
        self.step_zero_spin.setValue(16)
        self.step_zero_spin.valueChanged.connect(self._refresh)
        grid.addWidget(self.step_zero_spin, 5, 1)

        grid.addWidget(QtWidgets.QLabel("step high samples"), 6, 0)
        self.step_high_spin = QtWidgets.QSpinBox()
        self.step_high_spin.setRange(0, CUR_WAVE_MAX)
        self.step_high_spin.setValue(48)
        self.step_high_spin.valueChanged.connect(self._refresh)
        grid.addWidget(self.step_high_spin, 6, 1)

        grid.addWidget(QtWidgets.QLabel("step cps"), 7, 0)
        self.step_cps_spin = QtWidgets.QSpinBox()
        self.step_cps_spin.setRange(1, 65535)
        self.step_cps_spin.setValue(1)
        self.step_cps_spin.valueChanged.connect(self._refresh)
        grid.addWidget(self.step_cps_spin, 7, 1)

        grid.addWidget(QtWidgets.QLabel("step mode"), 8, 0)
        self.step_mode_cb = QtWidgets.QComboBox()
        self.step_mode_cb.addItems(["hold last", "loop"])
        self.step_mode_cb.currentIndexChanged.connect(self._refresh)
        grid.addWidget(self.step_mode_cb, 8, 1)
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
        is_step = kind.startswith("Step")
        is_square = kind.startswith("Square")
        # Frequency is meaningful only for periodic (sine/square) playback.
        self.freq_spin.setEnabled(kind.startswith("Sine") or is_square)
        self.duty_spin.setEnabled(is_square)
        for w in (self.step_zero_spin, self.step_high_spin, self.step_cps_spin,
                  self.step_mode_cb):
            w.setEnabled(is_step)
        zc = self.step_zero_spin.value()
        max_high = CUR_WAVE_MAX - zc
        if self.step_high_spin.maximum() != max_high:
            self.step_high_spin.blockSignals(True)
            self.step_high_spin.setMaximum(max_high)
            if self.step_high_spin.value() > max_high:
                self.step_high_spin.setValue(max_high)
            self.step_high_spin.blockSignals(False)
        hc = self.step_high_spin.value()
        step_cps = self.step_cps_spin.value()
        step_loop = self.step_mode_cb.currentText().startswith("loop")
        ys, cps, actual = gen_current_wave(
            kind, amp, freq, step_zero=zc, step_high=hc, step_cps=step_cps,
            square_duty=self.duty_spin.value())
        if is_step and step_loop:
            actual = CUR_PLAYER_CLK_HZ / (max(1, cps) * max(1, len(ys)))
        self._ys, self._cps = ys, cps
        self._kind = kind
        self._actual = actual
        self._step_zero = zc
        self._step_high = hc
        self._step_loop = step_loop
        n = len(ys)
        dt = cps / CUR_PLAYER_CLK_HZ
        if kind.startswith("Step") and not step_loop:
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
        if ((kind.startswith("Sine") or kind.startswith("Square"))
                and actual < CUR_LOOPBACK_AC_CORNER_HZ):
            tail = (f"  Note: {format_rate(actual)} is below the ~"
                    f"{CUR_LOOPBACK_AC_CORNER_HZ/1e3:.0f} kHz loopback AC corner; "
                    f"use the Current source DAC route or a DC-coupled readout "
                    f"to inspect it cleanly.")
        elif kind.startswith("Step"):
            if step_loop:
                tail = "  Loops the programmed zero/high pattern."
            else:
                tail = "  Programs once, then holds the final constant-current sample."
        elif kind.startswith("Constant"):
            tail = "  Programs a one-sample loop, so the injected current is DC."
        else:
            tail = "  Route Current source on a DAC to view the injected waveform."
        rate_text = "one-shot hold" if (kind.startswith("Step") and not step_loop) else format_rate(actual)
        if is_square and n > 0:
            high_n = int(np.count_nonzero(ys))
            rate_text += f", duty {100.0 * high_n / n:.1f}%"
        if actual > 0 and (actual <= CUR_SINE_HW_MIN_HZ * 1.001 or
                           actual >= CUR_SINE_HW_MAX_HZ * 0.999):
            rate_text += " (nearest hardware rate)"
        self.info.setText(
            f"{kind}: {n} samples, cps={cps} -> {rate_text}, "
            f"peak {ys.max():.3f} mA (1 mA = 1.0 I-unit, unipolar 0+), "
            f"DAC mirror gain {self.gain_spin.value():.3f}x.{tail}")
        self.scope._set_current_preview(kind, ys, cps, actual, programmed=False)
    def _on_prog(self):
        dac = self.scope.dac
        if not dac:
            self.info.setText("connect a board first")
            return
        ys, cps = self._ys, self._cps
        self.prog_btn.setEnabled(False)

        def work():
            gain_note = ""
            r = dac.set_current_gain(self.gain_spin.value())
            if not r or r.startswith("ERR"):
                if r and "unknown command" in r.lower():
                    # Firmware predates CURG (display-only DAC mirror gain).
                    # The waveform itself still programs fine without it.
                    gain_note = "  [board firmware lacks CURG; mirror gain skipped]"
                else:
                    self.done.emit(False, r or "(no reply)")
                    return
            if self._kind.startswith("Step"):
                r = dac.program_current_step(
                    cps, self._step_zero, self._step_high,
                    self.amp_spin.value(), hold_last=not self._step_loop)
            else:
                r = dac.program_current(ys, cps, hold_last=False)
            self.done.emit(bool(r and r.startswith("OK")),
                           (r or "(no reply)") + gain_note)
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


# ----------------------------------------------------- crossbar visualization
class CrossbarView(QtWidgets.QWidget):
    """Visual 16:4 DAC source crossbar.

    The 16 crossbar sources are listed down the left edge and the 4 DAC outputs
    down the right edge. A SOLID colored line is drawn from each DAC's *applied*
    (live-on-the-board) source to that DAC; a staged-but-not-yet-applied pick is
    drawn as a faint DASHED line. The solid routing therefore only changes once a
    route is actually committed, so the picture never lies about the hardware."""

    def __init__(self, source_labels, colors, parent=None):
        super().__init__(parent)
        self.sources = list(source_labels)
        self.colors = list(colors)
        self.applied = [None, None, None, None]   # live source idx per DAC
        self.pending = [None, None, None, None]   # staged source idx per DAC
        self.profiles = [None, None, None, None]  # spike/monitor profile note
        self.setMinimumHeight(24 * len(self.sources) + 24)
        self.setMinimumWidth(360)

    def set_applied(self, ch, idx, profile=None):
        self.applied[ch] = idx
        self.profiles[ch] = profile
        self.update()

    def set_pending(self, ch, idx):
        self.pending[ch] = idx
        self.update()

    def reset(self):
        self.applied = [None, None, None, None]
        self.pending = [None, None, None, None]
        self.update()

    def paintEvent(self, _ev):
        qp = QtGui.QPainter(self)
        qp.setRenderHint(QtGui.QPainter.Antialiasing, True)
        qp.fillRect(self.rect(), QtGui.QColor("#0d1116"))
        W, H = self.width(), self.height()
        n = len(self.sources)
        top, bot = 16.0, H - 16.0
        src_x = 122.0
        dac_x = W - 74.0
        span = max(1.0, bot - top)

        def sy(i):
            return top + span * (i / max(1, n - 1))

        def dy(c):
            return top + span * ((c + 0.5) / 4.0)

        f = qp.font()
        f.setPointSize(8)
        qp.setFont(f)
        live = {i for i in self.applied if i is not None}
        staged = {self.pending[c] for c in range(4)
                  if self.pending[c] is not None
                  and self.pending[c] != self.applied[c]}

        # staged (dashed, faint) lines first, beneath the solid ones
        for c in range(4):
            pi = self.pending[c]
            if pi is None or pi == self.applied[c]:
                continue
            pen = QtGui.QPen(QtGui.QColor(150, 162, 176, 160))
            pen.setStyle(QtCore.Qt.DashLine)
            pen.setWidthF(1.4)
            qp.setPen(pen)
            qp.drawLine(QtCore.QPointF(src_x, sy(pi)),
                        QtCore.QPointF(dac_x, dy(c)))
        # applied (solid, colored) lines
        for c in range(4):
            ai = self.applied[c]
            if ai is None:
                continue
            pen = QtGui.QPen(QtGui.QColor(self.colors[c]))
            pen.setWidthF(2.6)
            qp.setPen(pen)
            qp.drawLine(QtCore.QPointF(src_x, sy(ai)),
                        QtCore.QPointF(dac_x, dy(c)))

        # source nodes + right-aligned labels
        align_r = int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        for i, name in enumerate(self.sources):
            y = sy(i)
            on, stg = i in live, i in staged
            txt = "#e6eef6" if on else ("#aeb9c6" if stg else "#5f7185")
            qp.setPen(QtGui.QColor(txt))
            qp.drawText(QtCore.QRectF(2, y - 9, src_x - 14, 18), align_r, name)
            qp.setPen(QtCore.Qt.NoPen)
            qp.setBrush(QtGui.QColor("#4FC3F7") if on else QtGui.QColor("#33414d"))
            qp.drawEllipse(QtCore.QPointF(src_x, y), 3.4, 3.4)

        # DAC nodes + labels
        align_l = int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        f.setBold(True)
        qp.setFont(f)
        for c in range(4):
            y = dy(c)
            qp.setPen(QtCore.Qt.NoPen)
            qp.setBrush(QtGui.QColor(self.colors[c]))
            qp.drawEllipse(QtCore.QPointF(dac_x, y), 5.0, 5.0)
            qp.setPen(QtGui.QColor("#e6eef6"))
            qp.drawText(QtCore.QRectF(dac_x + 10, y - 10, W - dac_x - 12, 20),
                        align_l, f"DAC{c}")
        f.setBold(False)
        qp.setFont(f)
        qp.end()


# --------------------------------------------------------------- GUI
class ScopeWindow(QtWidgets.QMainWindow):
    captured = QtCore.pyqtSignal(object)      # emits {ch: int16[]} from worker
    collected = QtCore.pyqtSignal(object)     # emits {ch: int16[]} burst-over-eth
    stat_result = QtCore.pyqtSignal(bool, str)  # board-verify result from worker
    neuron_done = QtCore.pyqtSignal(str, bool)  # (target, ok) program-neuron result
    dac_done = QtCore.pyqtSignal(int, bool, str)  # (ch, ok, detail) program-DAC result
    autosampled = QtCore.pyqtSignal(object)   # emits {ch: int16[], '_cov'} each auto-sample
    burst_result = QtCore.pyqtSignal(object)  # emits {'caps':[...], 'frames':int} triggered burst
    burst_progress = QtCore.pyqtSignal(int, int)  # (done, total) during a triggered burst
    msamp_result = QtCore.pyqtSignal(object)  # multisample Ethernet burst (BCPT) result
    config_done = QtCore.pyqtSignal(object)   # chattering-demo defaults result

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
        # crossbar routing state: solid lines / summary reflect the APPLIED route
        # (only updated once a route is committed), never the staged dropdown.
        self.custom_profiles = load_custom_profiles()
        self._applied_label = [None, None, None, None]    # live source per DAC
        self._applied_profile = [None, None, None, None]  # live profile per DAC
        self._dac_prog = [None, None, None, None]          # (label,prof,nidx) in flight
        self._prog_profile_name = {}                       # target -> profile being set
        self.neuron_applied_profile = {"0": None, "1": None, "2": None, "3": None}
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
        # right column: a scrollable area (connection + tabs) above an
        # ALWAYS-VISIBLE strip (capture controls + status) that never scrolls
        # off, whatever tab is selected or how the panel is scrolled.
        rightw = QtWidgets.QWidget()
        rightw.setFixedWidth(PANEL_W)
        right_col = QtWidgets.QVBoxLayout(rightw)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.addWidget(scroll, stretch=1)
        root.addWidget(rightw)
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
        self.tabs.addTab(capture_tab, "Display")
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

        xprev = QtWidgets.QGroupBox("Crossbar routing (16 → 4)")
        xprev_lay = QtWidgets.QVBoxLayout(xprev)
        self.xbar_view = CrossbarView(SOURCE_LABELS, CH_COLORS)
        xprev_lay.addWidget(self.xbar_view)
        legend = QtWidgets.QLabel(
            "solid line = live route · dashed = staged (press “Confirm route”)")
        legend.setStyleSheet("color:#9fb3c8; font-size:10px;")
        legend.setWordWrap(True)
        xprev_lay.addWidget(legend)
        self.xbar_summary = QtWidgets.QLabel()
        self.xbar_summary.setStyleSheet("color:#cdd9e5; font-size:11px;")
        self.xbar_summary.setWordWrap(True)
        xprev_lay.addWidget(self.xbar_summary)
        left.addWidget(xprev)
        left.addStretch(1)

        # neuron simulation speed (all neurons)
        nb = QtWidgets.QGroupBox("Neuron sim speed (all)")
        ng = QtWidgets.QHBoxLayout(nb)
        self.dt_cb = QtWidgets.QComboBox()
        self.dt_cb.addItems([neuron_dt_label(lbl, val)
                             for lbl, val in NEURON_DT_OPTIONS])
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
        pg_.addWidget(self.np_loadprof, 0, 1)
        self.np_saveprof = QtWidgets.QPushButton("Save…")
        self.np_saveprof.setToolTip("Save the current a/b/c/d/I values as a named "
                                    "custom profile (persists across sessions)")
        self.np_saveprof.clicked.connect(self._on_save_profile)
        pg_.addWidget(self.np_saveprof, 0, 2)
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

        # display options (capture buttons are relocated to an always-visible
        # group outside the tabs, below)
        opt = QtWidgets.QGroupBox("Display")
        og = QtWidgets.QGridLayout(opt)
        self.cic_chk = QtWidgets.QCheckBox("CIC anti-alias (ch2/3)")
        self.cic_chk.setChecked(args.cic)
        self.cic_chk.toggled.connect(self._on_cic)
        # Legacy display-time mod-4 diagnostic; raw saves are never altered.
        self.deint_chk = QtWidgets.QCheckBox("Legacy mod-4 baseline removal")
        self.deint_chk.setToolTip(
            "Subtract each mod-4 sample phase's mean from full-rate captures.\n"
            "For old captures or genuine small core offsets; corrected FPGA\n"
            "byte mapping does not need it for the old +/-7 mV artifact.\n"
            "Display only -- saved .npz stays raw.\n"
            "Also removes true DC and the phase-locked fs/4 & fs/2 component.")
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
        # Triggered burst average: N repeated PCAPT captures, each hardware-synced
        # to the current-injection window start, then aligned + averaged.
        self.burst_n = QtWidgets.QSpinBox()
        self.burst_n.setRange(2, 256)
        self.burst_n.setValue(16)
        self.burst_n.setPrefix("N=")
        self.burst_btn = QtWidgets.QPushButton("Trig Burst Avg")
        self.burst_btn.clicked.connect(self._on_burst)
        self.burst_step_chk = QtWidgets.QCheckBox("fit step")
        # Off by default: when on, every burst REWRITES DAC0's crossbar route
        # (NSRC 0 current) and replaces the loaded current waveform with a
        # window-fit step -- surprising when a demo/experiment config is live.
        self.burst_step_chk.setChecked(False)
        self.burst_step_chk.setToolTip(
            "Before the burst, program a one-shot current step sized to the "
            "capture window (0 baseline, amp for most of the window, 0 settle "
            "after) and route it to DAC0.")
        self.burst_amp = QtWidgets.QDoubleSpinBox()
        self.burst_amp.setRange(0.1, 30.0)
        self.burst_amp.setValue(6.0)
        self.burst_amp.setDecimals(1)
        self.burst_amp.setSuffix(" mA")
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
        # Multisample trigger-synced Ethernet burst (firmware BCPT): N deep DMA
        # captures, each fired in hardware by the current player's sample-0
        # pulse, strided in DDR and drained by ONE UDP pass. Per-rep size comes
        # from the Collect size combo above.
        self.msamp_reps = QtWidgets.QSpinBox()
        self.msamp_reps.setRange(2, 127)
        self.msamp_reps.setValue(16)
        self.msamp_reps.setPrefix("reps=")
        self.msamp_btn = QtWidgets.QPushButton("Multi-Eth Trig Capture")
        self.msamp_btn.setToolTip(
            "BCPT: reps trigger-synchronized full-rate captures (per-rep size = "
            "the Collect size above), aligned + averaged, saved as burst_*.npz. "
            "Requires the current source to be configured and running "
            "(e.g. via the Chattering Demo Setup button or the current editor).")
        self.msamp_btn.clicked.connect(self._on_multisample)
        # One-click demo bring-up: chattering neurons, 10 mA step current,
        # current->DAC0 / neuron0 spike->DAC1, 50-sample trapezoid pulse.
        self.defaults_btn = QtWidgets.QPushButton("Chattering Demo Setup")
        self.defaults_btn.setToolTip(
            "All neurons -> chattering profile with injected input current i=0 "
            "(iconst supplies the drive); current source -> 10 mA one-shot step, "
            "first 25% baseline, full 1024-sample BRAM, hold mode; crossbar: "
            "current source on DAC0, neuron-0 spike output on DAC1; spike pulse "
            "shape -> inverted 50-sample trapezoid with 5-sample ramps.")
        self.defaults_btn.clicked.connect(self._on_default_config)
        og.addWidget(self.cic_chk, 0, 0, 1, 2)
        og.addWidget(self.deint_chk, 1, 0, 1, 2)
        og.addWidget(self.auto_chk, 2, 0)
        og.addWidget(self.trig_chk, 2, 1)
        og.addWidget(self.rb_time, 3, 0)
        og.addWidget(self.rb_fft, 3, 1)
        og.addWidget(self.run_btn, 4, 0, 1, 2)
        capture_lay.addWidget(opt)

        # Acquisition controls live OUTSIDE the tabs (added to `outer`) so the
        # UART Capture and Collect Ethernet buttons are ALWAYS visible, whatever
        # tab is selected.
        acq = QtWidgets.QGroupBox("Capture (always available)")
        ag = QtWidgets.QGridLayout(acq)
        ag.addWidget(self.capt_frames, 0, 0)
        ag.addWidget(self.capt_btn, 0, 1)
        ag.addWidget(self.collect_mb_cb, 1, 0)
        ag.addWidget(self.collect_btn, 1, 1)
        ag.addWidget(self.stream_btn, 2, 0, 1, 2)
        ag.addWidget(self.burst_n, 3, 0)
        ag.addWidget(self.burst_btn, 3, 1)
        ag.addWidget(self.burst_step_chk, 4, 0)
        ag.addWidget(self.burst_amp, 4, 1)
        ag.addWidget(self.msamp_reps, 5, 0)
        ag.addWidget(self.msamp_btn, 5, 1)
        ag.addWidget(self.defaults_btn, 6, 0, 1, 2)
        right_col.addWidget(acq)        # outside the scroll area -> always visible

        left.addStretch(1)
        right.addStretch(1)
        capture_lay.addStretch(1)
        wave_lay.addStretch(1)
        self.status = QtWidgets.QLabel("Connect to a COM port to begin.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        right_col.addWidget(self.status)   # always-visible strip, not scrollable

        self._refresh_profile_combos()
        self._refresh_xbar_profile_enable()
        self._refresh_xbar_preview()
        self._refresh_xbar_summary()
        ys, cps, actual = gen_current_wave("Sine", 10.0, 5000.0)
        self._set_current_preview("Sine", ys, cps, actual, programmed=False)
        self._apply_view_ranges()
        self._set_controls_enabled(False)
        self.captured.connect(self._show_capture)
        self.burst_result.connect(self._show_trig_burst)
        self.burst_progress.connect(
            lambda i, n: self.status.setText(f"Triggered burst: {i}/{n} captured..."))
        self.collected.connect(self._on_collected)
        self.msamp_result.connect(self._show_multisample)
        self.config_done.connect(self._on_config_done)
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
        if kind.startswith("Step") and actual <= 0:
            plot_ys = np.concatenate((ys, np.repeat(ys[-1], max(16, ys.size))))
        else:
            reps = 3 if ys.size > 1 else 16
            plot_ys = np.tile(ys, reps)
        t = np.arange(plot_ys.size) * dt
        self.cur_preview_curve.setData(t, plot_ys)
        ymax = max(1.0, float(ys.max()) * 1.15)
        self.cur_preview.setYRange(-0.05 * ymax, ymax)
        state = "programmed" if programmed else "staged"
        freq = "one-shot hold" if (kind.startswith("Step") and actual <= 0) else (
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
        """A dropdown change only STAGES a route -- draw it as a dashed pending
        line. The solid (live) line is set separately, on apply, so the picture
        never claims a route the board hasn't taken."""
        if not hasattr(self, "xbar_view"):
            return
        for ch in range(4):
            label = self.src_cbs[ch].currentText()
            idx = SOURCE_LABELS.index(label) if label in SOURCE_LABELS else None
            self.xbar_view.set_pending(ch, idx)

    def _set_applied_route(self, ch, label, profile=None):
        """Record a route as live-on-the-board and redraw the solid line +
        summary. Called only after the board confirms (NSRC OK)."""
        self._applied_label[ch] = label
        self._applied_profile[ch] = profile
        idx = SOURCE_LABELS.index(label) if label in SOURCE_LABELS else None
        if hasattr(self, "xbar_view"):
            self.xbar_view.set_applied(ch, idx, profile)
        self._refresh_xbar_summary()

    def _refresh_xbar_summary(self):
        if not hasattr(self, "xbar_summary"):
            return
        rows = []
        for ch in range(4):
            lbl = self._applied_label[ch]
            if lbl is None:
                rows.append(f"DAC{ch} ← (not applied)")
            elif (self._applied_profile[ch]
                  and (lbl.startswith("Spike ") or lbl.startswith("Monitor "))):
                rows.append(f"DAC{ch} ← {lbl} [{self._applied_profile[ch]}]")
            else:
                rows.append(f"DAC{ch} ← {lbl}")
        self.xbar_summary.setText("\n".join(rows))

    def _set_neuron_running(self, target, profile):
        """Show the profile a neuron is currently running (the 'show its
        profile' display in the Per-neuron profiles box)."""
        if target == "all":
            for k in self.neuron_status:
                self._set_neuron_running(k, profile)
            return
        self.neuron_applied_profile[target] = profile
        st = self.neuron_status.get(target)
        if st:
            st.setText(f"▶ {profile}")
            st.setStyleSheet("color:#81C784; font-size:10px;")

    def _apply_profile_blocking(self, target, profile):
        """Apply a profile to a neuron (UART, call from a worker thread). A
        built-in name uses NEUR <profile>; a custom profile writes its saved
        a/b/c/d/I params. Returns (ok, profile)."""
        if profile in NEURON_PROFILES:
            r = self.dac.set_neuron(target, profile)
            return bool(r and not r.startswith("ERR")), profile
        vals = self.custom_profiles.get(profile)
        if not vals:
            return False, profile
        ok = True
        for p in PROFILE_KEYS:
            r = self.dac.set_neuron_param(target, p, izh_to_q16(vals[p]))
            if not r or r.startswith("ERR"):
                ok = False
        return ok, profile

    def _refresh_profile_combos(self):
        """Rebuild every profile dropdown to include the built-ins plus any
        saved custom profiles, preserving each combo's current selection."""
        names = list(NEURON_PROFILES) + list(self.custom_profiles.keys())
        combos = list(self.neuron_profile_cbs.values()) + list(self.prof_cbs)
        for cb in combos:
            cur = cb.currentText()
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(names)
            i = cb.findText(cur)
            cb.setCurrentIndex(i if i >= 0 else 0)
            cb.blockSignals(False)
        cur = self.np_loadprof.currentText()
        self.np_loadprof.blockSignals(True)
        self.np_loadprof.clear()
        self.np_loadprof.addItems(["load profile…"] + names)
        j = self.np_loadprof.findText(cur)
        self.np_loadprof.setCurrentIndex(j if j > 0 else 0)
        self.np_loadprof.blockSignals(False)
        self._refresh_xbar_profile_enable()

    def _on_save_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save neuron profile", "Name for this a/b/c/d/I set:")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name in NEURON_PROFILES:
            self.np_status.setText(f"'{name}' is a built-in name — pick another")
            self.np_status.setStyleSheet("color:#E57373; font-size:11px;")
            return
        vals = {p: float(self.np_spins[p].value()) for p in PROFILE_KEYS}
        self.custom_profiles[name] = vals
        save_custom_profiles(self.custom_profiles)
        self._refresh_profile_combos()
        self.np_status.setText(
            f"saved profile '{name}'  (a={vals['a']:g} b={vals['b']:g} "
            f"c={vals['c']:g} d={vals['d']:g} I={vals['iconst']:g})")
        self.np_status.setStyleSheet("color:#81C784; font-size:11px;")

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
        # drop any previously-applied routes from the picture until the board
        # re-confirms them (a failed/old connect must not show phantom live lines)
        for ch in range(4):
            self._applied_label[ch] = None
            self._applied_profile[ch] = None
        if hasattr(self, "xbar_view"):
            self.xbar_view.reset()
        self._refresh_xbar_preview()
        self._refresh_xbar_summary()
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
        # reflect the source each DAC was set to on connect -- these are now the
        # live routes (solid lines + summary), matching the NSRC sent above.
        for ch in range(4):
            self.dac_status[ch].setText(f"OK — {self.args.initial}")
            self.dac_status[ch].setStyleSheet("color:#81C784; font-size:11px;")
            self._set_applied_route(ch, self.args.initial, None)
        # base_setup() programmed neuron n with the n-th built-in profile
        for n in range(4):
            self._set_neuron_running(str(n), NEURON_PROFILES[n])
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
                  self.burst_btn, self.burst_n, self.burst_step_chk, self.burst_amp,
                  self.msamp_reps, self.msamp_btn, self.defaults_btn,
                  self.np_loadprof, self.np_saveprof, self.cur_preview_btn):
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
        self._dac_prog[ch] = (label, profile, neuron_idx)
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
                ok2, _ = self._apply_profile_blocking(neuron_idx, profile)
                if not ok2:
                    ok = False
                detail = f"{label} (neuron {neuron_idx}: {profile})"
            self.dac_done.emit(ch, ok, detail)
        self._bg(work)

    def _on_dac_done(self, ch, ok, detail):
        self.dac_btns[ch].setEnabled(True)
        if ok:
            self.dac_status[ch].setText(f"OK — {detail}")
            self.dac_status[ch].setStyleSheet("color:#81C784; font-size:11px;")
            # the route is now live: promote the staged pick to a solid line and
            # reflect any neuron (re)program in the per-neuron profile display.
            prog = self._dac_prog[ch]
            if prog is not None:
                label, profile, neuron_idx = prog
                self._set_applied_route(
                    ch, label, profile if neuron_idx is not None else None)
                if neuron_idx is not None:
                    self._set_neuron_running(str(neuron_idx), profile)
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
        vals = NEURON_PROFILE_VALUES.get(name) or self.custom_profiles.get(name)
        if not vals:
            return
        for p, sp in self.np_spins.items():
            sp.setValue(vals[p])
        self.np_loadprof.blockSignals(True)
        self.np_loadprof.setCurrentIndex(0)
        self.np_loadprof.blockSignals(False)
        self.status.setText(
            f"staged profile '{name}' -- press a Program button (0/1/2/3/all)")

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
        self._prog_profile_name[target] = profile
        btn = self.neuron_profile_btns.get(target)
        if btn:
            btn.setEnabled(False)
        st = self.neuron_status.get(target)
        if st:
            st.setText("…")
            st.setStyleSheet("color:#FFB74D; font-size:10px;")
        self.status.setText(f"programming neuron {target} profile {profile}")

        def work():
            ok, _ = self._apply_profile_blocking(target, profile)
            self.neuron_done.emit(target, ok)
        self._bg(work)

    def _program_neuron(self, target):
        if not self.dac:
            return
        vals = [(p, self.np_spins[p].value()) for p, *_ in NEURON_PARAM_SPECS]
        labels = ", ".join(f"{p}={v:g}" for p, v in vals)
        self._prog_profile_name[target] = "custom"
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
        prof = self._prog_profile_name.get(target, "custom")
        if ok:
            self._set_neuron_running(target, prof)    # shows '▶ <profile>'
        else:
            targets = self.neuron_status.keys() if target == "all" else [target]
            for k in targets:
                st = self.neuron_status.get(k)
                if st:
                    st.setText("ERR")
                    st.setStyleSheet("color:#E57373; font-size:10px;")
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
            p.plot(t, self._deint(chans[ch]) * VOLTS_PER_COUNT,
                   pen=pg.mkPen(CH_COLORS[ch], width=1.0))
        deint = ("; interleave baseline removed"
                 if self.deint_chk.isChecked() else "")
        win.addLabel(f"UART CAPT snapshot  (x = ns @ 1 GS/s{deint})",
                     row=4, col=0)
        win.show()
        self._popup = win                  # keep a reference so it isn't GC'd
        sub = save_capture(self.args.capture_dir, "uart", chans, 1.0e9)
        where = f"  -> {sub}" if sub else "  (save FAILED)"
        self.status.setText(f"UART capture: {len(chans[0])} samples/ch.{where}")

    # ---- triggered burst average (N x PCAPT, hardware-synced to injection) ----
    def _on_burst(self):
        if not self.dac:
            return
        frames = CAPT_FRAME_OPTIONS[self.capt_frames.currentIndex()]
        n = self.burst_n.value()
        do_step = self.burst_step_chk.isChecked()
        amp = self.burst_amp.value()
        self.burst_btn.setEnabled(False)
        self.status.setText(f"Triggered burst: 0/{n} ...")

        def work():
            try:
                meta = None
                if do_step:
                    # route the current source to DAC0 (your loopback) and program
                    # a one-shot step sized to the capture window
                    self.dac.cmd("NSRC 0 current", ok=("DAC xbar", "ERR"))
                    reply, meta = self.dac.program_step_for_capture(frames, amp)
                    if "OK CURW" not in (reply or ""):
                        self.burst_result.emit(
                            {"error": f"step program failed: {reply!r}"})
                        return
                caps = []
                for i in range(n):
                    d = self.dac.uart_capture_triggered(frames)
                    if d is None:
                        self.burst_result.emit(
                            {"error": f"capture {i + 1}/{n} returned no data. "
                                      "PCAPT waits for the current-injection restart -- "
                                      "make sure the current source is configured AND "
                                      "running (program a step in the Current Source "
                                      "window) before running a burst."})
                        return
                    caps.append(d)
                    self.burst_progress.emit(i + 1, n)
                self.burst_result.emit({"caps": caps, "frames": frames, "meta": meta})
            except Exception as e:            # surface errors instead of a silent grey hang
                import traceback
                traceback.print_exc()
                self.burst_result.emit({"error": f"{type(e).__name__}: {e}"})
        self._bg(work)

    def _show_trig_burst(self, data):
        self.burst_btn.setEnabled(True)
        if not isinstance(data, dict) or "caps" not in data:
            msg = data.get("error", "no data") if isinstance(data, dict) else "no data"
            self.status.setText(f"Triggered burst failed: {msg}")
            return
        caps = data["caps"]
        n = len(caps)
        L = min(len(c[0]) for c in caps)
        anchor = 0
        # de-interleaved float counts, truncated to the common length
        stack = {ch: np.stack([self._deint(c[ch]).astype(np.float64)[:L]
                               for c in caps]) for ch in range(4)}
        # Alignment sanity: cross-correlate each rep to rep 0 on the anchor channel.
        # A correct hardware trigger gives ~0 offset; we still roll to remove any
        # residual jitter before averaging (belt and suspenders).
        ref = stack[anchor][0] - stack[anchor][0].mean()
        offs = []
        for i in range(n):
            sig = stack[anchor][i] - stack[anchor][i].mean()
            xc = np.correlate(sig, ref, mode="full")
            off = int(np.argmax(xc) - (L - 1))
            if abs(off) > L // 4:      # weak/noisy anchor -> don't trust wild shifts
                off = 0
            offs.append(off)
            for ch in range(4):
                stack[ch][i] = np.roll(stack[ch][i], -off)
        avg = {ch: stack[ch].mean(axis=0) for ch in range(4)}

        # Measure where the step actually lands: first sample of the aligned
        # average that departs from the pre-step baseline.  The gap between this
        # and the programmed baseline time is the fixed DAC->ADC loopback latency.
        meta = data.get("meta")
        onset = None
        lat_txt = ""
        if meta:
            a = avg[anchor]
            base = a[:max(8, L // 20)]
            mu, sd = base.mean(), base.std() + 1e-6
            hit = np.where(np.abs(a - mu) >
                           max(6.0 * sd, 0.05 * (a.max() - a.min())))[0]
            if len(hit):
                onset = int(hit[0])
                lat = onset - meta["step_ns"]           # ADC sample = 1 ns
                lat_txt = (f"; step @ {onset} ns (programmed baseline "
                           f"{meta['step_ns']:.0f} ns -> loopback latency "
                           f"~{lat:.0f} ns)")

        win = pg.GraphicsLayoutWidget()
        win.setWindowTitle(f"Triggered burst average (N={n})")
        win.setBackground("#101418")
        win.resize(900, 720)
        t = np.arange(L)
        for ch in range(4):
            p = win.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            for i in range(n):                       # faint raw bursts
                col = pg.mkColor(CH_COLORS[ch]); col.setAlpha(45)
                p.plot(t, stack[ch][i] * VOLTS_PER_COUNT,
                       pen=pg.mkPen(col, width=0.6))
            p.plot(t, avg[ch] * VOLTS_PER_COUNT,       # bold average
                   pen=pg.mkPen("#ffffff", width=1.4))
            if ch == anchor and onset is not None:     # markers on the anchor channel
                p.addLine(x=onset, pen=pg.mkPen("#E57373", width=1,
                                                style=QtCore.Qt.DashLine))    # measured step
                p.addLine(x=meta["step_ns"], pen=pg.mkPen("#81C784", width=1,
                                                          style=QtCore.Qt.DotLine))  # expected
        omin, omax = min(offs), max(offs)
        win.addLabel(f"N={n} bursts aligned+averaged  (x = ns @ 1 GS/s; "
                     f"measured offsets {omin}..{omax} samples{lat_txt})",
                     row=4, col=0)
        win.show()
        self._popup = win
        sub = self._save_burst(caps, avg, offs)
        where = f"  -> {sub}" if sub else "  (save FAILED)"
        self.status.setText(
            f"Triggered burst avg: N={n}, {L} samp/ch, jitter {omin}..{omax}"
            f"{lat_txt}.{where}")

    def _save_burst(self, caps, avg, offs):
        try:
            import os
            import time
            d = self.args.capture_dir
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"burst_{time.strftime('%Y%m%d_%H%M%S')}.npz")
            kw = {"offsets": np.asarray(offs)}
            for ch in range(4):
                kw[f"raw_ch{ch}"] = np.stack([c[ch] for c in caps])
                kw[f"avg_ch{ch}"] = np.asarray(avg[ch])
            np.savez_compressed(path, **kw)
            return path
        except Exception:
            return None

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

    def _burst_collect(self, nbytes, attempts=4):
        """Fresh full-rate capture (BCAP+BRDO) drained over UDP, with automatic
        retry. The readout occasionally drops a single packet (~6%, chip 1), so on
        an incomplete drain we re-run the WHOLE cycle (fresh socket + BRST register
        + BCAP + BRDO) up to `attempts` times -- exactly the manual re-press that
        already works -- with a settle between tries so the rapid re-issue does not
        race the A53 request handshake. Returns {ch.., '_cov', '_attempts'} or
        {'_err': str}."""
        last = {"_err": "no attempts ran"}
        for attempt in range(max(1, attempts)):
            if attempt > 0:
                time.sleep(0.4)        # human-paced settle; fast re-issue races A53
            last = self._burst_once(nbytes)
            if not (isinstance(last, dict) and "_err" in last):
                last["_attempts"] = attempt + 1
                return last
        return last

    def _burst_once(self, nbytes):
        """One fresh BCAP+BRDO+UDP drain (its own socket + BRST registration),
        identical to a single manual Collect press. Returns {ch.., '_cov'} or
        {'_err': str}."""
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
            # a cyclic stream and a one-shot burst can't share the DMA, so stop
            # streaming before BCAP (no-op if off).
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
            while time.time() < deadline and not asm.complete():
                # fast-exit only on a genuine mid-drain stall (a dropped packet
                # never arrives); never before the first packet.
                started = asm.coverage(0) > 0.0 or asm.coverage(1) > 0.0
                if started and asm.idle(0.6):
                    break
                time.sleep(0.05)
            if not asm.complete():
                return {"_err": (f"UDP drain incomplete for request {req}: "
                                 f"chip0 {100 * asm.coverage(0):.1f}%, "
                                 f"chip1 {100 * asm.coverage(1):.1f}% coverage")}
            chans = {}
            chans.update(decode_chip(asm.buf[0], 0))
            chans.update(decode_chip(asm.buf[1], 2))
            chans["_cov"] = min(asm.coverage(0), asm.coverage(1))
            return chans
        except Exception as exc:  # noqa: BLE001
            return {"_err": f"Ethernet collect exception: {exc}"}
        finally:
            if asm is not None:
                asm.close()

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
        tries = chans.pop("_attempts", 1)
        self._show_burst(chans, cov, tries)

    # ---- multisample trigger-synced burst over Ethernet (BCPT + BRDO) ----
    def _on_multisample(self):
        if not self.dac:
            return
        self.msamp_btn.setEnabled(False)
        self._resume_autosample = self.autosample_timer.isActive()
        if self._resume_autosample:
            self.autosample_timer.stop()
        self._resume_after_collect = self.tap is not None
        if self.tap:
            self.tap.close()
            self.tap = None
        nbytes, lbl = COLLECT_SIZE_OPTIONS[self.collect_mb_cb.currentIndex()]
        reps = self.msamp_reps.value()
        self.status.setText(f"multisample: BCPT {lbl} x {reps} trigger-synced reps...")
        self._bg(lambda: self.msamp_result.emit(self._multisample_once(nbytes, reps)))

    def _multisample_once(self, nbytes, reps):
        """BCPT + BRDO + one UDP drain, sliced into per-rep stacks and integer-
        aligned on ch0. Runs in a worker thread. Returns
        {'stack': {ch: float64[N,L]}, 'offs': [...], 'meta': {...}} or {'_err'}."""
        try:
            from burst_capture import Reassembler, decode_chip, parse_brdo_request
        except Exception as exc:  # noqa: BLE001
            return {"_err": f"burst_capture import failed: {exc}"}
        kb = max(1, nbytes // 1024)
        asm = None
        try:
            # a cyclic stream and a burst can't share the DMA
            self.dac.stop_stream()
            # Each rep waits in hardware for the player's next injection-window
            # start, so a slow LOOPING waveform (low-frequency square/sine) can
            # legitimately take reps x period. Wait long enough; if this still
            # times out the MB is likely mid-BCPT and needs to finish before it
            # answers anything else.
            bcpt = self.dac.cmd(f"BCPT {kb}k {reps}", ok=("OK BCPT", "ERR"),
                                timeout=180.0)
            if not bcpt:
                return {"_err": "BCPT reply timed out -- the board is likely "
                                "still capturing (reps x waveform period). Wait "
                                "for it to finish, then retry; for slow loop "
                                "waveforms use fewer reps or a Step (hold) "
                                "current instead"}
            if not bcpt.startswith("OK BCPT"):
                return {"_err": f"BCPT failed: {bcpt} -- the current player "
                                "must be configured and RUNNING first "
                                "(Chattering Demo Setup or current editor)"}
            meta = {}
            for tok in bcpt.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    try:
                        meta[k] = int(v, 0)
                    except ValueError:
                        pass
            if not all(k in meta for k in
                       ("reps", "bytes_per_rep", "stride", "total_per_chip")):
                return {"_err": f"unparseable BCPT reply: {bcpt!r}"}
            total = meta["total_per_chip"]
            try:
                asm = Reassembler(self.args.board_ip, self.args.cmd_port,
                                  self.args.local_ip, self.args.local_port, total)
            except OSError as exc:
                return {"_err": (f"UDP bind failed on {self.args.local_ip}:"
                                 f"{self.args.local_port}: {exc}")}
            if not asm.register(timeout=2.0):
                return {"_err": "BRST registration timed out (no BRST_READY from A53)"}
            brdo = self.dac.cmd("BRDO", ok=("OK BRDO", "ERR"))
            req = parse_brdo_request(brdo)
            if not brdo.startswith("OK BRDO") or req is None:
                return {"_err": f"BRDO failed: {brdo or '(no UART reply)'}"}
            asm.set_request_id(req)
            deadline = time.time() + max(10.0, (2.0 * total / 70.0e6) + 4.0)
            while time.time() < deadline and not asm.complete():
                started = asm.coverage(0) > 0.0 or asm.coverage(1) > 0.0
                if started and asm.idle(0.8):
                    break
                time.sleep(0.05)
            cov = min(asm.coverage(0), asm.coverage(1))
            if not asm.complete():
                return {"_err": (f"UDP drain incomplete: chip0 "
                                 f"{100 * asm.coverage(0):.1f}%, chip1 "
                                 f"{100 * asm.coverage(1):.1f}% coverage")}
            chans = {}
            chans.update(decode_chip(asm.buf[0], 0))
            chans.update(decode_chip(asm.buf[1], 2))

            # slice the strided DDR layout: per channel 4 bytes/sample
            spr = meta["bytes_per_rep"] // 4      # wanted samples per rep
            sps = meta["stride"] // 4             # rep-to-rep stride in samples
            n = meta["reps"]
            stack = {ch: np.stack([chans[ch][r * sps: r * sps + spr]
                                   for r in range(n)]).astype(np.float64)
                     for ch in range(4)}

            # integer alignment on the ch0 anchor via FFT cross-correlation to
            # the ensemble median (shifts are pure integers: DAC/ADC clocks are
            # locked; only the clk_50 CDCs move the window by a few beats).
            L = spr
            ref = np.median(stack[0], axis=0)
            ref = ref - ref.mean()
            fr = np.fft.rfft(ref, n=2 * L)
            maxlag = min(64, L // 4)
            offs = []
            for i in range(n):
                sig = stack[0][i] - stack[0][i].mean()
                cc = np.fft.irfft(fr * np.conj(np.fft.rfft(sig, n=2 * L)), n=2 * L)
                lags = np.concatenate([np.arange(0, maxlag + 1),
                                       np.arange(-maxlag, 0)])
                idx = np.concatenate([np.arange(0, maxlag + 1),
                                      np.arange(2 * L - maxlag, 2 * L)])
                off = int(lags[int(np.argmax(cc[idx]))])
                offs.append(off)
                if off:
                    for ch in range(4):
                        stack[ch][i] = np.roll(stack[ch][i], off)
            meta["cov"] = cov
            return {"stack": stack, "offs": offs, "meta": meta}
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return {"_err": f"multisample exception: {type(exc).__name__}: {exc}"}
        finally:
            if asm is not None:
                asm.close()

    def _show_multisample(self, res):
        self.msamp_btn.setEnabled(True)
        # resume auto-sampling / live stream if they were running before
        if getattr(self, "_resume_autosample", False) and self.dac \
                and self.stream_btn.isChecked():
            self._autosample_busy = False
            self.autosample_timer.start()
        self._resume_autosample = False
        if getattr(self, "_resume_after_collect", False) and self.dac:
            self.dac.start_stream(self.args.decim, self.args.cic)
            try:
                self.tap = StreamTap(self.args.board_ip, self.args.cmd_port,
                                     self.args.local_ip, self.args.local_port,
                                     self.args.window, self.args.rcvbuf)
            except OSError:
                self.tap = None
        if not isinstance(res, dict) or "stack" not in res:
            msg = res.get("_err", "no data") if isinstance(res, dict) else "no data"
            self.status.setText(f"Multisample capture failed: {msg}")
            return
        stack, offs, meta = res["stack"], res["offs"], res["meta"]
        n = meta["reps"]
        L = stack[0].shape[1]
        avg = {ch: stack[ch].mean(axis=0) for ch in range(4)}

        win = pg.GraphicsLayoutWidget()
        win.setWindowTitle(f"Multisample Ethernet burst (N={n}, {L} samp/ch)")
        win.setBackground("#101418")
        win.resize(900, 720)
        t = np.arange(L)
        draw_reps = n * L <= 2_000_000    # keep the popup responsive for huge grabs
        for ch in range(4):
            p = win.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            if draw_reps:
                for i in range(n):
                    col = pg.mkColor(CH_COLORS[ch]); col.setAlpha(45)
                    p.plot(t, stack[ch][i] * VOLTS_PER_COUNT,
                           pen=pg.mkPen(col, width=0.6))
            p.plot(t, avg[ch] * VOLTS_PER_COUNT,
                   pen=pg.mkPen("#ffffff", width=1.4))
        omin, omax = min(offs), max(offs)
        win.addLabel(f"N={n} hardware-triggered Ethernet bursts aligned+averaged "
                     f"(x = ns @ 1 GS/s; measured offsets {omin}..{omax} samples; "
                     f"UDP coverage {100 * meta.get('cov', 1.0):.1f}%)",
                     row=4, col=0)
        win.show()
        self._msamp_popup = win
        caps = [{ch: stack[ch][i].astype(np.int16) for ch in range(4)}
                for i in range(n)]
        sub = self._save_burst(caps, avg, offs)
        where = f"  -> {sub}" if sub else "  (save FAILED)"
        self.status.setText(
            f"Multisample: N={n} x {L} samp/ch over Ethernet, jitter "
            f"{omin}..{omax} samp.{where}")

    # ---- one-click chattering demo bring-up ----
    def _on_default_config(self):
        if not self.dac:
            return
        self.defaults_btn.setEnabled(False)
        self.status.setText("Applying chattering demo defaults...")

        def work():
            log = []

            def step(name, reply, *ok_starts):
                log.append(f"{name}: {reply or '(no reply)'}")
                return bool(reply) and any(reply.startswith(s) for s in ok_starts)

            try:
                ok = True
                # all neurons -> chattering profile (a/b/c/d + iconst=10 drive)
                ok &= step("NEUR", self.dac.cmd("NEUR all chattering",
                                                ok=("OK", "NEUR", "ERR")), "OK")
                # explicit special 0 mA injected input current (the player's
                # i_external adds on top of this)
                ok &= step("NEUR i", self.dac.set_neuron_param("all", "i", 0), "OK")
                # current source: 10 mA one-shot step, first 25% baseline,
                # full 1024-sample BRAM, hold mode (replayed per BCPT trigger)
                zc = CUR_WAVE_MAX // 4
                ok &= step("CURS", self.dac.program_current_step(
                    1, zc, CUR_WAVE_MAX - zc, 10.0, hold_last=True), "OK CURS")
                # crossbar: current source -> DAC0, neuron-0 spike -> DAC1
                ok &= step("NSRC0", self.dac.cmd("NSRC 0 current",
                                                 ok=("DAC xbar", "ERR")), "DAC xbar")
                ok &= step("NSRC1", self.dac.cmd("NSRC 1 spike0",
                                                 ok=("DAC xbar", "ERR")), "DAC xbar")
                # spike pulse: 50-sample trapezoid with 5-sample ramps
                ok &= step("PULS", self.dac.program_pulse(
                    build_trapezoid_pulse(50, ramp_samples=5)), "PULS")
                self.config_done.emit({"ok": bool(ok), "log": log})
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                log.append(f"exception: {type(e).__name__}: {e}")
                self.config_done.emit({"ok": False, "log": log})
        self._bg(work)

    def _on_config_done(self, res):
        self.defaults_btn.setEnabled(True)
        if res.get("ok"):
            # reflect the demo routing in the crossbar combos (combo changes
            # only refresh the preview; nothing is re-sent)
            if hasattr(self, "src_cbs") and len(self.src_cbs) >= 2:
                self.src_cbs[0].setCurrentText("Current source")
                self.src_cbs[1].setCurrentText("Spike 0")
            if hasattr(self, "prof_cbs"):
                for cb in self.prof_cbs:
                    cb.setCurrentText("chattering")
            self.status.setText(
                "Chattering demo ready: all neurons chattering (i=0), 10 mA "
                "step (25% baseline, 1024 samp, hold), current->DAC0, "
                "spike0->DAC1, 50-samp trapezoid pulse. Multi-Eth Trig Capture "
                "is now armed to use it.")
        else:
            tail = res["log"][-1] if res.get("log") else "unknown"
            self.status.setText(f"Demo defaults FAILED at: {tail}")

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
        tries = chans.pop("_attempts", 1)
        # show a 4x-wider window than the default scope span (the burst holds
        # plenty of samples; this just displays more of them)
        self._render_main(chans, 1.0e9, span=4 * self.args.time_span)
        retry_note = f"  ({tries} tries)" if tries > 1 else ""
        self.status.setText(
            f"auto-sample 1/s: {len(chans[0])} samples/ch @ 1 GS/s  "
            f"coverage {100 * cov:.0f}%{retry_note}  (not saved)")

    def _deint(self, x):
        """Display-time mod-4 interleave-baseline removal, when enabled.
        Full-rate data only -- never call on the decimated live stream."""
        if getattr(self, "deint_chk", None) is not None \
                and self.deint_chk.isChecked():
            return deinterleave_baseline(x)
        return x

    def _render_main(self, chans, fs, span=None):
        """Draw a captured {ch: int16[]} set into the 4 main plots, honoring
        the Time/FFT and Autoscale toggles (full rate, no decimation)."""
        if span is None:
            span = self.args.time_span
        for ch in range(4):
            full = self._deint(chans[ch].astype(np.float64))
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

    def _show_burst(self, chans, cov, tries=1):
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
            y = self._deint(chans[ch].astype(np.float64)).astype(np.float32) \
                * VOLTS_PER_COUNT
            p.plot(np.arange(len(y)), y, pen=pg.mkPen(CH_COLORS[ch], width=1.0))
        deint = "; interleave baseline removed" if self.deint_chk.isChecked() else ""
        win.addLabel(f"BCAP {size_lbl}/chip @ 1 GS/s  (x = ns; "
                     f"coverage {100 * cov:.1f}%{deint})", row=4, col=0)
        win.show()
        self._popup = win
        sub = save_capture(self.args.capture_dir, "eth", chans, 1.0e9,
                           coverage=cov, bytes_per_chip=nbytes)
        where = f"  -> {sub}" if sub else "  (save FAILED)"
        retry_note = f"  ({tries} tries)" if tries > 1 else ""
        self.status.setText(f"Ethernet burst: {len(chans[0])} samples/ch, "
                            f"coverage {100 * cov:.1f}%.{retry_note}{where}")

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
