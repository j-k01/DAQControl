#!/usr/bin/env python3
"""Real-time 4-channel ADC scope (PyQtGraph) with DAC control + UART capture.

ADC plot on the left; a control panel on the right. The board does NOT stream
on connect -- acquisition is opt-in:
  - Collect Ethernet: one-shot full-rate burst snapshot (BCAP+BRDO) in a popup,
    with a selectable size (MB/chip), saved to disk -- the reliable way to grab
    data.
  - Start/Stop Auto-Sample: takes a fresh small one-shot burst once per second
    and draws it in the main plots (off by default). Auto-samples are NOT saved.

  - Connection: pick the UART COM port and connect/reconnect (no streaming and
    no route/profile reprogramming); the GUI reads reg17, shows live routes,
    and restores the expected fast global neuron timing (period=1, dt=0.5).
  - Per channel (DAC0..3): pick any crossbar source -- Off / DDS / BRAM 0-3 /
    Spike 0-3 / Monitor 0-3 (per-neuron current) / Current source / Tag -- from
    the source dropdown, then hit "Confirm route" to commit. Confirming sends
    only NSRC; choosing a neuron-derived source never programs or resets that
    neuron. Neuron profiles are applied exclusively from the Neuron tab. The
    per-DAC status line confirms "OK — <source>" once the route is applied.
    The XBAR tab draws
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
      * Pulse shape: shows a spike pulse (<=4096 signed DAC samples) and lets you
        drag individual points up/down; "Program pulse" sends it via PULS to
        selected per-neuron shape banks. Spike 0..3 are four independent
        crossbar sources, each followed by its own gain/offset calibration.
  - CIC anti-alias (chip 1), one-shot autoscale, manual shared X / per-channel
    Y ranges, selectable Y-axis linking, rising-edge trigger, and Time/FFT views.
  - Legacy mod-4 baseline removal: optional display-time diagnostic for old
    captures or genuine small per-core offsets. Corrected FPGA images assemble
    LMFS=4211 transport bytes directly and do not need it to remove the former
    +/-7 mV artifact. Saved .npz files always keep the RAW samples.
  - UART Capture: grab an ADC snapshot over UART (PCAP) and pop up the 4
    channels -- works without the Ethernet path. UART Capture, Collect Ethernet
    and Auto-Sample sit in an always-visible Capture bar below the tabs, so they
    are reachable no matter which tab is selected.
  - Triggered acquisition: Trig Burst Avg (PCAPT) and Multi-Eth Trig Capture
    (BCPT) replay a fresh current program and neuron state for every repetition.
    Captures are averaged at their original hardware-aligned sample indices;
    correlation offsets are reported as diagnostics but never shift saved data.
    Live trigger averaging redraws only when a completed BCPT batch supplies new
    data. Its four plots share X and support visible-trace per-channel
    autoscaling, fixed ranges, and selectable Y-axis linking.

Prereqs: board programmed + A53 PS-eth app running; UART (default COM10); NIC at
192.168.2.1/24. See notes/dac_sources_howto.md.

  uv run python scripts/dac_scope_qt.py
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
from collections import deque

import numpy as np
import serial
import serial.tools.list_ports as list_ports
import pyqtgraph as pg
from mzi_heater_map import (
    BOARD_DEFINITIONS, HEATER_HARDWARE, HEATER_MAX_V, HEATER_MIN_V,
    MZI_NET_NAMES, ordered_heater_nets, validate_heater_voltages,
    validate_requested_heater_voltages,
)
from mzi_calibration import (
    PydaqMziController,
    analyze_optical_peaks,
    calibration_voltage_sequence,
    dominant_spike_polarity,
    estimate_main_lobe_lag,
    estimate_main_lobe_lag_auto_polarity,
    measure_spikes_at_indices,
    measure_reference_spikes,
    measure_spikes_in_windows,
    measure_spikes_with_loopback,
    measure_triggered_spikes,
    optical_schedule_from_loopback,
    parse_heater_voltages,
)
from optical_experiment import (
    create_experiment, load_manifest, save_heater_capture, update_manifest,
)
from process_optical_experiment import process_experiment
from tone_calibration import analyze_tone_capture, dds_phase_increment

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
# Hardware reg17 codes.  Keep this explicit: the GUI display order deliberately
# lists Current source before Tag, while the HDL/firmware encoding is Tag=14 and
# Current source=15.  SOURCE_LABELS.index() is therefore not a valid code map.
LABEL_TO_XBAR_CODE = {
    "Off": 0, "DDS": 1,
    "BRAM 0": 2, "BRAM 1": 3, "BRAM 2": 4, "BRAM 3": 5,
    "Spike 0": 6, "Spike 1": 7, "Spike 2": 8, "Spike 3": 9,
    "Monitor 0": 10, "Monitor 1": 11, "Monitor 2": 12, "Monitor 3": 13,
    "Tag": 14, "Current source": 15,
}
XBAR_CODE_TO_LABEL = {code: label for label, code in LABEL_TO_XBAR_CODE.items()}
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
LIVEAVG_DISPLAY_MAX_POINTS = 2048
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


def describe_burst_capture_failure(command, reply):
    """Turn BCAP/BCPT firmware replies into actionable, accurate diagnostics."""
    command = str(command).upper()
    reply = str(reply or "").strip()
    if not reply:
        return (f"{command} returned no UART reply after 180 s. The firmware "
                "is still wedged in capture polling or UART is desynchronized; "
                "recover/reprogram the board before retrying.")
    if "current player not running" in reply:
        return (f"{command} failed: {reply}. Program the current source and "
                "verify its RW16 RUNNING readback before capturing.")
    if "timeout (engine)" in reply:
        statuses = []
        for token in reply.split():
            if not (token.startswith("st0=") or token.startswith("st1=")):
                continue
            try:
                value = int(token.split("=", 1)[1], 0)
            except ValueError:
                continue
            statuses.append({
                "running": bool(value & (1 << 22)),
                "tready": bool(value & (1 << 20)),
                "axis": bool(value & (1 << 18)),
                "remaining": value & 0x3FFFF,
            })
        if len(statuses) == 2 and all(
                item["running"] and item["tready"] and item["axis"] and
                item["remaining"] for item in statuses):
            remaining = "/".join(str(item["remaining"]) for item in statuses)
            return (f"{command} failed: {reply}. The trigger fired and both "
                    "capture engines are ready, but ADC-valid beats did not "
                    f"complete (remaining beats {remaining}). This is an "
                    "ADC/JESD data-path failure, not a current-player failure; "
                    "reinitialize/reprogram the board and verify the ADC links.")
        return (f"{command} failed: {reply}. The hardware capture engine did "
                "not complete; this is not evidence that the current player "
                "failed to start.")
    if "no trigger" in reply:
        return (f"{command} failed: {reply}. The capture armed, but no "
                "current-player cycle-start trigger reached the ADC domain.")
    return f"{command} failed: {reply}"

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
# Named optical-heater states are separate from neuron profiles. Loading a
# state only stages it; the GUI requires an explicit Apply before commanding
# hardware.
HEATER_CONFIGS_PATH = os.path.join(
    os.path.expanduser("~"), ".daq_heater_configs.json")


def load_heater_configs():
    """Return validated named heater configurations from disk."""

    try:
        with open(HEATER_CONFIGS_PATH, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(raw, dict):
        return {}
    result = {}
    for name, config in raw.items():
        try:
            voltages = validate_heater_voltages(
                config.get("heater_voltages_v", {}))
            selected = ordered_heater_nets(
                config.get("selected_heaters", ()))
        except (AttributeError, TypeError, ValueError):
            continue
        if not selected:
            selected = (MZI_NET_NAMES[0],)
        result[str(name)] = {
            "heater_voltages_v": voltages,
            "selected_heaters": list(selected),
        }
    return result


def save_heater_configs(configs):
    """Atomically persist named heater configurations."""

    directory = os.path.dirname(os.path.abspath(HEATER_CONFIGS_PATH))
    os.makedirs(directory, exist_ok=True)
    temporary = HEATER_CONFIGS_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(configs, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, HEATER_CONFIGS_PATH)


def heater_mapping_payload(commanded_voltages=None):
    commanded = {net: None for net in MZI_NET_NAMES}
    for net, value in (commanded_voltages or {}).items():
        if net not in commanded:
            raise ValueError(f"unknown heater net {net!r}")
        if value is None:
            continue
        voltage = float(value)
        if not HEATER_MIN_V <= voltage <= HEATER_MAX_V:
            raise ValueError(
                f"{net} voltage {voltage:g} V is outside "
                f"{HEATER_MIN_V:g}..{HEATER_MAX_V:g} V")
        commanded[net] = voltage
    return {
        "schema": "daq_pydaq_heater_mapping",
        "schema_version": 1,
        "pico": "PICO-002",
        "safe_voltage_range_v": [HEATER_MIN_V, HEATER_MAX_V],
        "boards": {
            board: {"uid": data["uid"], "cs_pin": data["cs_pin"]}
            for board, data in BOARD_DEFINITIONS.items()
        },
        "heaters": {
            net: {**HEATER_HARDWARE[net],
                  "commanded_voltage_v": commanded[net]}
            for net in MZI_NET_NAMES
        },
    }
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


def trigger_offset_diagnostics(stack_by_ch, maxlag=64, max_samples=262144):
    """Measure trigger consistency without modifying any captured samples.

    ``stack_by_ch`` maps ADC channel -> ``[repetition, sample]``.  The channel
    with the clearest repeatable time-domain feature is selected automatically;
    this avoids treating ADC0 noise as a trigger when the injected current is
    zero/constant and only the neuron/spike channel contains timing information.

    The returned offsets are diagnostics only.  Triggered acquisitions are
    already aligned in hardware and must be averaged at their original sample
    indices--never ``roll``ed according to a noisy correlation estimate.
    """
    stacks = {int(ch): np.asarray(values, dtype=np.float64)
              for ch, values in stack_by_ch.items()}
    if not stacks:
        return {"anchor": None, "offsets": [], "observable": False,
                "score": 0.0, "signal_rms": 0.0, "noise_rms": 0.0}
    first = next(iter(stacks.values()))
    if first.ndim != 2 or first.shape[0] == 0 or first.shape[1] == 0:
        return {"anchor": None, "offsets": [], "observable": False,
                "score": 0.0, "signal_rms": 0.0, "noise_rms": 0.0}

    # Prefer a channel whose ensemble median has substantially more structure
    # than the rep-to-rep residual.  A flat/noise-only loopback scores below 1;
    # a current edge or repeatable spike train scores well above it.
    # Correlation is only a UI diagnostic; cap its work so a multi-megasample
    # BCPT does not allocate another multi-gigabyte median/residual workspace.
    # Trigger/current edges and initial neuron spikes are in the leading window.
    diag_length = min(first.shape[1], max(256, int(max_samples)))
    candidates = []
    for ch, values in stacks.items():
        if values.shape != first.shape:
            continue
        values = values[:, :diag_length]
        median = np.median(values, axis=0)
        centered = median - median.mean()
        signal_rms = float(np.sqrt(np.mean(centered * centered)))
        residual = values - median
        noise_rms = float(np.median(np.sqrt(np.mean(residual * residual, axis=1))))
        score = signal_rms / max(noise_rms, 1.0)
        candidates.append((score, signal_rms, -noise_rms, ch, median, noise_rms))

    if not candidates:
        return {"anchor": None, "offsets": [0] * first.shape[0],
                "observable": False, "score": 0.0,
                "signal_rms": 0.0, "noise_rms": 0.0}
    score, signal_rms, _, anchor, ref, noise_rms = max(candidates)
    # Requiring both absolute structure and repeatability keeps zero-current or
    # open-input noise from producing authoritative-looking arbitrary lags.
    observable = bool(signal_rms >= 4.0 and score >= 1.5)
    if not observable:
        return {"anchor": None, "offsets": [0] * first.shape[0],
                "observable": False, "score": score,
                "signal_rms": signal_rms, "noise_rms": noise_rms}

    values = stacks[anchor][:, :diag_length]
    nrep, length = values.shape
    ref = ref - ref.mean()
    nfft = 2 * length
    fr = np.fft.rfft(ref, n=nfft)
    maxlag = max(0, min(int(maxlag), length // 4))
    lags = np.concatenate([np.arange(0, maxlag + 1),
                           np.arange(-maxlag, 0)])
    indices = np.concatenate([np.arange(0, maxlag + 1),
                              np.arange(nfft - maxlag, nfft)])
    offsets = []
    for rep in range(nrep):
        sig = values[rep] - values[rep].mean()
        cc = np.fft.irfft(fr * np.conj(np.fft.rfft(sig, n=nfft)), n=nfft)
        offsets.append(int(lags[int(np.argmax(cc[indices]))]))
    return {"anchor": int(anchor), "offsets": offsets,
            "observable": True, "score": score,
            "signal_rms": signal_rms, "noise_rms": noise_rms}

def peak_envelope(values, max_points=LIVEAVG_DISPLAY_MAX_POINTS):
    """Return an extrema-preserving display envelope bounded by max_points.

    Raw captures are retained for averaging. This representation is used only
    for live display, where ordinary stride decimation can completely miss a
    narrow neuron spike.
    """
    y = np.asarray(values)
    n = y.size
    limit = max(2, int(max_points))
    if n <= limit:
        return np.arange(n), y

    bins = max(1, limit // 2)
    width = int(np.ceil(n / bins))
    starts = np.arange(0, n, width, dtype=np.int64)
    lows = np.minimum.reduceat(y, starts)
    highs = np.maximum.reduceat(y, starts)
    centers = np.minimum(starts + width // 2, n - 1)
    x = np.repeat(centers, 2)
    envelope = np.empty(2 * starts.size, dtype=y.dtype)
    envelope[0::2] = lows
    envelope[1::2] = highs
    return x, envelope


def event_preserving_trace(values, event_indices=(), max_points=4000,
                           event_radius=32):
    """Downsample an offline trace without drawing min/max vertical combs.

    A uniform background sample is augmented with the full neighborhood of
    every known event. Unlike peak_envelope, every returned X coordinate is
    unique, so a narrow pulse remains recognizable in a small sweep plot.
    """
    y = np.asarray(values)
    n = y.size
    if n <= max(2, int(max_points)):
        return np.arange(n), y

    events = np.asarray(event_indices, dtype=np.int64).reshape(-1)
    radius = max(0, int(event_radius))
    important = []
    for event in events:
        if 0 <= event < n:
            important.append(np.arange(
                max(0, event - radius), min(n, event + radius + 1),
                dtype=np.int64))
    important = (np.unique(np.concatenate(important))
                 if important else np.empty(0, dtype=np.int64))
    remaining = max(2, int(max_points) - important.size)
    background = np.linspace(0, n - 1, remaining, dtype=np.int64)
    indices = np.unique(np.concatenate((background, important)))
    return indices, y[indices]



# --------------------------------------------------------------- DAC content
def clamp_s16(v):
    return max(-DAC_FULLSCALE, min(DAC_FULLSCALE, int(round(v))))


def spike_cal_raw(height_v, offset_v, invert=False):
    """Convert per-neuron calibration volts -> (gain_q2_14, offset_counts) for
    firmware SCAL.  Height is the pulse amplitude assuming a FULL-SCALE
    programmed shape (it scales proportionally for smaller shapes); invert
    flips the pulse polarity (negative Q2.14 gain).  Raises ValueError for
    illegal combinations: values outside the DAC range, or a height + |offset|
    sum that would clip the pulse."""
    height_counts = int(round(max(0.0, float(height_v)) / VOLTS_PER_COUNT))
    offset_counts = int(round(float(offset_v) / VOLTS_PER_COUNT))
    if height_counts > DAC_FULLSCALE:
        raise ValueError(f"height > {DAC_VMAX:.3f} V DAC range")
    if abs(offset_counts) > DAC_FULLSCALE:
        raise ValueError(f"|offset| > {DAC_VMAX:.3f} V DAC range")
    if height_counts + abs(offset_counts) > DAC_FULLSCALE:
        total = (height_counts + abs(offset_counts)) * VOLTS_PER_COUNT
        raise ValueError(
            f"height + |offset| = {total:.3f} V would clip the "
            f"{DAC_VMAX:.3f} V DAC range")
    # Signed Q2.14: 0x4000 = +1.0x, 0xC000 = -1.0x. Clamp magnitude to >= 1
    # because raw 0 means "unity" in HW.
    gain = max(1, min(0x7FFF,
                      int(round(height_counts / DAC_FULLSCALE * 16384.0))))
    if invert:
        gain = -gain
    return gain & 0xFFFF, offset_counts


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
        self.last_error = ""
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
        """Set one XBar nibble and verify the actual reg17 readback.

        The acknowledgement alone only proves that firmware parsed NSRC.  The
        readback prevents the GUI from displaying a live route unless the
        register file really contains the requested source (and also proves
        that routing Spike 0 to DAC0 and DAC1 is legal: low byte 0x66).
        """
        token = LABEL_TO_NSRC[label]
        reply = self.cmd(f"NSRC {ch} {token}", ok=("DAC xbar", "ERR"))
        if not reply or reply.startswith("ERR"):
            return reply or "ERR no UART response to NSRC"
        rb = self.cmd("RDRW 17", ok=("REG17", "ERR"))
        if not rb.startswith("REG17"):
            return f"ERR XBar readback failed ({rb or 'no UART response'})"
        try:
            value = int(rb.split("=", 1)[1].strip(), 0)
        except (IndexError, ValueError):
            return f"ERR malformed XBar readback ({rb})"
        expected = LABEL_TO_XBAR_CODE[label]
        actual = (value >> (4 * int(ch))) & 0xF
        if actual != expected:
            return (f"ERR XBar readback DAC{ch}: requested {expected}, "
                    f"read {actual} (reg17=0x{value & 0xFFFF:04X})")
        return f"{reply}; reg17=0x{value & 0xFFFF:04X}"

    def set_dds_frequency(self, frequency_hz):
        increment, actual = dds_phase_increment(frequency_hz)
        reply = self.cmd(
            f"DDSI 0x{increment:06X}", ok=("DDS inc=", "ERR"))
        if not reply or reply.startswith("ERR"):
            return reply or "ERR no UART response to DDSI", increment, actual
        return reply, increment, actual

    def probe_firmware(self, timeout=2.0):
        """Return reg17 reply only when the DAQ MicroBlaze is actually alive."""
        return self.cmd("RDRW 17", ok=("REG17", "ERR"), timeout=timeout)

    def get_sources(self):
        """Read, decode, and return the four live reg17 crossbar routes."""
        reply = self.probe_firmware()
        if not reply.startswith("REG17"):
            raise RuntimeError(reply or "no UART response to RDRW 17")
        try:
            value = int(reply.split("=", 1)[1].strip(), 0)
            labels = [
                XBAR_CODE_TO_LABEL[(value >> (4 * ch)) & 0xF]
                for ch in range(4)
            ]
        except (IndexError, KeyError, ValueError) as exc:
            raise RuntimeError(f"malformed XBar readback: {reply}") from exc
        return labels, value & 0xFFFF

    def set_neuron(self, ch, profile):
        return self.cmd(f"NEUR {ch} {profile}", ok=("OK", "NEUR", "ERR"))

    def set_neuron_timing(self, dt_hex, period=1):
        """Apply global neuron cadence without touching profiles or routes.

        Both values matter to wall-clock spike rate. The FPGA power-on fallback
        (period=256, dt=0.0625) advances simulated time 2048x more slowly than
        the established GUI operating point (period=1, dt=0.5).
        """
        replies = [
            self.cmd(f"NEUR all period {int(period)}",
                     ok=("OK", "NEUR", "ERR")),
            self.cmd(f"NEUR all dt 0x{dt_hex:X}",
                     ok=("OK", "NEUR", "ERR")),
        ]
        return replies

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

    def start_stream(self, decim, usecic):
        return self.cmd(f"STRM {decim}{' cic' if usecic else ''}",
                        ok=("OK STRM", "ERR"))

    def stop_stream(self):
        return self.cmd("STRM STOP", ok=("OK STRM", "ERR"))

    def uart_capture(self, frames):
        """PCAP <frames> -> 4-channel snapshot. PCAP keeps RW3_DAC_PROGRAM_EN
        set so BRAM channels keep playing during the capture (plain CAPT clears
        it and BRAM would read as noise). It never changes the DAC crossbar."""
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

    def get_current_player_status(self, timeout=2.0):
        """Read and decode the live current-player control register (RW16)."""
        reply = self.cmd("RDRW 16", ok=("REG16", "ERR"), timeout=timeout)
        if not reply.startswith("REG16"):
            raise RuntimeError(
                f"current-player readback failed ({reply or 'no UART reply'})")
        try:
            value = int(reply.split("=", 1)[1].strip(), 0)
        except (IndexError, ValueError) as exc:
            raise RuntimeError(
                f"malformed current-player readback ({reply})") from exc
        return {
            "raw": value & 0xFFFFFFFF,
            "cps": value & 0xFFFF,
            "count": ((value >> 16) & 0x3FF) + 1,
            "hold_last": bool(value & (1 << 26)),
            "running": bool(value & (1 << 30)),
        }

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

    def program_pulse(self, counts, target="all"):
        """Program one independent neuron shaper, or broadcast to all four."""
        vals = [max(-32768, min(32767, int(round(v)))) for v in counts]
        n = len(vals)
        target = str(target).lower()
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write(f"PULS ch {target} bin {n}\n".encode("ascii"))
            self.s.flush()
            ack = self._readuntil(("PBRD", "ERR"))
            if not ack.startswith("PBRD"):
                return ack or ""
            self.s.write(struct.pack(f"<{n}h", *vals))
            self.s.flush()
            return self._readuntil(("PULS", "ERR"))

    def pulse_default(self, target="all"):
        """Reload the boot-default shape on one neuron or all four."""
        return self.cmd(f"PULS ch {target} default", ok=("PULS", "ERR"))

    def set_spike_cal(self, target, gain_q2_14, offset_counts):
        """Per-neuron spike-pulse calibration (firmware SCAL): gain signed
        Q2.14 (0x4000 = +1.000x, 0xC000 = -1.000x = inverted) scales the
        shaped pulse; offset (signed DAC counts) trims that neuron's DAC
        resting baseline. target = 0..3 or 'all'."""
        return self.cmd(
            f"SCAL {target} 0x{gain_q2_14 & 0xFFFF:04X} {int(offset_counts)}",
            ok=("OK SCAL", "ERR"))

    def _capture(self, cmd_str, frames):
        """Send a capture command, wait for the FE10CAFE sync, read
        frames*8*4 bytes, and decode to 4-channel int16 arrays (over UART)."""
        with self.lock:
            self.last_error = ""
            self.s.reset_input_buffer()
            self.s.write((cmd_str + "\n").encode("ascii"))
            self.s.flush()
            win = bytearray()
            prefix = bytearray()
            deadline = time.time() + 15
            while time.time() < deadline:
                b = self.s.read(1)
                if not b:
                    continue
                if len(prefix) < 512:
                    prefix += b
                win += b
                if len(win) > 4:
                    del win[0]
                if bytes(win) == CAPT_SYNC:
                    break
            else:
                msg = bytes(prefix).decode("ascii", errors="replace").strip()
                self.last_error = msg or "no UART response / no capture sync"
                return None
            need = frames * 8 * 4
            data = bytearray()
            while len(data) < need:
                chunk = self.s.read(need - len(data))
                if not chunk:
                    break
                data += chunk
        if len(data) < need:
            self.last_error = f"short UART payload: {len(data)}/{need} bytes"
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

        target_row = QtWidgets.QHBoxLayout()
        target_row.addWidget(QtWidgets.QLabel("program shaper"))
        self.target_cb = QtWidgets.QComboBox()
        self.target_cb.addItem("All neurons", "all")
        for neuron in range(4):
            self.target_cb.addItem(f"Neuron {neuron}", neuron)
        self.target_cb.setToolTip(
            "Each neuron has an independent 4096-point waveform bank and pulse length.")
        self.target_cb.currentIndexChanged.connect(self._info)
        target_row.addWidget(self.target_cb)
        target_row.addStretch(1)
        lay.addLayout(target_row)

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
            f"{self.target_cb.currentText()}: {len(ys)} samples ({len(ys)} ns), "
            f"nbeats={nb}, peak |{pk}| counts "
            f"({pk * VOLTS_PER_COUNT:.3f} V), trapezoid ramp/hold/ramp = "
            f"{ramp}/{hold}/{ramp}.  Drag points up/down to edit; "
            f"route a Spike source on a DAC to emit this pulse.")

    def _on_prog(self):
        dac = self.scope.dac
        if not dac:
            self.info.setText("connect a board first")
            return
        counts = self.editor.values()
        target = self.target_cb.currentData()
        self.prog_btn.setEnabled(False)

        def work():
            r = dac.program_pulse(counts, target)
            self.done.emit(bool(r and not r.startswith("ERR")), r or "(no reply)")
        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, ok, reply):
        self.prog_btn.setEnabled(True)
        self.info.setText(("OK — " if ok else "ERR — ") + (reply or "").strip())


class OpticalNeuronProfilesWindow(QtWidgets.QWidget):
    """Compact per-neuron profile editor used by the optical experiment."""

    def __init__(self, parent_scope, profiles):
        super().__init__(parent_scope, QtCore.Qt.Window)
        self.scope = parent_scope
        self.profiles = profiles
        self.setWindowTitle("Optical experiment neuron profiles")
        self.setMinimumWidth(380)
        layout = QtWidgets.QFormLayout(self)
        for neuron, profile in enumerate(self.profiles):
            profile.setToolTip(
                f"Profile for neuron {neuron}; optical setup forces i and "
                "iconst to 0 mA.")
            layout.addRow(f"Neuron {neuron}", profile)
        self.program_btn = QtWidgets.QPushButton("Program optical setup")
        self.program_btn.setToolTip(
            "Program these profiles with zero static current, the pulse-editor "
            "waveform, the square current source, the selected DAC0-DAC2 "
            "routes, and the fixed DAC3 reference route.")
        self.program_btn.clicked.connect(
            self.scope._on_mzi_program_test)
        layout.addRow("", self.program_btn)


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
            is_step = self._kind.startswith("Step")
            hold_last = is_step and not self._step_loop
            if is_step:
                r = dac.program_current_step(
                    cps, self._step_zero, self._step_high,
                    self.amp_spin.value(), hold_last=hold_last)
                expected_count = self._step_zero + self._step_high
            else:
                r = dac.program_current(ys, cps, hold_last=False)
                expected_count = len(ys)
            if not r or not r.startswith("OK"):
                self.done.emit(False, (r or "(no reply)") + gain_note)
                return
            try:
                player = dac.get_current_player_status()
            except RuntimeError as exc:
                self.done.emit(False, f"{r}; {exc}" + gain_note)
                return
            if (not player["running"] or player["cps"] != int(cps) or
                    player["count"] != expected_count or
                    player["hold_last"] != hold_last):
                self.done.emit(
                    False,
                    f"{r}; RW16 verification mismatch: "
                    f"0x{player['raw']:08X}, running={int(player['running'])}, "
                    f"cps={player['cps']}, count={player['count']}, "
                    f"hold={int(player['hold_last'])}" + gain_note)
                return
            self.done.emit(
                True, f"{r}; verified RUNNING RW16=0x{player['raw']:08X}" +
                gain_note)
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
        self.setMinimumHeight(24 * len(self.sources) + 24)
        self.setMinimumWidth(360)

    def set_applied(self, ch, idx):
        self.applied[ch] = idx
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
    liveavg_result = QtCore.pyqtSignal(object)  # one live-averaging BCPT batch
    config_done = QtCore.pyqtSignal(object)   # chattering-demo defaults result
    mzi_cal_progress = QtCore.pyqtSignal(int, int, float, float)
    mzi_setup_progress = QtCore.pyqtSignal(str)
    mzi_heater_programmed = QtCore.pyqtSignal(str, float)
    mzi_cal_result = QtCore.pyqtSignal(object)
    mzi_import_result = QtCore.pyqtSignal(object)
    scal_done = QtCore.pyqtSignal(str, bool, str)  # (neuron, ok, detail) spike-cal result

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.dac = None
        self.tap = None
        self.paused = False
        self.fft_view = False
        self.time_y_ranges = [(-0.95, 0.95) for _ in range(4)]
        self.fft_y_ranges = [(-90.0, 5.0) for _ in range(4)]
        self.time_x_range = None       # None = fit the current waveform
        self.fft_x_range = None
        self._mzi_heater_buttons = {}
        self._mzi_heater_voltages = {net: None for net in MZI_NET_NAMES}
        self._mzi_staged_heater_voltages = None
        self._mzi_selected_heaters = {MZI_NET_NAMES[0]}
        self._mzi_heater_configs = load_heater_configs()
        self._mzi_result_datasets = {}
        self._mzi_active_config_name = None
        self._mzi_heater_controls = []
        self.main_y_link = [False] * 4
        self.liveavg_auto_y = [True] * 4
        self.liveavg_y_ranges = [(-0.95, 0.95) for _ in range(4)]
        self.liveavg_y_link = [False] * 4
        self.liveavg_x_range = None    # samples; all four plots share X
        self.trigger = True
        self._popup = None
        self._cur_win = None        # CurrentSourceWindow (lazily created)
        self._mzi_controller = PydaqMziController()
        self._mzi_cancel = threading.Event()
        self._mzi_running = False
        self._pulse_win = None      # PulseShapeWindow (lazily created)
        self._mzi_neuron_win = None
        # crossbar routing state: solid lines / summary reflect the APPLIED route
        # (only updated once a route is committed), never the staged dropdown.
        self.custom_profiles = load_custom_profiles()
        self._applied_label = [None, None, None, None]    # live source per DAC
        self._dac_prog = [None, None, None, None]          # source label in flight
        self._prog_profile_name = {}                       # target -> profile being set
        self.neuron_applied_profile = {"0": None, "1": None, "2": None, "3": None}
        self.setWindowTitle("DAC scope + control (PyQtGraph)")
        self.resize(1360, 880)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        self.workspace_tabs = QtWidgets.QTabWidget()
        root.addWidget(self.workspace_tabs)
        scope_workspace = QtWidgets.QWidget()
        scope_root = QtWidgets.QHBoxLayout(scope_workspace)
        scope_root.setContentsMargins(0, 0, 0, 0)
        experiment_workspace = QtWidgets.QWidget()
        experiment_layout = QtWidgets.QVBoxLayout(experiment_workspace)
        results_workspace = QtWidgets.QWidget()
        results_layout = QtWidgets.QVBoxLayout(results_workspace)
        self.workspace_tabs.addTab(scope_workspace, "Scope and control")
        self.workspace_tabs.addTab(experiment_workspace, "Optical experiment")
        self.workspace_tabs.addTab(results_workspace, "Optical results")

        # ---- plots ----
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("#101418")
        scope_root.addWidget(self.glw, stretch=4)
        self.plots, self.curves = [], []
        for ch in range(4):
            p = self.glw.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            # One shared time/frequency axis: zooming or panning any waveform
            # moves every channel to the identical X window.  Y remains fully
            # independent so each ADC can use its own fixed Y range.
            if ch > 0:
                p.setXLink(self.plots[0])
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
        scope_root.addWidget(rightw)
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
        self._build_mzi_calibration_panel(experiment_layout)
        self._build_mzi_results_panel(results_layout)

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

        # Per-channel crossbar source. Selection only STAGES a route; nothing
        # reaches the board until "Confirm route" sends NSRC. Neuron profiles
        # intentionally live only on the Neuron tab.
        self.src_cbs = []
        self.dac_btns, self.dac_status = [], []
        initial = args.initial if args.initial in SOURCE_LABELS else "DDS"
        for ch in range(4):
            box = QtWidgets.QGroupBox(f"DAC{ch}")
            g = QtWidgets.QGridLayout(box)
            src = QtWidgets.QComboBox()
            src.addItems(SOURCE_LABELS)
            src.setCurrentText(initial)
            src.setToolTip("16:4 DAC crossbar (reg17): route any source to this "
                           "DAC. Routing does not program or reset neurons.")
            src.currentIndexChanged.connect(self._refresh_xbar_preview)
            g.addWidget(QtWidgets.QLabel("source"), 0, 0)
            g.addWidget(src, 0, 1, 1, 2)
            self.src_cbs.append(src)
            btn = QtWidgets.QPushButton("Confirm route")
            btn.clicked.connect(self._make_program_dac_cb(ch))
            g.addWidget(btn, 1, 0, 1, 3)
            self.dac_btns.append(btn)
            st = QtWidgets.QLabel("not routed")
            st.setStyleSheet("color:#9fb3c8; font-size:11px;")
            st.setWordWrap(True)
            g.addWidget(st, 2, 0, 1, 3)
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

        # Per-neuron spike calibration (firmware SCAL): height scales the
        # shaped pulse (stated for a full-scale shape), offset trims that
        # neuron's DAC resting baseline. Illegal (clipping) values are refused.
        cal_box = QtWidgets.QGroupBox("Per-neuron spike calibration")
        cal_grid = QtWidgets.QGridLayout(cal_box)
        cal_grid.addWidget(QtWidgets.QLabel("height (V)"), 0, 1)
        cal_grid.addWidget(QtWidgets.QLabel("offset (V)"), 0, 2)
        cal_grid.addWidget(QtWidgets.QLabel("inv"), 0, 3)
        self.scal_height = {}
        self.scal_offset = {}
        self.scal_invert = {}
        self.scal_btns = {}
        self.scal_status = {}
        for n in range(4):
            key = str(n)
            cal_grid.addWidget(QtWidgets.QLabel(f"neuron {n}"), n + 1, 0)
            h = QtWidgets.QDoubleSpinBox()
            h.setRange(0.0, DAC_VMAX)
            h.setDecimals(3)
            h.setSingleStep(0.010)
            h.setValue(DAC_VMAX)
            h.setToolTip("Pulse height for a full-scale programmed shape: "
                         f"{DAC_VMAX:.3f} V = gain 1.000x (unchanged). "
                         "Smaller shapes scale proportionally.")
            o = QtWidgets.QDoubleSpinBox()
            o.setRange(DAC_VMIN, DAC_VMAX)
            o.setDecimals(3)
            o.setSingleStep(0.010)
            o.setValue(0.0)
            o.setToolTip("DC baseline trim for this neuron's DAC output "
                         "(applied continuously, between pulses too). "
                         "height + |offset| must stay within the DAC range.")
            inv = QtWidgets.QCheckBox()
            inv.setToolTip("Invert this neuron's pulse polarity "
                           "(negative Q2.14 gain)")
            b = QtWidgets.QPushButton("Apply")
            b.setToolTip(f"Send SCAL {n} (signed Q2.14 gain + offset counts)")
            b.clicked.connect(self._make_scal_cb(key))
            st = QtWidgets.QLabel("-")
            st.setStyleSheet("color:#9fb3c8; font-size:10px;")
            st.setWordWrap(True)
            cal_grid.addWidget(h, n + 1, 1)
            cal_grid.addWidget(o, n + 1, 2)
            cal_grid.addWidget(inv, n + 1, 3)
            cal_grid.addWidget(b, n + 1, 4)
            cal_grid.addWidget(st, n + 1, 5)
            self.scal_height[key] = h
            self.scal_offset[key] = o
            self.scal_invert[key] = inv
            self.scal_btns[key] = b
            self.scal_status[key] = st
        right.addWidget(cal_box)

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
        self.auto_btn = QtWidgets.QPushButton("Autoscale once")
        self.auto_btn.setToolTip(
            "Fit each channel to the traces currently on screen, then keep "
            "those Y ranges fixed.")
        self.auto_btn.clicked.connect(self._on_autoscale_once)
        self.range_btn = QtWidgets.QPushButton("Axes...")
        self.range_btn.setToolTip(
            "Set shared X limits, independent Y limits, and optional Y-axis "
            "linking for ADC0-3 in Time and FFT views.")
        self.range_btn.clicked.connect(self._on_y_ranges)
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
        # to a fresh current/neuron restart.  Average at the original sample
        # indices; correlation is diagnostic only and never shifts the data.
        self.burst_n = QtWidgets.QSpinBox()
        self.burst_n.setRange(2, 256)
        self.burst_n.setValue(16)
        self.burst_n.setPrefix("N=")
        self.burst_btn = QtWidgets.QPushButton("Trig Burst Avg")
        self.burst_btn.setToolTip(
            "Repeat PCAPT with a fresh deterministic current/neuron restart for "
            "every capture. Repetitions are averaged exactly as captured; the "
            "GUI reports correlation offsets but never software-shifts them.")
        self.burst_btn.clicked.connect(self._on_burst)
        self.burst_step_chk = QtWidgets.QCheckBox("fit step")
        # Off by default: when on, replace the loaded current waveform with a
        # window-fit step. Acquisition never changes any crossbar route.
        self.burst_step_chk.setChecked(False)
        self.burst_step_chk.setToolTip(
            "Before the burst, program a one-shot current step sized to the "
            "capture window (0 baseline, amp for most of the window, 0 settle "
            "after). This does not change the XBAR. Select Current source on "
            "the desired DAC yourself if you want a direct loopback trace.")
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
            "the Collect size above), hardware-aligned and averaged without "
            "software shifting, saved as burst_*.npz. Each repetition reloads "
            "the neuron state and restarts the current waveform from sample 0. "
            "Requires the current source to be configured and running "
            "(e.g. via the Chattering Demo Setup button or the current editor).")
        self.msamp_btn.clicked.connect(self._on_multisample)
        # Live triggered averaging: back-to-back small BCPT batches feed a
        # rolling window; a persistent plot shows the mean and optional ghosts.
        self.liveavg_btn = QtWidgets.QPushButton("Start Live Trig Avg")
        self.liveavg_btn.setCheckable(True)
        self.liveavg_btn.setToolTip(
            "Continuously run hardware-triggered BCPT batches and display the "
            "running average of the selected 8-16 captures (with optional "
            "ghosts). Runs until toggled off; per-rep size = the Collect "
            "size above. Requires the current player running.")
        self.liveavg_btn.clicked.connect(self._on_liveavg_toggle)
        self.liveavg_window = QtWidgets.QSpinBox()
        self.liveavg_window.setRange(8, 16)
        self.liveavg_window.setValue(16)
        self.liveavg_window.setPrefix("window=")
        self.liveavg_window.setToolTip(
            "Number of most recent trigger-aligned captures retained in the "
            "rolling average; old captures are discarded continuously.")
        self.liveavg_downsample_chk = QtWidgets.QCheckBox("Downsample plot")
        self.liveavg_downsample_chk.setChecked(True)
        self.liveavg_downsample_chk.setToolTip(
            "Use a peak-preserving display envelope. Raw captures and the "
            "rolling average remain full resolution.")
        self.liveavg_downsample_chk.toggled.connect(
            self._on_liveavg_display_options)
        self.liveavg_ghosts_chk = QtWidgets.QCheckBox("Show ghosts")
        self.liveavg_ghosts_chk.setChecked(False)
        self.liveavg_ghosts_chk.setToolTip(
            "Overlay the individual captures retained in the rolling window.")
        self.liveavg_ghosts_chk.toggled.connect(
            self._on_liveavg_display_options)
        self.liveavg_axes_btn = QtWidgets.QPushButton("Live avg axes...")
        self.liveavg_axes_btn.setToolTip(
            "Choose per-channel visible-trace autoscale or fixed Y limits, "
            "link selected Y axes, and set one shared X window. Visible ghost "
            "captures participate in autoscaling.")
        self.liveavg_axes_btn.clicked.connect(self._on_liveavg_axes)
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
        og.addWidget(self.auto_btn, 2, 0)
        og.addWidget(self.range_btn, 2, 1)
        og.addWidget(self.trig_chk, 3, 0, 1, 2)
        og.addWidget(self.rb_time, 4, 0)
        og.addWidget(self.rb_fft, 4, 1)
        og.addWidget(self.run_btn, 5, 0, 1, 2)
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
        ag.addWidget(self.liveavg_window, 6, 0)
        ag.addWidget(self.liveavg_btn, 6, 1)
        ag.addWidget(self.liveavg_downsample_chk, 7, 0)
        ag.addWidget(self.liveavg_ghosts_chk, 7, 1)
        ag.addWidget(self.liveavg_axes_btn, 8, 0, 1, 2)
        ag.addWidget(self.defaults_btn, 9, 0, 1, 2)
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
        self.mzi_cal_progress.connect(self._on_mzi_cal_progress)
        self.mzi_setup_progress.connect(self.mzi_status.setText)
        self.mzi_heater_programmed.connect(self._on_mzi_heater_programmed)
        self.mzi_cal_result.connect(self._on_mzi_cal_result)
        self.mzi_import_result.connect(self._on_mzi_import_result)

        self.msamp_result.connect(self._show_multisample)
        self.liveavg_result.connect(self._on_liveavg_batch)
        self.config_done.connect(self._on_config_done)
        self.scal_done.connect(self._on_scal_done)
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
        # Live triggered averaging: one background capture thread at a time.
        self._liveavg_busy = False
        self._liveavg_last_snapshot = None
        self._liveavg_win = None

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

    def _set_applied_route(self, ch, label):
        """Record a route as live-on-the-board and redraw the solid line +
        summary. Called only after the board confirms (NSRC OK)."""
        self._applied_label[ch] = label
        idx = SOURCE_LABELS.index(label) if label in SOURCE_LABELS else None
        if hasattr(self, "xbar_view"):
            self.xbar_view.set_applied(ch, idx)
        self._refresh_xbar_summary()

    def _refresh_xbar_summary(self):
        if not hasattr(self, "xbar_summary"):
            return
        rows = []
        for ch in range(4):
            lbl = self._applied_label[ch]
            if lbl is None:
                rows.append(f"DAC{ch} ← (not applied)")
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
        combos = list(self.neuron_profile_cbs.values())
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
            live_sources, xbar_value = self.dac.get_sources()
            timing_replies = self.dac.set_neuron_timing(
                NEURON_DT_OPTIONS[self.dt_cb.currentIndex()][1], period=1)
            if any(not reply or reply.startswith("ERR")
                   for reply in timing_replies):
                raise RuntimeError(
                    "DAQ firmware did not accept neuron timing setup: "
                    + " | ".join(timing_replies))
        except Exception as e:  # noqa: BLE001
            self.conn_lbl.setText(f"UART connect failed: {e}")
            self.conn_lbl.setStyleSheet("color:#E57373;")
            # Release COM immediately. Do not call DacControl.close() here: it
            # sends another firmware command, which would add a second timeout
            # precisely when the firmware probe has already failed.
            if self.dac:
                try:
                    self.dac.s.close()
                except Exception:  # noqa: BLE001
                    pass
            self.dac = None
            return
        self.connect_btn.setText("Reconnect")
        self._set_controls_enabled(True)
        # Connection preserves reg17 and all per-neuron profiles. Only the two
        # global timing values are restored to the established fast operating
        # point; firmware applies those with mask=0, so no profile is reloaded.
        for ch, label in enumerate(live_sources):
            self.src_cbs[ch].blockSignals(True)
            self.src_cbs[ch].setCurrentText(label)
            self.src_cbs[ch].blockSignals(False)
            self.dac_status[ch].setText(
                f"LIVE — {label} (reg17=0x{xbar_value:04X})")
            self.dac_status[ch].setStyleSheet("color:#81C784; font-size:11px;")
            self._set_applied_route(ch, label)
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
        self.status.setText(
            "Connected. Neuron timing: period=1, dt=0.5 (routes/profiles "
            "preserved). Use Collect Ethernet for a saved snapshot, or "
            "Auto-Sample for 1/s live view.")

    def _set_controls_enabled(self, on):
        self._controls_enabled = on
        for w in (self.wf_btn, self.cic_chk, self.capt_btn, self.collect_btn,
                  self.collect_mb_cb, self.stream_btn, self.dt_cb,
                  self.burst_btn, self.burst_n, self.burst_step_chk, self.burst_amp,
                  self.msamp_reps, self.msamp_btn, self.liveavg_btn,
                  self.liveavg_downsample_chk, self.liveavg_ghosts_chk,
                  self.defaults_btn, self.mzi_program_btn, self.mzi_quick_btn,
                  self.mzi_point_btn, self.mzi_run_btn,
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
        for b in self.dac_btns:
            b.setEnabled(on)
        for key in self.scal_btns:
            self.scal_btns[key].setEnabled(on)
            self.scal_height[key].setEnabled(on)
            self.scal_offset[key].setEnabled(on)
            self.scal_invert[key].setEnabled(on)

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

    # ---- per-neuron spike calibration (SCAL) ----
    def _make_scal_cb(self, key):
        def cb():
            self._apply_spike_cal(key)
        return cb

    def _apply_spike_cal(self, key):
        if not self.dac:
            return
        try:
            gain, off = spike_cal_raw(self.scal_height[key].value(),
                                      self.scal_offset[key].value(),
                                      invert=self.scal_invert[key].isChecked())
        except ValueError as e:
            self.scal_done.emit(key, False, str(e))
            return
        self.scal_btns[key].setEnabled(False)

        def work():
            try:
                r = self.dac.set_spike_cal(key, gain, off)
                self.scal_done.emit(
                    key, bool(r) and r.startswith("OK SCAL"), r or "(no reply)")
            except Exception as e:  # noqa: BLE001
                self.scal_done.emit(key, False, f"{type(e).__name__}: {e}")
        self._bg(work)

    def _on_scal_done(self, key, ok, text):
        if key not in self.scal_btns:
            return
        if self._controls_enabled:
            self.scal_btns[key].setEnabled(True)
        st = self.scal_status[key]
        st.setText("OK" if ok else text)
        st.setStyleSheet("color:#81C784; font-size:10px;" if ok
                         else "color:#E57373; font-size:10px;")

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

    def _open_mzi_pulse_window(self):
        self._open_pulse_window()
        self._pulse_win.target_cb.setCurrentIndex(0)

    def _open_mzi_neuron_window(self):
        if self._mzi_neuron_win is None:
            self._mzi_neuron_win = OpticalNeuronProfilesWindow(
                self, self.mzi_profiles)
        self._mzi_neuron_win.show()
        self._mzi_neuron_win.raise_()
        self._mzi_neuron_win.activateWindow()

    def _optical_pulse_counts(self):
        if self._pulse_win is None:
            return list(PULSE_DEFAULT)
        return [clamp_s16(value) for value in self._pulse_win.editor.values()]

    def _program_dac(self, ch):
        """Commit DACn's staged source using NSRC only.

        Routing a Spike/Monitor output must never program or reset its neuron;
        neuron configuration belongs exclusively to the Neuron tab.
        """
        if not self.dac:
            return
        label = self.src_cbs[ch].currentText()
        self._dac_prog[ch] = label
        self.dac_btns[ch].setEnabled(False)
        self.dac_status[ch].setText(f"routing {label}…")
        self.dac_status[ch].setStyleSheet("color:#FFB74D; font-size:11px;")

        def work():
            r = self.dac.set_source(ch, label)
            ok = bool(r and not r.startswith("ERR"))
            detail = label if ok else f"{label}: {r or 'no UART response'}"
            self.dac_done.emit(ch, ok, detail)
        self._bg(work)

    def _on_dac_done(self, ch, ok, detail):
        self.dac_btns[ch].setEnabled(True)
        if ok:
            self.dac_status[ch].setText(f"OK — {detail}")
            self.dac_status[ch].setStyleSheet("color:#81C784; font-size:11px;")
            # The route is now live: promote the staged pick to a solid line.
            label = self._dac_prog[ch]
            if label is not None:
                self._set_applied_route(ch, label)
        else:
            self.dac_status[ch].setText(f"ERR — {detail} not set")
            self.dac_status[ch].setStyleSheet("color:#E57373; font-size:11px;")
        self._dac_prog[ch] = None

    def _on_cic(self, on):
        if self.dac:
            self._bg(lambda: self.dac.set_cic(on))

    def _on_run(self):
        self.paused = not self.paused
        self.run_btn.setText("Run" if self.paused else "Pause")

    def _on_view(self, _checked):
        self.fft_view = self.rb_fft.isChecked()
        self._apply_view_ranges()

    def _on_autoscale_once(self):
        ranges = list(self.fft_y_ranges if self.fft_view else self.time_y_ranges)
        min_span = 10.0 if self.fft_view else 0.04
        for ch, curve in enumerate(self.curves):
            _x, values = curve.getData()
            fitted = self._fitted_y_range(values, min_span=min_span)
            if fitted is not None:
                ranges[ch] = fitted
        if self.fft_view:
            self.fft_y_ranges = ranges
        else:
            self.time_y_ranges = ranges
        self._apply_view_ranges()

    def _on_y_ranges(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Main plot axes")
        layout = QtWidgets.QVBoxLayout(dlg)
        link_box = QtWidgets.QGroupBox("Lock selected Y axes together")
        link_lay = QtWidgets.QHBoxLayout(link_box)
        link_checks = []
        for ch in range(4):
            check = QtWidgets.QCheckBox(f"ADC{ch}")
            check.setChecked(self.main_y_link[ch])
            link_lay.addWidget(check)
            link_checks.append(check)
        layout.addWidget(link_box)
        tabs = QtWidgets.QTabWidget()
        editors = {}
        x_editors = {}

        for mode, ranges, suffix, bounds, decimals, step, x_range, x_suffix, x_bounds in (
                ("Time", self.time_y_ranges, " V", (-100.0, 100.0), 4, 0.05,
                 self.time_x_range, " s", (0.0, 3600.0)),
                ("FFT", self.fft_y_ranges, " dB", (-300.0, 100.0), 1, 5.0,
                 self.fft_x_range, " Hz", (0.0, 1.0e9))):
            page = QtWidgets.QWidget()
            page_lay = QtWidgets.QVBoxLayout(page)
            grid = QtWidgets.QGridLayout()
            grid.addWidget(QtWidgets.QLabel("Channel"), 0, 0)
            grid.addWidget(QtWidgets.QLabel("Minimum"), 0, 1)
            grid.addWidget(QtWidgets.QLabel("Maximum"), 0, 2)
            mode_editors = []
            for ch, (lo, hi) in enumerate(ranges):
                lo_box = QtWidgets.QDoubleSpinBox()
                hi_box = QtWidgets.QDoubleSpinBox()
                for box, value in ((lo_box, lo), (hi_box, hi)):
                    box.setRange(*bounds)
                    box.setDecimals(decimals)
                    box.setSingleStep(step)
                    box.setSuffix(suffix)
                    box.setValue(value)
                grid.addWidget(QtWidgets.QLabel(f"ADC{ch}"), ch + 1, 0)
                grid.addWidget(lo_box, ch + 1, 1)
                grid.addWidget(hi_box, ch + 1, 2)
                mode_editors.append((lo_box, hi_box))
            editors[mode] = mode_editors
            page_lay.addLayout(grid)

            x_box = QtWidgets.QGroupBox("Shared X axis (all four waveforms)")
            x_grid = QtWidgets.QGridLayout(x_box)
            x_auto = QtWidgets.QCheckBox("Auto fit data")
            x_auto.setChecked(x_range is None)
            default_x = ((0.0, self.args.time_span / 1.0e9)
                         if mode == "Time" else (0.0, 0.5e9))
            x_lo = QtWidgets.QDoubleSpinBox()
            x_hi = QtWidgets.QDoubleSpinBox()
            for box, value in zip((x_lo, x_hi), x_range or default_x):
                box.setRange(*x_bounds)
                box.setDecimals(9 if mode == "Time" else 1)
                box.setSuffix(x_suffix)
                box.setValue(value)
                box.setEnabled(not x_auto.isChecked())
            x_auto.toggled.connect(
                lambda checked, boxes=(x_lo, x_hi):
                    [box.setEnabled(not checked) for box in boxes])
            x_grid.addWidget(x_auto, 0, 0, 1, 2)
            x_grid.addWidget(QtWidgets.QLabel("Minimum"), 1, 0)
            x_grid.addWidget(x_lo, 1, 1)
            x_grid.addWidget(QtWidgets.QLabel("Maximum"), 2, 0)
            x_grid.addWidget(x_hi, 2, 1)
            page_lay.addWidget(x_box)
            x_editors[mode] = (x_auto, x_lo, x_hi)
            tabs.addTab(page, mode)
        tabs.setCurrentIndex(1 if self.fft_view else 0)
        layout.addWidget(tabs)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        values = {}
        for mode, mode_editors in editors.items():
            values[mode] = [(lo.value(), hi.value()) for lo, hi in mode_editors]
            if any(lo >= hi for lo, hi in values[mode]):
                QtWidgets.QMessageBox.warning(
                    self, "Invalid Y range",
                    f"Every {mode} minimum must be below its maximum.")
                return
        x_values = {}
        for mode, (auto, lo, hi) in x_editors.items():
            x_values[mode] = None if auto.isChecked() else (lo.value(), hi.value())
            if x_values[mode] is not None and lo.value() >= hi.value():
                QtWidgets.QMessageBox.warning(
                    self, "Invalid X range",
                    f"The {mode} X minimum must be below its maximum.")
                return
        self.time_y_ranges = values["Time"]
        self.fft_y_ranges = values["FFT"]
        self.time_x_range = x_values["Time"]
        self.fft_x_range = x_values["FFT"]
        self.main_y_link = [check.isChecked() for check in link_checks]
        self._apply_view_ranges()
        if getattr(self, "_liveavg_plots", None):
            mean_traces = (
                self._liveavg_last_snapshot.get("mean_traces", {})
                if self._liveavg_last_snapshot else {})
            self._apply_liveavg_axes(mean_traces)

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
            self._bg(lambda: self.dac.set_neuron_timing(dt, period=1))

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
            why = self.dac.last_error if self.dac else "no connection"
            self.status.setText(f"UART capture failed: {why}")
            return
        win = pg.GraphicsLayoutWidget()
        win.setWindowTitle("UART ADC capture")
        win.setBackground("#101418")
        win.resize(900, 700)
        for ch in range(4):
            p = win.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            self._apply_time_plot_range(p, ch)
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
        # Snapshot the confirmed route only for optional direct-step latency
        # annotation. Capture must never alter it.
        current_route_ch = next(
            (ch for ch, label in enumerate(self._applied_label)
             if label == "Current source"), None)
        self.burst_btn.setEnabled(False)
        self.status.setText(f"Triggered burst: 0/{n} ...")

        def work():
            try:
                meta = None
                if do_step:
                    # Program the injected waveform only. The user's XBAR
                    # configuration is experiment state and must be preserved.
                    reply, meta = self.dac.program_step_for_capture(frames, amp)
                    if "OK CURW" not in (reply or ""):
                        self.burst_result.emit(
                            {"error": f"step program failed: {reply!r}"})
                        return
                    meta["current_route_ch"] = current_route_ch
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
        # de-interleaved float counts, truncated to the common length
        stack = {ch: np.stack([self._deint(c[ch]).astype(np.float64)[:L]
                               for c in caps]) for ch in range(4)}
        # The FPGA now transports sample zero with the observation packet,
        # pre-arms the burst FIFO, and resets neuron state/dividers on the same
        # current-player restart.  Preserve that hardware alignment.  The lag
        # estimate is displayed only as a health diagnostic and automatically
        # chooses a useful channel (important when ADC0 current is exactly 0).
        diag = trigger_offset_diagnostics(stack)
        offs = diag["offsets"]
        avg = {ch: stack[ch].mean(axis=0) for ch in range(4)}

        # If the user explicitly routed Current source to a DAC, measure where
        # its direct loopback step lands. Fit-step itself never changes XBAR.
        meta = data.get("meta")
        onset = None
        lat_txt = ""
        current_route_ch = meta.get("current_route_ch") if meta else None
        if meta and current_route_ch is not None:
            a = avg[current_route_ch]
            base = a[:max(8, L // 20)]
            mu, sd = base.mean(), base.std() + 1e-6
            hit = np.where(np.abs(a - mu) >
                           max(6.0 * sd, 0.05 * (a.max() - a.min())))[0]
            if len(hit):
                onset = int(hit[0])
                lat = onset - meta["step_ns"]           # ADC sample = 1 ns
                lat_txt = (f"; ADC{current_route_ch} step @ {onset} ns "
                           f"(programmed baseline "
                           f"{meta['step_ns']:.0f} ns -> loopback latency "
                           f"~{lat:.0f} ns)")
        elif meta:
            lat_txt = "; step programmed; Current source not routed for direct readback"

        win = pg.GraphicsLayoutWidget()
        win.setWindowTitle(f"Triggered burst average (N={n})")
        win.setBackground("#101418")
        win.resize(900, 720)
        t = np.arange(L)
        for ch in range(4):
            p = win.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            self._apply_time_plot_range(p, ch)
            for i in range(n):                       # faint raw bursts
                col = pg.mkColor(CH_COLORS[ch]); col.setAlpha(45)
                p.plot(t, stack[ch][i] * VOLTS_PER_COUNT,
                       pen=pg.mkPen(col, width=0.6))
            p.plot(t, avg[ch] * VOLTS_PER_COUNT,       # bold average
                   pen=pg.mkPen("#ffffff", width=1.4))
            if ch == current_route_ch and onset is not None:
                p.addLine(x=onset, pen=pg.mkPen("#E57373", width=1,
                                                style=QtCore.Qt.DashLine))    # measured step
                p.addLine(x=meta["step_ns"], pen=pg.mkPen("#81C784", width=1,
                                                          style=QtCore.Qt.DotLine))  # expected
        omin, omax = min(offs), max(offs)
        diag_txt = (f"diagnostic ADC{diag['anchor']} offsets {omin}..{omax} samples"
                    if diag["observable"] else
                    "offset diagnostic unavailable (no repeatable timing feature)")
        win.addLabel(f"N={n} hardware-aligned bursts averaged without shifting "
                     f"(x = ns @ 1 GS/s; {diag_txt}{lat_txt})",
                     row=4, col=0)
        win.show()
        self._popup = win
        sub = self._save_burst(caps, avg, offs, diag)
        where = f"  -> {sub}" if sub else "  (save FAILED)"
        health = (f"diagnostic ADC{diag['anchor']} lag {omin}..{omax}"
                  if diag["observable"] else "lag not observable")
        self.status.setText(
            f"Triggered burst avg: N={n}, {L} samp/ch, {health}"
            f"{lat_txt}.{where}")

    def _save_burst(self, caps, avg, offs, diag=None):
        try:
            import os
            import time
            d = self.args.capture_dir
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"burst_{time.strftime('%Y%m%d_%H%M%S')}.npz")
            # Keep the historical `offsets` key for readers of older burst
            # files, but explicitly label its new diagnostic-only meaning.
            kw = {"offsets": np.asarray(offs),
                  "offsets_diagnostic": np.asarray(offs),
                  "software_alignment_applied": np.asarray(False)}
            if diag:
                kw["trigger_anchor"] = np.asarray(
                    -1 if diag.get("anchor") is None else diag["anchor"])
                kw["trigger_observable"] = np.asarray(diag.get("observable", False))
                kw["trigger_score"] = np.asarray(diag.get("score", 0.0))
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
        """Capture once, then retry only the UDP drain of that same DDR image."""
        return self._burst_once(nbytes, drain_attempts=attempts)

    def _burst_once(self, nbytes, drain_attempts=1):
        """One BCAP followed by one or more BRDO drains of the same DDR data."""
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
            # A failed engine can outlast the ordinary four-second UART wait.
            # Wait for its real diagnostic instead of claiming no UART reply
            # while MicroBlaze is still polling the capture hardware.
            bcap = self.dac.cmd(f"BCAP {kb}k", ok=("OK BCAP", "ERR"),
                                timeout=180.0)
            if not bcap.startswith("OK BCAP"):
                return {"_err": describe_burst_capture_failure("BCAP", bcap)}
            tries = max(1, int(drain_attempts))
            req = None
            for attempt in range(tries):
                if attempt:
                    time.sleep(0.4)
                asm.begin_request()
                if not asm.register(timeout=2.0):
                    return {"_err": "BRST registration timed out "
                                    "(no BRST_READY from A53)"}
                brdo = self.dac.cmd("BRDO", ok=("OK BRDO", "ERR"))
                req = parse_brdo_request(brdo)
                if not brdo.startswith("OK BRDO") or req is None:
                    return {"_err": f"BRDO failed: {brdo or '(no UART reply)'}"}
                asm.set_request_id(req)
                deadline = time.time() + max(8.0, (2.0 * bpc / 70.0e6) + 2.0)
                while time.time() < deadline and not asm.complete():
                    # Fast-exit only on a genuine mid-drain stall; never before
                    # the first packet.
                    started = asm.coverage(0) > 0.0 or asm.coverage(1) > 0.0
                    if started and asm.idle(0.6):
                        break
                    time.sleep(0.05)
                if asm.complete():
                    break
            if not asm.complete():
                return {"_err": (f"UDP drain incomplete after {tries} attempts "
                                 f"(last request {req}): "
                                 f"chip0 {100 * asm.coverage(0):.1f}%, "
                                 f"chip1 {100 * asm.coverage(1):.1f}% coverage")}
            chans = {}
            chans.update(decode_chip(asm.buf[0], 0))
            chans.update(decode_chip(asm.buf[1], 2))
            chans["_cov"] = min(asm.coverage(0), asm.coverage(1))
            chans["_attempts"] = attempt + 1
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

    # Optical-weight acquisition deliberately reuses _multisample_once(), the
    # proven BCPT trigger-aligned Ethernet path from the Display tab.
    def _build_mzi_calibration_panel(self, layout):
        layout.setContentsMargins(8, 8, 8, 8)
        workspace = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        workspace.setChildrenCollapsible(False)
        layout.addWidget(workspace, 1)

        def scroll_column(minimum_width):
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            panel = QtWidgets.QWidget()
            column = QtWidgets.QVBoxLayout(panel)
            column.setContentsMargins(6, 6, 6, 6)
            column.setSizeConstraint(QtWidgets.QLayout.SetNoConstraint)
            panel.setSizePolicy(
                QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            scroll.setWidget(panel)
            scroll.setMinimumWidth(minimum_width)
            workspace.addWidget(scroll)
            return column

        heater_column = scroll_column(370)
        setup_column = scroll_column(350)
        result_column = scroll_column(420)
        workspace.setStretchFactor(0, 2)
        workspace.setStretchFactor(1, 2)
        workspace.setStretchFactor(2, 3)

        pico_box = QtWidgets.QGroupBox("1. PICO-002 / PyDAQ")
        pico_layout = QtWidgets.QVBoxLayout(pico_box)
        pico_buttons = QtWidgets.QVBoxLayout()
        self.mzi_pico_init_btn = QtWidgets.QPushButton("Initialize PICO-002")
        self.mzi_pico_init_btn.setToolTip(
            "Connect through the FPGA, identify PICO-002, reset its command parser, "
            "and define EVAL0/EVAL1 for PyDAQ. This does not flash Pico firmware.")
        self.mzi_pico_init_btn.clicked.connect(self._on_mzi_init_pico)
        self.mzi_pico_test_btn = QtWidgets.QPushButton("Test connection")
        self.mzi_pico_test_btn.setToolTip(
            "Run five non-destructive PICO-002 handshake probes without changing heaters.")
        self.mzi_pico_test_btn.clicked.connect(self._on_mzi_test_pico)
        pico_buttons.addWidget(self.mzi_pico_init_btn)
        pico_buttons.addWidget(self.mzi_pico_test_btn)
        pico_layout.addLayout(pico_buttons)
        self.mzi_pico_status = QtWidgets.QLabel("Pico: not initialized")
        self.mzi_pico_status.setWordWrap(True)
        self.mzi_pico_status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        pico_layout.addWidget(self.mzi_pico_status)
        heater_column.addWidget(pico_box)

        target = QtWidgets.QGroupBox("Sweep heater and manual heater control")
        self.mzi_heater_group = target
        target_layout = QtWidgets.QVBoxLayout(target)
        sweep_hint = QtWidgets.QLabel(
            "Checked heaters are swept together at the same voltage. Click individual heaters, "
            "or use a row or column header to select a group.")
        sweep_hint.setWordWrap(True)
        sweep_hint.setStyleSheet("color:#9fb3c8; font-size:11px;")
        target_layout.addWidget(sweep_hint)

        heater_grid = QtWidgets.QGridLayout()
        heater_grid.setHorizontalSpacing(4)
        heater_grid.setVerticalSpacing(4)
        for column in range(1, 7):
            header = QtWidgets.QToolButton()
            header.setText(str(column))
            header.setToolTip(f"Select or clear every heater in column {column}.")
            header.clicked.connect(
                lambda _checked=False, col=column:
                self._on_mzi_group_toggle(
                    [f"h_{row}_{col}" for row in range(1, 10)]))
            heater_grid.addWidget(header, 0, column)
        for row in range(1, 10):
            header = QtWidgets.QToolButton()
            header.setText(str(row))
            header.setToolTip(f"Select or clear every heater in row {row}.")
            header.clicked.connect(
                lambda _checked=False, selected_row=row:
                self._on_mzi_group_toggle(
                    [f"h_{selected_row}_{column}" for column in range(1, 7)]))
            heater_grid.addWidget(header, row, 0)
            for column in range(1, 7):
                net = f"h_{row}_{column}"
                button = QtWidgets.QToolButton()
                button.setCheckable(True)
                button.setMinimumSize(50, 40)
                button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
                button.setChecked(net in self._mzi_selected_heaters)
                button.setProperty("mziNet", net)
                button.toggled.connect(
                    lambda checked, heater=net:
                    self._on_mzi_heater_toggled(heater, checked))
                heater_grid.addWidget(button, row, column)
                self._mzi_heater_buttons[net] = button
                self._mzi_heater_controls.append(button)
        target_layout.addLayout(heater_grid)

        voltage_row = QtWidgets.QGridLayout()
        self.mzi_selected_count = QtWidgets.QLabel("1 selected")
        self.mzi_selected_voltage = QtWidgets.QDoubleSpinBox()
        self.mzi_selected_voltage.setRange(HEATER_MIN_V, HEATER_MAX_V)
        self.mzi_selected_voltage.setValue(0.0)
        self.mzi_selected_voltage.setDecimals(4)
        self.mzi_selected_voltage.setSingleStep(0.01)
        self.mzi_selected_voltage.setSuffix(" V")
        self.mzi_selected_voltage.setToolTip(
            "Manual heater voltage used by Set selected and Capture at Manual Voltage.")
        self.mzi_set_selected_btn = QtWidgets.QPushButton("Set selected")
        self.mzi_set_selected_btn.setToolTip(
            "Write the manual voltage to every blue-selected heater. Green means acknowledged.")
        self.mzi_set_selected_btn.clicked.connect(
            lambda: self._on_mzi_set_heaters("selected"))
        self.mzi_zero_selected_btn = QtWidgets.QPushButton("Zero chosen")
        self.mzi_zero_selected_btn.setToolTip(
            "Write 0 V to every blue-selected heater.")
        self.mzi_zero_selected_btn.clicked.connect(
            lambda: self._on_mzi_set_heaters("zero_selected"))
        self.mzi_zero_all_btn = QtWidgets.QPushButton("Zero all")
        self.mzi_zero_all_btn.setToolTip(
            "Write 0 V to all 54 heaters after PICO-002 acknowledges each command.")
        self.mzi_zero_all_btn.clicked.connect(
            lambda: self._on_mzi_set_heaters("zero_all"))
        voltage_row.addWidget(self.mzi_selected_count, 0, 0)
        voltage_row.addWidget(self.mzi_selected_voltage, 0, 1)
        voltage_row.addWidget(self.mzi_set_selected_btn, 1, 0, 1, 2)
        voltage_row.addWidget(self.mzi_zero_selected_btn, 2, 0)
        voltage_row.addWidget(self.mzi_zero_all_btn, 2, 1)
        target_layout.addLayout(voltage_row)
        self.mzi_write_status = QtWidgets.QLabel(
            "No heater writes acknowledged in this session.")
        self.mzi_write_status.setWordWrap(True)
        self.mzi_write_status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        target_layout.addWidget(self.mzi_write_status)
        self._mzi_heater_controls.extend([
            self.mzi_selected_voltage, self.mzi_set_selected_btn,
            self.mzi_zero_selected_btn, self.mzi_zero_all_btn,
            self.mzi_pico_init_btn, self.mzi_pico_test_btn])
        heater_column.addWidget(target)
        self._refresh_mzi_heater_map()

        config_box = QtWidgets.QGroupBox("Heater configurations")
        config_layout = QtWidgets.QGridLayout(config_box)
        self.mzi_config_name = QtWidgets.QLineEdit()
        self.mzi_config_name.setPlaceholderText("configuration name")
        self.mzi_config_combo = QtWidgets.QComboBox()
        self.mzi_config_combo.addItems(sorted(self._mzi_heater_configs))
        self.mzi_config_save_btn = QtWidgets.QPushButton("Save")
        self.mzi_config_save_btn.setToolTip(
            "Save the currently acknowledged heater voltages as a named configuration.")
        self.mzi_config_save_btn.clicked.connect(self._on_mzi_config_save)
        self.mzi_config_load_btn = QtWidgets.QPushButton("Load")
        self.mzi_config_load_btn.clicked.connect(self._on_mzi_config_load)
        self.mzi_config_apply_btn = QtWidgets.QPushButton("Apply")
        self.mzi_config_apply_btn.setToolTip(
            "Send every staged voltage to PICO-002 and update the map after each acknowledgement.")
        self.mzi_config_apply_btn.clicked.connect(
            lambda: self._on_mzi_set_heaters("staged"))
        self.mzi_config_delete_btn = QtWidgets.QPushButton("Delete")
        self.mzi_config_delete_btn.clicked.connect(self._on_mzi_config_delete)
        self.mzi_mapping_export_btn = QtWidgets.QPushButton("Export")
        self.mzi_mapping_export_btn.setToolTip(
            "Export the logical-heater to physical-board/channel mapping.")
        self.mzi_mapping_export_btn.clicked.connect(self._on_mzi_mapping_export)
        config_layout.addWidget(self.mzi_config_name, 0, 0, 1, 2)
        config_layout.addWidget(self.mzi_config_save_btn, 1, 0)
        config_layout.addWidget(self.mzi_config_combo, 1, 1)
        config_layout.addWidget(self.mzi_config_load_btn, 2, 0)
        config_layout.addWidget(self.mzi_config_apply_btn, 2, 1)
        config_layout.addWidget(self.mzi_config_delete_btn, 3, 0)
        config_layout.addWidget(self.mzi_mapping_export_btn, 3, 1)
        self.mzi_config_status = QtWidgets.QLabel("No configuration staged")
        self.mzi_config_status.setWordWrap(True)
        self.mzi_config_status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        config_layout.addWidget(self.mzi_config_status, 4, 0, 1, 2)
        self._mzi_heater_controls.extend([
            self.mzi_config_name, self.mzi_config_combo,
            self.mzi_config_save_btn, self.mzi_config_load_btn,
            self.mzi_config_apply_btn, self.mzi_config_delete_btn,
            self.mzi_mapping_export_btn])
        heater_column.addWidget(config_box)
        heater_column.addStretch(1)

        stimulus = QtWidgets.QGroupBox("2. Experiment setup")
        sf = QtWidgets.QFormLayout(stimulus)
        self.mzi_mode = QtWidgets.QComboBox()
        self.mzi_mode.addItem("Mode A - Spiking", "spike")
        self.mzi_mode.addItem("Mode B - Pure tone", "tone")
        self.mzi_mode.setToolTip(
            "Spiking uses neuron pulses and BCPT. Pure tone uses the shared "
            "DDS and a phase-zero BCPD trigger at every averaged capture.")
        sf.addRow("Experiment mode", self.mzi_mode)

        self.mzi_tone_panel = QtWidgets.QGroupBox("Pure-tone stimulus")
        tone_form = QtWidgets.QFormLayout(self.mzi_tone_panel)
        self.mzi_tone_frequency = QtWidgets.QDoubleSpinBox()
        self.mzi_tone_frequency.setRange(10.0, 10000.0)
        self.mzi_tone_frequency.setValue(100.0)
        self.mzi_tone_frequency.setDecimals(3)
        self.mzi_tone_frequency.setSuffix(" kHz")
        self.mzi_tone_frequency.valueChanged.connect(
            self._ensure_mzi_tone_capture_length)
        self.mzi_tone_frequency.setToolTip(
            "Shared DDS frequency. It remains identical at every heater voltage.")
        tone_form.addRow("Frequency", self.mzi_tone_frequency)
        tone_routes = QtWidgets.QHBoxLayout()
        self.mzi_tone_inputs = []
        for channel in range(3):
            enabled = QtWidgets.QCheckBox(f"DAC{channel}")
            enabled.setChecked(channel == 0)
            enabled.setToolTip(
                f"Route the shared full-scale, zero-offset DDS tone to DAC{channel}.")
            self.mzi_tone_inputs.append(enabled)
            tone_routes.addWidget(enabled)
        tone_routes.addWidget(QtWidgets.QLabel("DAC3: DDS reference"))
        tone_routes.addStretch(1)
        tone_form.addRow("Photonic inputs", tone_routes)
        tone_note = QtWidgets.QLabel(
            "0 V offset, full bipolar DAC range. DAC3 is always looped to "
            "ADC3 as the electrical amplitude and phase reference.")
        tone_note.setWordWrap(True)
        tone_note.setStyleSheet("color:#4FC3F7; font-size:11px;")
        tone_form.addRow("", tone_note)
        self.mzi_tone_panel.setVisible(False)
        sf.addRow(self.mzi_tone_panel)

        self.mzi_profiles = []
        for neuron in range(4):
            profile = QtWidgets.QComboBox()
            profile.addItems(NEURON_PROFILES)
            profile.setCurrentText("regular")
            self.mzi_profiles.append(profile)
        editor_row = QtWidgets.QHBoxLayout()
        self.mzi_neuron_editor_btn = QtWidgets.QPushButton("Neuron profiles...")
        self.mzi_neuron_editor_btn.setToolTip(
            "Open per-neuron optical profile selection and programming.")
        self.mzi_neuron_editor_btn.clicked.connect(
            self._open_mzi_neuron_window)
        self.mzi_pulse_editor_btn = QtWidgets.QPushButton("Pulse shape...")
        self.mzi_pulse_editor_btn.setToolTip(
            "Open the shared trapezoid/freeform pulse editor. The optical "
            "setup uses its current waveform for all four neuron shapers.")
        self.mzi_pulse_editor_btn.clicked.connect(
            self._open_mzi_pulse_window)
        editor_row.addWidget(self.mzi_neuron_editor_btn)
        editor_row.addWidget(self.mzi_pulse_editor_btn)
        sf.addRow("Editors", editor_row)

        current_summary = QtWidgets.QLabel(
            "Current source: Square, 15.0 mA, 5.0 kHz, 50% duty. "
            "This is injected into all neurons; every neuron has i = iconst = 0 mA.")
        current_summary.setWordWrap(True)
        current_summary.setStyleSheet("color:#81C784; font-size:11px;")
        sf.addRow("Stimulus", current_summary)


        route_summary = QtWidgets.QLabel(
            "DAC0-DAC2 are independent optical stimulus paths. DAC3 always "
            "carries Spike 3 and is physically looped to ADC3 as the clean "
            "timing reference. Every ADC is captured and averaged separately.")
        route_summary.setWordWrap(True)
        route_summary.setStyleSheet("color:#81C784; font-size:11px;")
        sf.addRow("Reference path", route_summary)
        source_row = QtWidgets.QHBoxLayout()
        self.mzi_dac_sources = []
        self.mzi_dac_invert = []
        for channel in range(3):
            source_row.addWidget(QtWidgets.QLabel(f"DAC{channel}"))
            source = QtWidgets.QComboBox()
            source.addItems(SOURCE_LABELS)
            source.setCurrentText(f"Spike {channel}")
            source.setToolTip(
                f"Normal 16:4 crossbar source for DAC{channel}. The selection "
                "is staged until Program setup is pressed.")
            self.mzi_dac_sources.append(source)
            source_row.addWidget(source)
            invert = QtWidgets.QCheckBox("Invert")
            invert.setToolTip(
                f"Invert DAC{channel}'s selected spike signal through the existing "
                "per-neuron SCAL control.")
            self.mzi_dac_invert.append(invert)
            source_row.addWidget(invert)
        reference_label = QtWidgets.QLabel("DAC3: Spike 3 reference")
        reference_label.setStyleSheet("color:#4FC3F7;")
        reference_label.setToolTip(
            "DAC3 is fixed to Spike 3 because ADC3 is the timing reference.")
        source_row.addWidget(reference_label)
        reference_invert = QtWidgets.QCheckBox("Invert")
        reference_invert.setToolTip(
            "Invert the DAC3 Spike 3 timing reference through SCAL.")
        self.mzi_dac_invert.append(reference_invert)
        source_row.addWidget(reference_invert)
        source_row.addStretch(1)
        sf.addRow("DAC crossbar", source_row)
        self.mzi_program_btn = QtWidgets.QPushButton("Program setup")
        self.mzi_program_btn.setToolTip(
            "Program the 5 kHz square current, all four zero-bias neurons, all pulse shapers, "
            "and the selected DAC0-DAC2 crossbar routes. DAC3 stays on "
            "Spike 3 as the reference. Heater voltages are not changed.")
        self.mzi_program_btn.clicked.connect(self._on_mzi_program_test)
        sf.addRow("", self.mzi_program_btn)
        self.mzi_mode.currentIndexChanged.connect(self._on_mzi_mode_changed)
        setup_column.addWidget(stimulus)

        capture = QtWidgets.QGroupBox("3. Capture and sweep")
        cf = QtWidgets.QFormLayout(capture)
        self.mzi_experiment_name = QtWidgets.QLineEdit("optical_sweep")
        self.mzi_experiment_name.setPlaceholderText("experiment name")
        cf.addRow("Experiment name", self.mzi_experiment_name)
        self.mzi_capture_size = QtWidgets.QComboBox()
        for capture_bytes, label in COLLECT_SIZE_OPTIONS[:4]:
            self.mzi_capture_size.addItem(label, capture_bytes)
        self.mzi_capture_size.addItem("768 KB (192k/ch)", 768 * 1024)
        self.mzi_capture_size.setCurrentIndex(0)
        self.mzi_capture_size.setToolTip(
            "Raw samples saved for each of all four ADCs in every trigger-aligned capture.")
        cf.addRow("Capture length", self.mzi_capture_size)
        self.mzi_spacing = QtWidgets.QComboBox()
        self.mzi_spacing.addItem("Uniform voltage", "voltage")
        self.mzi_spacing.addItem("Uniform heater power (V^2)", "power")
        self.mzi_spacing.addItem("Explicit voltage list", "explicit")
        cf.addRow("Sweep spacing", self.mzi_spacing)
        self.mzi_vstart = QtWidgets.QDoubleSpinBox()
        self.mzi_vstart.setRange(HEATER_MIN_V, HEATER_MAX_V)
        self.mzi_vstart.setValue(0.0)
        self.mzi_vstart.setDecimals(4)
        self.mzi_vstart.setSuffix(" V")
        self.mzi_vstop = QtWidgets.QDoubleSpinBox()
        self.mzi_vstop.setRange(HEATER_MIN_V, HEATER_MAX_V)
        self.mzi_vstop.setValue(HEATER_MAX_V)
        self.mzi_vstop.setDecimals(4)
        self.mzi_vstop.setSuffix(" V")
        self.mzi_points = QtWidgets.QSpinBox()
        self.mzi_points.setRange(3, 201)
        self.mzi_points.setValue(20)
        range_row = QtWidgets.QHBoxLayout()
        range_row.addWidget(self.mzi_vstart)
        range_row.addWidget(QtWidgets.QLabel("to"))
        range_row.addWidget(self.mzi_vstop)
        range_row.addWidget(QtWidgets.QLabel("points"))
        range_row.addWidget(self.mzi_points)
        cf.addRow("Voltage range", range_row)
        self.mzi_voltage_list = QtWidgets.QLineEdit(
            "0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.0")
        self.mzi_voltage_list.setEnabled(False)
        cf.addRow("Explicit list", self.mzi_voltage_list)

        self.mzi_settle = QtWidgets.QDoubleSpinBox()
        self.mzi_settle.setRange(0.0, 1000.0)
        self.mzi_settle.setValue(20.0)
        self.mzi_settle.setSuffix(" ms")
        self.mzi_settle.setToolTip(
            "Delay after an acknowledged heater write and before the first capture.")
        self.mzi_reps = QtWidgets.QSpinBox()
        self.mzi_reps.setRange(4, 64)
        self.mzi_reps.setValue(16)
        capture_row = QtWidgets.QHBoxLayout()
        capture_row.addWidget(QtWidgets.QLabel("settle"))
        capture_row.addWidget(self.mzi_settle)
        capture_row.addWidget(QtWidgets.QLabel("captures"))
        capture_row.addWidget(self.mzi_reps)
        cf.addRow("Per voltage", capture_row)
        self.mzi_reverse = QtWidgets.QCheckBox("Reverse")
        self.mzi_reverse.setChecked(True)
        self.mzi_reverse.setToolTip(
            "After the forward sweep, visit the same voltages in reverse order.")
        cf.addRow("", self.mzi_reverse)
        self.mzi_restore = QtWidgets.QDoubleSpinBox()
        self.mzi_restore.setRange(HEATER_MIN_V, HEATER_MAX_V)
        self.mzi_restore.setValue(0.0)
        self.mzi_restore.setDecimals(4)
        self.mzi_restore.setSuffix(" V")
        self.mzi_restore.setToolTip(
            "Voltage written to every checked sweep heater when the sweep ends or fails.")
        cf.addRow("Restore after sweep", self.mzi_restore)
        setup_column.addWidget(capture)

        advanced_btn = QtWidgets.QPushButton("Advanced spike detection")
        advanced_btn.setCheckable(True)
        advanced_btn.setToolTip(
            "Show thresholds used only when extracting spike boundaries from the 16-capture average.")
        setup_column.addWidget(advanced_btn)
        self.mzi_detection_panel = QtWidgets.QGroupBox("Spike detection")
        detection_form = QtWidgets.QFormLayout(self.mzi_detection_panel)
        self.mzi_detect_sigma = QtWidgets.QDoubleSpinBox()
        self.mzi_detect_sigma.setRange(1.0, 20.0)
        self.mzi_detect_sigma.setValue(5.0)
        self.mzi_detect_sigma.setDecimals(1)
        self.mzi_detect_sigma.setToolTip(
            "A spike starts only after the averaged output exceeds this many robust noise sigmas.")
        self.mzi_boundary_sigma = QtWidgets.QDoubleSpinBox()
        self.mzi_boundary_sigma.setRange(0.0, 10.0)
        self.mzi_boundary_sigma.setValue(2.0)
        self.mzi_boundary_sigma.setDecimals(1)
        self.mzi_boundary_sigma.setToolTip(
            "A detected spike boundary ends after the averaged output returns inside this noise band.")
        self.mzi_min_seed = QtWidgets.QSpinBox()
        self.mzi_min_seed.setRange(1, 32)
        self.mzi_min_seed.setValue(2)
        self.mzi_min_seed.setToolTip(
            "Consecutive above-threshold samples required to reject isolated noise excursions.")

        self.mzi_optical_max_lag = QtWidgets.QSpinBox()
        self.mzi_optical_max_lag.setRange(1, 8192)
        self.mzi_optical_max_lag.setValue(1024)
        self.mzi_optical_max_lag.setSuffix(" samples")
        self.mzi_optical_max_lag.setToolTip(
            "Maximum fixed optical-path latency searched relative to ADC3.")
        self.mzi_loopback_padding = QtWidgets.QSpinBox()
        self.mzi_loopback_padding.setRange(0, 256)
        self.mzi_loopback_padding.setValue(8)
        self.mzi_loopback_padding.setSuffix(" samples")
        self.mzi_loopback_padding.setToolTip(
            "Extra samples included on each side of every ADC3-derived spike "
            "window when measuring the optical channel.")
        detection_form.addRow("Detection threshold", self.mzi_detect_sigma)
        detection_form.addRow("Boundary threshold", self.mzi_boundary_sigma)
        detection_form.addRow("Minimum seed samples", self.mzi_min_seed)
        detection_form.addRow("Optical latency search", self.mzi_optical_max_lag)
        detection_form.addRow("Optical window padding", self.mzi_loopback_padding)
        self.mzi_detection_panel.setVisible(False)
        advanced_btn.toggled.connect(self.mzi_detection_panel.setVisible)
        self.mzi_advanced_btn = advanced_btn
        setup_column.addWidget(self.mzi_detection_panel)
        setup_column.addStretch(1)
        self._on_mzi_mode_changed()

        def update_spacing_fields():
            explicit = self.mzi_spacing.currentData() == "explicit"
            self.mzi_voltage_list.setEnabled(explicit)
            for widget in (self.mzi_vstart, self.mzi_vstop, self.mzi_points):
                widget.setEnabled(not explicit)
        self.mzi_spacing.currentIndexChanged.connect(update_spacing_fields)

        self.mzi_plot = pg.PlotWidget()
        self.mzi_plot.setMinimumHeight(260)
        self.mzi_plot.setBackground("#101418")
        self.mzi_plot.showGrid(x=True, y=True, alpha=0.22)
        self.mzi_plot.setLabel(
            "left", "mean detected spike peak", units="mV")
        self.mzi_plot.setLabel("bottom", "heater", units="V")
        self.mzi_plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.mzi_curve_fwd = self.mzi_plot.plot(
            pen=pg.mkPen("#4FC3F7", width=1.5), symbol="o",
            symbolSize=4, symbolBrush="#4FC3F7")
        self.mzi_curve_rev = self.mzi_plot.plot(
            pen=pg.mkPen("#FFB74D", width=1.5), symbol="o",
            symbolSize=4, symbolBrush="#FFB74D")
        result_column.addWidget(self.mzi_plot)
        self.mzi_reference_plot = pg.PlotWidget()
        self.mzi_reference_plot.setMinimumHeight(175)
        self.mzi_reference_plot.setBackground("#101418")
        self.mzi_reference_plot.showGrid(x=True, y=True, alpha=0.22)
        self.mzi_reference_plot.setLabel(
            "left", "ADC3 reference", units="mV")
        self.mzi_reference_plot.setTitle(
            "ADC3 electrical timing reference: no capture yet")
        result_column.addWidget(self.mzi_reference_plot)

        self.mzi_spike_plot = pg.PlotWidget()
        self.mzi_spike_plot.setMinimumHeight(220)
        self.mzi_spike_plot.setBackground("#101418")
        self.mzi_spike_plot.showGrid(x=True, y=True, alpha=0.22)
        self.mzi_spike_plot.setLabel("left", "optical output", units="mV")
        self.mzi_spike_plot.setLabel("bottom", "ADC sample", units="ns")
        self.mzi_spike_plot.setTitle(
            "ADC0 optical response: no capture yet")
        self.mzi_reference_plot.setXLink(self.mzi_spike_plot)
        result_column.addWidget(self.mzi_spike_plot)
        self._mzi_last_point_result = None

        buttons = QtWidgets.QGridLayout()
        self.mzi_quick_btn = QtWidgets.QPushButton("Test one trigger")
        self.mzi_quick_btn.setToolTip(
            "Capture one hardware-triggered repetition (N=1, no averaging) "
            "and display ADC3 above the selected optical ADC.")
        self.mzi_quick_btn.clicked.connect(self._on_mzi_quick_capture)
        self.mzi_point_btn = QtWidgets.QPushButton("Capture averaged point")
        self.mzi_point_btn.setToolTip(
            "Capture the configured N trigger-aligned repetitions, average each "
            "ADC independently, and display ADC3 above the selected optical ADC.")
        self.mzi_point_btn.clicked.connect(self._on_mzi_capture_point)
        self.mzi_run_btn = QtWidgets.QPushButton("Run sweep")
        self.mzi_run_btn.setToolTip(
            "Program the setup, sweep every checked heater together over the configured voltages, "
            "and save every raw ADC capture under the named experiment directory.")
        self.mzi_run_btn.clicked.connect(self._on_mzi_calibrate)
        self.mzi_cancel_btn = QtWidgets.QPushButton("Stop")
        self.mzi_cancel_btn.setEnabled(False)
        self.mzi_cancel_btn.clicked.connect(self._on_mzi_cal_cancel)
        self.mzi_preview_adc = QtWidgets.QComboBox()
        for channel in range(3):
            self.mzi_preview_adc.addItem(f"ADC{channel}", channel)
        self.mzi_preview_adc.setToolTip(
            "Choose which independently averaged optical ADC appears below "
            "the ADC3 timing reference.")
        self.mzi_preview_adc.currentIndexChanged.connect(
            self._refresh_mzi_point_preview)
        buttons.addWidget(self.mzi_quick_btn, 0, 0)
        buttons.addWidget(QtWidgets.QLabel("Preview"), 0, 1)
        buttons.addWidget(self.mzi_preview_adc, 0, 2)
        buttons.addWidget(self.mzi_point_btn, 1, 0, 1, 3)
        buttons.addWidget(self.mzi_run_btn, 2, 0)
        buttons.addWidget(self.mzi_cancel_btn, 3, 0)
        result_column.addLayout(buttons)
        self.mzi_progress = QtWidgets.QProgressBar()
        self.mzi_progress.setRange(0, 1)
        self.mzi_progress.setValue(0)
        result_column.addWidget(self.mzi_progress)
        self.mzi_status = QtWidgets.QLabel(
            "Initialize PICO-002, select one or more heaters, then program the experiment setup.")
        self.mzi_status.setWordWrap(True)
        self.mzi_status.setStyleSheet("color:#9fb3c8; font-size:11px;")
        result_column.addWidget(self.mzi_status)
        result_column.addStretch(1)

        self.liveavg_timer = QtCore.QTimer(self)
        self.liveavg_timer.setInterval(25)
        self.liveavg_timer.timeout.connect(self._on_liveavg_tick)

    def _build_mzi_results_panel(self, layout):
        layout.setContentsMargins(8, 8, 8, 8)
        self._mzi_optical_trace_range = (-25.0, 25.0)
        self._mzi_reference_trace_range = (-1000.0, 1000.0)
        dataset_row = QtWidgets.QHBoxLayout()
        self.mzi_import_btn = QtWidgets.QPushButton("Import experiment")
        self.mzi_import_btn.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.SP_DialogOpenButton))
        self.mzi_import_btn.setToolTip(
            "Open any saved optical experiment directory and re-run analysis "
            "from its raw trigger-aligned captures.")
        self.mzi_import_btn.clicked.connect(self._on_mzi_import_experiment)
        self.mzi_dataset_combo = QtWidgets.QComboBox()
        self.mzi_dataset_combo.setEnabled(False)
        self.mzi_dataset_combo.setToolTip(
            "Switch between optical experiments loaded during this session.")
        self.mzi_dataset_combo.currentIndexChanged.connect(
            self._on_mzi_dataset_selected)
        dataset_row.addWidget(self.mzi_import_btn)
        dataset_row.addWidget(self.mzi_dataset_combo, 1)
        layout.addLayout(dataset_row)
        self.mzi_dataset_status = QtWidgets.QLabel(
            "No optical experiment loaded.")
        self.mzi_dataset_status.setWordWrap(True)
        self.mzi_dataset_status.setStyleSheet(
            "color:#9fb3c8; font-size:11px;")
        layout.addWidget(self.mzi_dataset_status)

        analysis_row = QtWidgets.QHBoxLayout()
        analysis_row.addWidget(QtWidgets.QLabel("ADC lane"))
        self.mzi_channel_tabs = QtWidgets.QTabBar()
        for channel in range(3):
            self.mzi_channel_tabs.addTab(f"ADC{channel}")
        self.mzi_channel_tabs.addTab("ADC3 Reference")
        self.mzi_channel_tabs.setExpanding(False)
        self.mzi_channel_tabs.setToolTip(
            "ADC0-ADC2 are independent optical measurements with one shared "
            "Y scale. ADC3 is the separate DAC3 electrical loopback used as "
            "the cross-correlation reference.")
        self.mzi_channel_tabs.currentChanged.connect(
            self._on_mzi_channel_tab_changed)
        analysis_row.addWidget(self.mzi_channel_tabs)
        analysis_row.addWidget(QtWidgets.QLabel("Peak polarity"))
        self.mzi_peak_polarity = QtWidgets.QComboBox()
        self.mzi_peak_polarity.addItem("Auto dominant", "auto")
        self.mzi_peak_polarity.addItem("Negative", "negative")
        self.mzi_peak_polarity.addItem("Positive", "positive")
        self.mzi_peak_polarity.setToolTip(
            "Auto selects the polarity with the larger median peak magnitude.")
        self.mzi_outlier_filter = QtWidgets.QCheckBox("Reject height outliers")
        self.mzi_outlier_filter.setChecked(False)
        self.mzi_outlier_filter.setToolTip(
            "Iteratively reject peak amplitudes outside the selected sigma limit.")
        self.mzi_outlier_sigma = QtWidgets.QDoubleSpinBox()
        self.mzi_outlier_sigma.setRange(0.5, 10.0)
        self.mzi_outlier_sigma.setDecimals(1)
        self.mzi_outlier_sigma.setSingleStep(0.5)
        self.mzi_outlier_sigma.setValue(2.5)
        self.mzi_outlier_sigma.setSuffix(" sigma")
        self.mzi_outlier_apply = QtWidgets.QPushButton("Apply analysis")
        self.mzi_outlier_apply.setToolTip(
            "Recalculate markers and both optical curves from the loaded data.")
        self.mzi_outlier_apply.clicked.connect(
            self._apply_mzi_peak_analysis)
        analysis_row.addWidget(self.mzi_peak_polarity)
        analysis_row.addWidget(self.mzi_outlier_filter)
        analysis_row.addWidget(self.mzi_outlier_sigma)
        analysis_row.addWidget(self.mzi_outlier_apply)
        analysis_row.addStretch(1)
        layout.addLayout(analysis_row)

        self.mzi_results_tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.mzi_results_tabs, 1)

        traces_tab = QtWidgets.QWidget()
        traces_layout = QtWidgets.QVBoxLayout(traces_tab)
        self.mzi_results_summary = QtWidgets.QLabel(
            "Run a sweep to inspect every post-processed voltage point.")
        self.mzi_results_summary.setWordWrap(True)
        traces_layout.addWidget(self.mzi_results_summary)
        trace_scale_row = QtWidgets.QHBoxLayout()
        self.mzi_trace_scale_label = QtWidgets.QLabel(
            "Optical Y range shared by ADC0-ADC2")
        trace_scale_row.addWidget(self.mzi_trace_scale_label)
        self.mzi_trace_y_min = QtWidgets.QDoubleSpinBox()
        self.mzi_trace_y_min.setRange(-10000.0, 9999.0)
        self.mzi_trace_y_min.setDecimals(3)
        self.mzi_trace_y_min.setValue(-25.0)
        self.mzi_trace_y_min.setSuffix(" mV")
        self.mzi_trace_y_min.setToolTip(
            "Lower Y-axis limit shared by ADC0, ADC1, and ADC2.")
        self.mzi_trace_y_max = QtWidgets.QDoubleSpinBox()
        self.mzi_trace_y_max.setRange(-9999.0, 10000.0)
        self.mzi_trace_y_max.setDecimals(3)
        self.mzi_trace_y_max.setValue(25.0)
        self.mzi_trace_y_max.setSuffix(" mV")
        self.mzi_trace_y_max.setToolTip(
            "Upper Y-axis limit shared by ADC0, ADC1, and ADC2.")
        self.mzi_trace_scale_apply = QtWidgets.QPushButton("Apply")
        self.mzi_trace_scale_apply.setToolTip(
            "Apply this fixed Y range to every averaged sweep trace.")
        self.mzi_trace_scale_apply.clicked.connect(
            self._apply_mzi_trace_scale)
        self.mzi_trace_scale_fit = QtWidgets.QPushButton("Fit data")
        self.mzi_trace_scale_fit.setToolTip(
            "Set one shared Y range that fits the currently loaded traces.")
        self.mzi_trace_scale_fit.clicked.connect(
            self._fit_mzi_trace_scale)
        trace_scale_row.addWidget(self.mzi_trace_y_min)
        trace_scale_row.addWidget(self.mzi_trace_y_max)
        trace_scale_row.addWidget(self.mzi_trace_scale_apply)
        trace_scale_row.addWidget(self.mzi_trace_scale_fit)
        trace_scale_row.addStretch(1)
        traces_layout.addLayout(trace_scale_row)
        self.mzi_trace_scroll = QtWidgets.QScrollArea()
        self.mzi_trace_scroll.setWidgetResizable(True)
        self.mzi_trace_panel = QtWidgets.QWidget()
        self.mzi_trace_grid = QtWidgets.QGridLayout(self.mzi_trace_panel)
        self.mzi_trace_grid.setContentsMargins(6, 6, 6, 6)
        self.mzi_trace_grid.setHorizontalSpacing(8)
        self.mzi_trace_grid.setVerticalSpacing(8)
        self.mzi_trace_scroll.setWidget(self.mzi_trace_panel)
        traces_layout.addWidget(self.mzi_trace_scroll, 1)
        self.mzi_sweep_trace_plots = []
        self.mzi_results_tabs.addTab(traces_tab, "Averaged sweep traces")

        curve_tab = QtWidgets.QWidget()
        curve_layout = QtWidgets.QVBoxLayout(curve_tab)
        self.mzi_result_curve_plot = pg.PlotWidget()
        self.mzi_result_curve_plot.setBackground("#101418")
        self.mzi_result_curve_plot.showGrid(x=True, y=True, alpha=0.22)
        self.mzi_result_curve_plot.setLabel(
            "left", "mean detected spike peak", units="mV")
        self.mzi_result_curve_plot.setLabel("bottom", "heater", units="V")
        self.mzi_result_curve_plot.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.mzi_result_curve_plot.addLegend(offset=(10, 10))
        self.mzi_result_curve_raw_fwd = self.mzi_result_curve_plot.plot(
            pen=pg.mkPen("#90A4AE", width=1.2, style=QtCore.Qt.DashLine),
            symbol="o", symbolSize=4, symbolBrush="#90A4AE",
            name="raw forward")
        self.mzi_result_curve_raw_rev = self.mzi_result_curve_plot.plot(
            pen=pg.mkPen("#B0BEC5", width=1.2, style=QtCore.Qt.DotLine),
            symbol="o", symbolSize=4, symbolBrush="#B0BEC5",
            name="raw reverse")
        self.mzi_result_curve_fwd = self.mzi_result_curve_plot.plot(
            pen=pg.mkPen("#4FC3F7", width=1.8), symbol="o",
            symbolSize=5, symbolBrush="#4FC3F7", name="filtered forward")
        self.mzi_result_curve_rev = self.mzi_result_curve_plot.plot(
            pen=pg.mkPen("#FFB74D", width=1.8), symbol="o",
            symbolSize=5, symbolBrush="#FFB74D", name="filtered reverse")
        curve_layout.addWidget(self.mzi_result_curve_plot, 1)
        self.mzi_results_tabs.addTab(curve_tab, "Optical curve")

    def _selected_mzi_nets(self):
        return ordered_heater_nets(self._mzi_selected_heaters)

    def _on_mzi_group_toggle(self, nets):
        group = set(ordered_heater_nets(nets))
        select_group = not group.issubset(self._mzi_selected_heaters)
        if select_group:
            self._mzi_selected_heaters.update(group)
        else:
            self._mzi_selected_heaters.difference_update(group)
        for net in group:
            button = self._mzi_heater_buttons[net]
            button.blockSignals(True)
            button.setChecked(net in self._mzi_selected_heaters)
            button.blockSignals(False)
        self._refresh_mzi_heater_map()

    def _on_mzi_heater_toggled(self, net, checked):
        if checked:
            self._mzi_selected_heaters.add(net)
        else:
            self._mzi_selected_heaters.discard(net)
        self._refresh_mzi_heater_map()

    def _refresh_mzi_heater_map(self):
        staged = self._mzi_staged_heater_voltages
        selected = self._selected_mzi_nets()
        if hasattr(self, "mzi_selected_count"):
            self.mzi_selected_count.setText(f"{len(selected)} selected")
        for net, button in self._mzi_heater_buttons.items():
            commanded = self._mzi_heater_voltages[net]
            voltage = None if commanded is None else float(commanded)
            pending = (staged is not None and (voltage is None or
                       abs(float(staged[net]) - voltage) > 0.5e-6))
            hardware = HEATER_HARDWARE[net]
            marker = "*" if pending else ""
            voltage_text = "--" if voltage is None else f"{voltage:.3f}"
            button.setText(f"{hardware['row']},{hardware['column']}\n"
                           f"{voltage_text}{marker} V")
            tooltip = (
                f"{net}: {hardware['board']} channel {hardware['channel']}\n" +
                ("Pico acknowledgment: none" if voltage is None else
                 f"Pico acknowledged: {voltage:.4f} V"))
            if pending:
                tooltip += f"\nStaged: {float(staged[net]):.4f} V"
            button.setToolTip(tooltip)
            background = "#474D53" if voltage is None else "#245C3D"
            if button.isChecked():
                border = "3px solid #EF5350"
            elif pending:
                border = "2px solid #FFB74D"
            else:
                border = "1px solid #53616F"
            button.setStyleSheet(
                "QToolButton {"
                f"background: {background}; border: {border};"
                "color: #E8EDF2; padding: 2px; border-radius: 3px;"
                "font-size: 10px; }"
                "QToolButton:hover { border-color: #A9C7D8; }")

    def _on_mzi_heater_programmed(self, net, voltage):
        if net not in self._mzi_heater_voltages:
            return
        self._mzi_heater_voltages[net] = float(voltage)
        self.mzi_write_status.setText(
            f"ACK from PICO-002: {net} set to {float(voltage):.4f} V")
        self.mzi_write_status.setStyleSheet("color:#81C784; font-size:11px;")
        self._refresh_mzi_heater_map()

    def _set_mzi_heater_voltages(self, requested):
        requested = validate_requested_heater_voltages(dict(requested))
        self._mzi_controller.set_voltages(
            requested, on_sent=self.mzi_heater_programmed.emit)

    def _on_mzi_set_heaters(self, mode):
        if self._mzi_running:
            return
        if mode == "selected":
            nets = self._selected_mzi_nets()
            if not nets:
                QtWidgets.QMessageBox.warning(
                    self, "No heaters selected", "Select at least one heater.")
                return
            voltage = float(self.mzi_selected_voltage.value())
            requested = {net: voltage for net in nets}
        elif mode == "zero_selected":
            nets = self._selected_mzi_nets()
            if not nets:
                QtWidgets.QMessageBox.warning(
                    self, "No heaters selected", "Select at least one heater.")
                return
            requested = {net: 0.0 for net in nets}
        elif mode == "zero_all":
            requested = {net: 0.0 for net in MZI_NET_NAMES}
        elif mode == "staged":
            if self._mzi_staged_heater_voltages is None:
                QtWidgets.QMessageBox.warning(
                    self, "No configuration loaded",
                    "Load a named heater configuration first.")
                return
            requested = dict(self._mzi_staged_heater_voltages)
        else:
            raise ValueError(f"unknown heater operation {mode!r}")
        settle_s = self.mzi_settle.value() / 1000.0
        self.mzi_write_status.setText(
            f"Connecting to PICO-002; writing {len(requested)} heater output(s)...")
        self.mzi_write_status.setStyleSheet("color:#FFB74D; font-size:11px;")
        self._mzi_begin(0, f"Programming {len(requested)} heater outputs...")
        self._bg(lambda: self.mzi_cal_result.emit(
            self._run_mzi_set_heaters(requested, settle_s=settle_s)))

    def _on_mzi_init_pico(self):
        if self._mzi_running:
            return
        self.mzi_pico_status.setText("Pico: initializing PyDAQ definitions...")
        self.mzi_pico_status.setStyleSheet("color:#FFB74D; font-size:11px;")
        self._mzi_begin(0, "Initializing PICO-002 through the FPGA bridge...")
        self._bg(lambda: self.mzi_cal_result.emit(self._run_mzi_init_pico()))

    def _run_mzi_init_pico(self):
        try:
            self._mzi_controller.connect(
                board_ip=self.args.board_ip, local_ip=self.args.local_ip)
            available = self._mzi_controller.available_nets()
            return {"kind": "pico_init", "heater_count": len(available)}
        except Exception as exc:  # noqa: BLE001
            return {"kind": "pico_init", "_err": f"{type(exc).__name__}: {exc}"}

    def _on_mzi_test_pico(self):
        if self._mzi_running:
            return
        self.mzi_pico_status.setText("Pico: testing 5 handshakes...")
        self.mzi_pico_status.setStyleSheet("color:#FFB74D; font-size:11px;")
        self._mzi_begin(0, "Testing PICO-002 connection...")
        self._bg(lambda: self.mzi_cal_result.emit(self._run_mzi_test_pico()))

    def _run_mzi_test_pico(self):
        try:
            self._mzi_controller.connect(
                board_ip=self.args.board_ip, local_ip=self.args.local_ip)
            result = self._mzi_controller.test_connection(probes=5)
            return {"kind": "pico_test", **result}
        except Exception as exc:  # noqa: BLE001
            return {"kind": "pico_test", "_err": f"{type(exc).__name__}: {exc}"}

    def _run_mzi_set_heaters(self, requested, *, settle_s=0.0):
        try:
            self._mzi_controller.connect(
                board_ip=self.args.board_ip, local_ip=self.args.local_ip)
            available = set(self._mzi_controller.available_nets())
            missing = sorted(set(requested) - available)
            if missing:
                raise RuntimeError(
                    f"heaters are not wired in PICO-002 config: {', '.join(missing)}")
            self._set_mzi_heater_voltages(requested)
            settle_s = max(0.0, float(settle_s))
            if settle_s:
                time.sleep(settle_s)
            return {"kind": "heater_set", "voltages": dict(requested),
                    "settle_s": settle_s}
        except Exception as exc:  # noqa: BLE001
            return {"kind": "heater_set", "_err": f"{type(exc).__name__}: {exc}"}

    def _refresh_mzi_config_combo(self, selected=None):
        self.mzi_config_combo.blockSignals(True)
        self.mzi_config_combo.clear()
        self.mzi_config_combo.addItems(sorted(self._mzi_heater_configs))
        if selected in self._mzi_heater_configs:
            self.mzi_config_combo.setCurrentText(selected)
        self.mzi_config_combo.blockSignals(False)

    def _on_mzi_config_save(self):
        name = self.mzi_config_name.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(
                self, "Configuration name required",
                "Enter a name for the heater configuration.")
            return
        unknown = [net for net, voltage in self._mzi_heater_voltages.items()
                   if voltage is None]
        if unknown:
            QtWidgets.QMessageBox.warning(
                self, "Heater state incomplete",
                "Program or load a complete heater configuration before saving it.")
            return
        self._mzi_heater_configs[name] = {
            "heater_voltages_v": dict(self._mzi_heater_voltages),
            "selected_heaters": list(self._selected_mzi_nets()),
        }
        try:
            save_heater_configs(self._mzi_heater_configs)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self, "Configuration save failed", str(exc))
            return
        self._refresh_mzi_config_combo(name)
        self.mzi_config_status.setText(f"Saved {name}")

    def _on_mzi_config_load(self):
        name = self.mzi_config_combo.currentText()
        if name not in self._mzi_heater_configs:
            return
        config = self._mzi_heater_configs[name]
        self._mzi_staged_heater_voltages = validate_heater_voltages(
            config["heater_voltages_v"])
        selected = set(ordered_heater_nets(config["selected_heaters"]))
        self._mzi_selected_heaters = selected or {MZI_NET_NAMES[0]}
        for net, button in self._mzi_heater_buttons.items():
            button.blockSignals(True)
            button.setChecked(net in self._mzi_selected_heaters)
            button.blockSignals(False)
        self._mzi_active_config_name = name
        self.mzi_config_name.setText(name)
        self.mzi_config_status.setText(f"Loaded {name}; not applied")
        self._refresh_mzi_heater_map()

    def _on_mzi_config_delete(self):
        name = self.mzi_config_combo.currentText()
        if name not in self._mzi_heater_configs:
            return
        del self._mzi_heater_configs[name]
        save_heater_configs(self._mzi_heater_configs)
        if self._mzi_active_config_name == name:
            self._mzi_active_config_name = None
            self._mzi_staged_heater_voltages = None
        self._refresh_mzi_config_combo()
        self.mzi_config_status.setText(f"Deleted {name}")
        self._refresh_mzi_heater_map()

    def _on_mzi_mapping_export(self):
        default = os.path.join(
            os.path.abspath(os.path.expanduser(self.args.capture_dir)),
            "pydaq_heater_mapping.json")
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export PyDAQ heater mapping", default, "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(heater_mapping_payload(self._mzi_heater_voltages),
                          handle, indent=2, sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Mapping export failed", str(exc))
            return
        self.mzi_config_status.setText(f"Exported {path}")

    def _ensure_mzi_tone_capture_length(self, *_):
        if not hasattr(self, "mzi_capture_size"):
            return
        try:
            _increment, actual_frequency = dds_phase_increment(
                self.mzi_tone_frequency.value() * 1000.0)
        except ValueError:
            return
        required_samples = int(np.ceil(
            16.0 * 1.0e9 / actual_frequency)) + 64
        required_bytes = required_samples * 4
        current_bytes = int(self.mzi_capture_size.currentData())
        if current_bytes >= required_bytes:
            return
        for index in range(self.mzi_capture_size.count()):
            if int(self.mzi_capture_size.itemData(index)) >= required_bytes:
                self.mzi_capture_size.setCurrentIndex(index)
                return
        self.mzi_capture_size.setCurrentIndex(
            self.mzi_capture_size.count() - 1)

    def _on_mzi_mode_changed(self, *_):
        tone_mode = (
            hasattr(self, "mzi_mode") and self.mzi_mode.currentData() == "tone")
        if hasattr(self, "mzi_tone_panel"):
            self.mzi_tone_panel.setVisible(tone_mode)
        if tone_mode:
            self._ensure_mzi_tone_capture_length()
        for widget in getattr(self, "mzi_dac_sources", []):
            widget.setEnabled(not tone_mode)
        for widget in getattr(self, "mzi_dac_invert", []):
            widget.setEnabled(not tone_mode)
        for name in ("mzi_neuron_editor_btn", "mzi_pulse_editor_btn"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(not tone_mode)
        if hasattr(self, "mzi_advanced_btn"):
            if tone_mode:
                self.mzi_advanced_btn.setChecked(False)
                self.mzi_detection_panel.setVisible(False)
            self.mzi_advanced_btn.setEnabled(not tone_mode)
        if hasattr(self, "mzi_program_btn"):
            self.mzi_program_btn.setToolTip(
                "Program the shared DDS frequency and fixed tone routes. "
                "DAC3 is always the electrical reference."
                if tone_mode else
                "Program the neuron, current source, pulse shapers, and "
                "selected spike routes for Mode A.")

    def _mzi_gui_spec(self):
        experiment_name = self.mzi_experiment_name.text().strip()
        if not experiment_name:
            raise ValueError("enter an experiment name")
        sweep_nets = self._selected_mzi_nets()
        if not sweep_nets:
            raise ValueError("select at least one heater to sweep")
        sweep_net = sweep_nets[0]
        capture_bytes = int(self.mzi_capture_size.currentData())
        if self.mzi_mode.currentData() == "tone":
            enabled_inputs = [
                channel for channel, enabled in enumerate(self.mzi_tone_inputs)
                if enabled.isChecked()]
            if not enabled_inputs:
                raise ValueError("enable at least one pure-tone photonic input")
            requested_frequency = self.mzi_tone_frequency.value() * 1000.0
            phase_increment, actual_frequency = dds_phase_increment(
                requested_frequency)
            captured_cycles = (
                (capture_bytes // 4 - 64) * actual_frequency / 1.0e9)
            if captured_cycles < 1.5:
                raise ValueError(
                    "pure-tone capture must contain at least 1.5 cycles; "
                    "select a longer capture length")
            return {
                "mode": "tone",
                "capture_command": "BCPD",
                "experiment_name": experiment_name,
                "capture_bytes": capture_bytes,
                "net": sweep_net,
                "nets": sweep_nets,
                "sweep_label": (sweep_net if len(sweep_nets) == 1 else
                                f"{len(sweep_nets)} selected heaters"),
                "tone_frequency_hz": requested_frequency,
                "tone_actual_frequency_hz": actual_frequency,
                "tone_phase_increment": phase_increment,
                "tone_inputs": enabled_inputs,
                "response_start_sample": 64,
                "reps": self.mzi_reps.value(),
                "settle_s": self.mzi_settle.value() / 1000.0,
                "restore_v": self.mzi_restore.value(),
                "heater_voltages_before_sweep":
                    dict(self._mzi_heater_voltages),
                "xbar_sources": [
                    *("DDS" if channel in enabled_inputs else "Off"
                      for channel in range(3)),
                    "DDS",
                ],
                "dac_invert": [False] * 4,
                "adc_channels": [0, 1, 2, 3],
                "optical_adc_channels": [0, 1, 2],
                "analysis_adc": 0,
                "reference_adc": 3,
            }
        pulse_counts = self._optical_pulse_counts()
        if not pulse_counts:
            raise ValueError(
                "the optical spike pulse must contain at least one point")
        current_samples, current_cps, actual_frequency = gen_current_wave(
            "Square", 15.0, 5000.0, square_duty=50.0)
        return {
            "mode": "spike",
            "capture_command": "BCPT",
            "experiment_name": experiment_name,
            "capture_bytes": capture_bytes,
            "net": sweep_net,
            "nets": sweep_nets,
            "sweep_label": (sweep_net if len(sweep_nets) == 1 else
                            f"{len(sweep_nets)} selected heaters"),
            "profiles": {
                neuron: self.mzi_profiles[neuron].currentText()
                for neuron in range(4)
            },
            "current_mode": "Square",
            "current_ma": 15.0,
            "current_frequency_hz": 5000.0,
            "current_actual_frequency_hz": actual_frequency,
            "current_duty_percent": 50.0,
            "current_sample_count": int(current_samples.size),
            "cps": int(current_cps),
            "response_start_sample": 64,
            "pulse_counts": pulse_counts,
            "pulse_len": len(pulse_counts),
            "reps": self.mzi_reps.value(),
            "detect_sigma": self.mzi_detect_sigma.value(),
            "boundary_sigma": self.mzi_boundary_sigma.value(),
            "minimum_seed_samples": self.mzi_min_seed.value(),
            "settle_s": self.mzi_settle.value() / 1000.0,
            "restore_v": self.mzi_restore.value(),
            "heater_voltages_before_sweep": dict(self._mzi_heater_voltages),
            "xbar_sources": [
                *(source.currentText() for source in self.mzi_dac_sources),
                "Spike 3",
            ],
            "dac_invert": [
                invert.isChecked() for invert in self.mzi_dac_invert
            ],
            "adc_channels": [0, 1, 2, 3],
            "optical_adc_channels": [0, 1, 2],
            "analysis_adc": 0,
            "reference_adc": 3,
            "optical_max_lag": self.mzi_optical_max_lag.value(),
            "loopback_window_padding": self.mzi_loopback_padding.value(),
        }

    def _mzi_experiment_metadata(self, spec):
        if spec.get("mode") == "tone":
            return {
                "hardware": {
                    "board_ip": self.args.board_ip,
                    "local_ip": self.args.local_ip,
                    "uart_port": self.args.port,
                    "sample_rate_hz": 1.0e9,
                },
                "acquisition": {
                    "transport":
                        "BCPD DDS-phase-aligned capture + BRDO UDP Ethernet",
                    "capture_bytes_per_chip_per_repetition":
                        spec["capture_bytes"],
                    "samples_per_channel_per_repetition":
                        spec["capture_bytes"] // 4,
                    "repetitions_per_heater_capture": spec["reps"],
                    "adc_channels": list(spec["adc_channels"]),
                    "optical_adc_channels":
                        list(spec["optical_adc_channels"]),
                    "reference_adc_channel": spec["reference_adc"],
                    "reference_dac_channel": 3,
                    "channels_averaged_independently": True,
                },
                "xbar": {
                    "sources_by_dac": {
                        f"DAC{channel}": source for channel, source in
                        enumerate(spec["xbar_sources"])},
                    "register17_readback": spec.get("xbar_register17"),
                    "fixed_during_sweep": True,
                },
                "stimulus": {
                    "mode": "shared_dds_pure_tone",
                    "requested_frequency_hz": spec["tone_frequency_hz"],
                    "actual_frequency_hz": spec["tone_actual_frequency_hz"],
                    "phase_increment": spec["tone_phase_increment"],
                    "offset_v": 0.0,
                    "range_v": [-DAC_VMAX, DAC_VMAX],
                    "enabled_photonic_dacs": list(spec["tone_inputs"]),
                    "electrical_reference": "DAC3 to ADC3",
                    "phase_restarted_for_every_repetition": True,
                    "captured_tone_cycles": (
                        (spec["capture_bytes"] // 4 - 64) *
                        spec["tone_actual_frequency_hz"] / 1.0e9),
                },
                "heater_sweep": {
                    "heater_nets": list(spec["nets"]),
                    "primary_heater_net": spec["net"],
                    "shared_sweep_voltage": len(spec["nets"]) > 1,
                    "spacing": spec["spacing"],
                    "heater_voltages_before_sweep":
                        spec["heater_voltages_before_sweep"],
                    "planned_voltages_v": spec["voltages"],
                    "planned_directions": spec["directions"],
                    "settle_seconds": spec["settle_s"],
                    "restore_voltage_v": spec["restore_v"],
                },
                "analysis": {
                    "method": "coherent average then least-squares sine fit",
                    "reported_per_adc": [
                        "amplitude_v", "gain_vs_reference",
                        "phase_vs_reference_rad",
                        "latency_modulo_period_ns"],
                    "latency_note":
                        "single-tone latency is modulo one tone period",
                },
                "software": {
                    "capture_application": "scripts/dac_scope_qt.py",
                    "processor": "scripts/tone_calibration.py",
                },
            }
        neurons = []
        for neuron in range(4):
            profile_name = spec["profiles"][neuron]
            profile = dict(NEURON_PROFILE_VALUES[profile_name])
            profile["i"] = 0.0
            profile["iconst"] = 0.0
            neurons.append({
                "index": neuron,
                "profile": profile_name,
                "parameters": profile,
                "dt_q16": "0x00008000",
                "period_cycles": 1,
                "static_i_ma": 0.0,
                "static_iconst_ma": 0.0,
            })
        return {
            "hardware": {
                "board_ip": self.args.board_ip,
                "local_ip": self.args.local_ip,
                "uart_port": self.args.port,
                "sample_rate_hz": 1.0e9,
            },
            "acquisition": {
                "transport": "BCPT trigger-aligned capture + BRDO UDP Ethernet",
                "capture_bytes_per_chip_per_repetition": spec["capture_bytes"],
                "samples_per_channel_per_repetition": spec["capture_bytes"] // 4,
                "repetitions_per_heater_capture": spec["reps"],
                "adc_channels": list(spec["adc_channels"]),
                "adc_channel": spec["analysis_adc"],
                "optical_adc_channels": list(spec["optical_adc_channels"]),
                "reference_adc_channel": spec["reference_adc"],
                "reference_dac_channel": 3,
                "all_adc_channels_saved": True,
                "software_time_alignment": False,
                "software_time_alignment_source": "none; BCPT hardware trigger",
                "loopback_max_repetition_lag_samples": 0,
                "loopback_max_optical_lag_samples": spec["optical_max_lag"],
                "loopback_window_padding_samples":
                    spec["loopback_window_padding"],
            },
            "xbar": {
                "sources_by_dac": {
                    f"DAC{channel}": source
                    for channel, source in enumerate(spec["xbar_sources"])
                },
                "register17_readback": spec.get("xbar_register17"),
                "invert_by_dac": {
                    f"DAC{channel}": bool(inverted)
                    for channel, inverted in enumerate(spec["dac_invert"])
                },
                "inversion_implementation": "existing per-neuron SCAL signed gain",
                "optical_test_route": (
                    "DAC0..DAC2 use normal independent crossbar selections; DAC3 always "
                    "Spike 3 looped to ADC3; ADC0..ADC2 analyzed separately"),
            },
            "stimulus": {
                "neurons": neurons,
                "current_source": {
                    "mode": "looping_square",
                    "amplitude_ma": spec["current_ma"],
                    "requested_frequency_hz": spec["current_frequency_hz"],
                    "actual_frequency_hz": spec["current_actual_frequency_hz"],
                    "duty_percent": spec["current_duty_percent"],
                    "sample_count": spec["current_sample_count"],
                    "cycles_per_sample": spec["cps"],
                },
                "spike_pulse": {
                    "shape": "pulse_editor_waveform",
                    "length_dac_points": spec["pulse_len"],
                    "samples_counts": list(spec["pulse_counts"]),
                    "minimum_count": int(min(spec["pulse_counts"])),
                    "maximum_count": int(max(spec["pulse_counts"])),
                    "programmed_independently_to_neurons": [0, 1, 2, 3],
                },
            },
            "heater_sweep": {
                "heater_nets": list(spec["nets"]),
                "primary_heater_net": spec["net"],
                "shared_sweep_voltage": len(spec["nets"]) > 1,
                "spacing": spec["spacing"],
                "heater_voltages_before_sweep": spec["heater_voltages_before_sweep"],
                "planned_voltages_v": spec["voltages"],
                "planned_directions": spec["directions"],
                "settle_seconds": spec["settle_s"],
                "restore_voltage_v": spec["restore_v"],
            },
            "detection": {
                "input": "arithmetic mean of all trigger-aligned repetitions",
                "threshold_sigma": spec["detect_sigma"],
                "boundary_sigma": spec["boundary_sigma"],
                "minimum_seed_samples": spec["minimum_seed_samples"],
                "response_start_sample": spec["response_start_sample"],
                "response_start_reason": "64-sample capture guard; no stimulus onset assumed",
                "template": "simultaneous DAC3-to-ADC3 Spike 0 loopback",
                "reference_adc_channel": spec["reference_adc"],
                "optical_adc_channel": spec["analysis_adc"],
                "optical_adc_channels": list(spec["optical_adc_channels"]),
                "channels_averaged_independently": True,
                "max_repetition_lag_samples": 0,
                "max_optical_lag_samples": spec["optical_max_lag"],
                "window_padding_samples": spec["loopback_window_padding"],
                "fft_filter": None,
            },
            "software": {
                "capture_application": "scripts/dac_scope_qt.py",
                "processor": "scripts/process_optical_experiment.py",
            },
        }
    def _mzi_begin(self, total, status):
        self._mzi_resume_autosample = self.autosample_timer.isActive()
        if self._mzi_resume_autosample:
            self.autosample_timer.stop()
        self._mzi_resume_tap = self.tap is not None
        if self.tap:
            self.tap.close()
            self.tap = None
        self._mzi_cancel.clear()
        self._mzi_running = True
        self.mzi_program_btn.setEnabled(False)
        self.mzi_quick_btn.setEnabled(False)
        self.mzi_point_btn.setEnabled(False)
        self.mzi_run_btn.setEnabled(False)
        self.mzi_cancel_btn.setEnabled(total > 0)
        self.mzi_progress.setRange(0, max(1, total))
        self.mzi_progress.setValue(0)
        self.mzi_status.setText(status)
        for control in self._mzi_heater_controls:
            control.setEnabled(False)

    def _program_mzi_test(self, spec):
        if spec.get("mode") == "tone":
            self.mzi_setup_progress.emit(
                "Programming shared DDS and fixed pure-tone routes...")
            reply, increment, actual = self.dac.set_dds_frequency(
                spec["tone_frequency_hz"])
            replies = [reply]
            spec["tone_phase_increment"] = int(increment)
            spec["tone_actual_frequency_hz"] = float(actual)
            for dac_channel, source in enumerate(spec["xbar_sources"]):
                replies.append(self.dac.set_source(dac_channel, source))
            for response in replies:
                if not response or str(response).startswith("ERR"):
                    raise RuntimeError(
                        f"pure-tone setup failed: "
                        f"{response or 'no UART reply'}")
            spec["dds_reply"] = reply
            self.mzi_setup_progress.emit(
                f"DDS verified at {actual / 1000.0:.3f} kHz; "
                "DAC3 is the fixed ADC3 electrical reference.")
            return
        self.mzi_setup_progress.emit("Programming shared neuron timing...")
        replies = list(self.dac.set_neuron_timing(0x8000, period=1))
        pulse = list(spec["pulse_counts"])
        for neuron in range(4):
            self.mzi_setup_progress.emit(
                f"Programming neuron {neuron}: {spec['profiles'][neuron]}...")
            profile = NEURON_PROFILE_VALUES[spec["profiles"][neuron]]
            for param in ("a", "b", "c", "d"):
                replies.append(self.dac.set_neuron_param(
                    neuron, param, izh_to_q16(profile[param])))
            replies.append(self.dac.set_neuron_param(neuron, "i", 0))
            replies.append(self.dac.set_neuron_param(neuron, "iconst", 0))
            replies.append(self.dac.program_pulse(pulse, target=neuron))

        inversion_by_signal = {}
        for dac_channel, source in enumerate(spec["xbar_sources"]):
            requested = bool(spec["dac_invert"][dac_channel])
            if not source.startswith("Spike "):
                if requested:
                    raise RuntimeError(
                        f"DAC{dac_channel} inversion uses the existing spike "
                        "SCAL control; select a Spike 0..3 source first")
                continue
            signal = int(source.rsplit(" ", 1)[1])
            previous = inversion_by_signal.get(signal)
            if previous is not None and previous != requested:
                raise RuntimeError(
                    f"DAC outputs sharing {source} cannot request opposite "
                    "SCAL polarity; select distinct Spike sources")
            inversion_by_signal[signal] = requested

        for signal in range(4):
            gain = 0xC000 if inversion_by_signal.get(signal, False) else 0x4000
            replies.append(self.dac.set_spike_cal(signal, gain, 0))
        for dac_channel, source in enumerate(spec["xbar_sources"]):
            replies.append(self.dac.set_source(dac_channel, source))
        self.mzi_setup_progress.emit(
            "DAC routes and per-signal SCAL polarity programmed; "
            "DAC3 is the fixed Spike 3 reference. "
            "Programming 15 mA, 5 kHz square...")
        current_samples, current_cps, actual_frequency = gen_current_wave(
            "Square", spec["current_ma"], spec["current_frequency_hz"],
            square_duty=spec["current_duty_percent"])
        if int(current_cps) != int(spec["cps"]):
            raise RuntimeError("current-source timing changed after validation")
        spec["current_actual_frequency_hz"] = float(actual_frequency)
        replies.append(self.dac.program_current(
            current_samples, current_cps, hold_last=False))
        self.mzi_setup_progress.emit("Checking FPGA acknowledgements...")
        for reply in replies:
            if not reply or str(reply).startswith("ERR"):
                raise RuntimeError(
                    f"experiment setup failed: {reply or 'no UART reply'}")
        player = self.dac.get_current_player_status()
        expected_count = len(current_samples)
        if (not player["running"] or player["cps"] != int(current_cps) or
                player["count"] != expected_count or player["hold_last"]):
            raise RuntimeError(
                "current-player verification failed: expected "
                f"RUNNING loop cps={int(current_cps)} count={expected_count}, "
                f"read RW16=0x{player['raw']:08X} "
                f"running={int(player['running'])} cps={player['cps']} "
                f"count={player['count']} hold={int(player['hold_last'])}")
        spec["current_player_readback"] = player
        self.mzi_setup_progress.emit(
            f"Current player verified RUNNING (RW16=0x{player['raw']:08X}).")

    def _on_mzi_program_test(self):
        if not self.dac or self._mzi_running:
            return
        try:
            spec = self._mzi_gui_spec()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid optical test", str(exc))
            return
        self._mzi_begin(0, "Programming neuron spike test...")
        self._bg(lambda: self.mzi_cal_result.emit(
            self._run_mzi_program_test(spec)))

    def _run_mzi_program_test(self, spec):
        try:
            self._program_mzi_test(spec)
            return {"kind": "configured", "spec": spec}
        except Exception as exc:  # noqa: BLE001
            return {"_err": f"{type(exc).__name__}: {exc}"}

    def _on_mzi_quick_capture(self):
        if not self.dac or self._mzi_running:
            return
        try:
            spec = self._mzi_gui_spec()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid optical test", str(exc))
            return
        spec["reps"] = 1
        voltage = self.mzi_selected_voltage.value()
        self._mzi_begin(
            1, f"Testing one trigger at {voltage:.4f} V...")
        self._bg(lambda: self.mzi_cal_result.emit(
            self._run_mzi_point(spec, voltage)))

    def _on_mzi_capture_point(self):
        if not self.dac or self._mzi_running:
            return
        try:
            spec = self._mzi_gui_spec()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid optical test", str(exc))
            return
        voltage = self.mzi_selected_voltage.value()
        self._mzi_begin(
            1, f"Capturing {spec['sweep_label']} at {voltage:.4f} V...")
        self._bg(lambda: self.mzi_cal_result.emit(
            self._run_mzi_point(spec, voltage)))

    def _finish_mzi_tone_point(
            self, spec, voltage, requested, raw_stacks, capture):
        stacks_v = {
            channel: np.asarray(stack, dtype=np.float64) * VOLTS_PER_COUNT
            for channel, stack in raw_stacks.items()
        }
        analysis = analyze_tone_capture(
            stacks_v, spec["tone_actual_frequency_hz"],
            reference_adc=spec["reference_adc"],
            start_sample=spec["response_start_sample"])
        capture_dir = os.path.abspath(os.path.expanduser(self.args.capture_dir))
        os.makedirs(capture_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        point_name = (spec["net"] if len(spec["nets"]) == 1 else
                      f"{len(spec['nets'])}_heaters")
        path = os.path.join(
            capture_dir, f"tone_point_{point_name}_{stamp}.npz")
        payload = {
            "heater_voltage_v": float(voltage),
            "heater_nets": np.asarray(spec["nets"]),
            "tone_frequency_hz": spec["tone_actual_frequency_hz"],
            "reference_adc_channel": spec["reference_adc"],
        }
        for channel, stack in raw_stacks.items():
            channel_result = analysis["channels"][channel]
            payload[f"raw_ch{channel}"] = stack
            payload[f"average_ch{channel}_v"] = channel_result["average_v"]
            payload[f"fitted_ch{channel}_v"] = channel_result["fitted_v"]
            payload[f"amplitude_ch{channel}_v"] = (
                channel_result["amplitude_v"])
            payload[f"gain_ch{channel}_vs_adc3"] = (
                channel_result["gain_vs_reference"])
            payload[f"latency_ch{channel}_ns"] = (
                channel_result["latency_modulo_period_ns"])
        np.savez_compressed(path, **payload)
        return {
            "kind": "point", "mode": "tone", "spec": spec,
            "voltage": float(voltage), "heater_voltages": requested,
            "path": path, "capture": capture, "tone_analysis": analysis,
            "height": analysis["channels"][spec["analysis_adc"]]["amplitude_v"],
            "channel_results": {
                channel: analysis["channels"][channel]
                for channel in spec["optical_adc_channels"]
            },
            "reference_average":
                analysis["channels"][spec["reference_adc"]]["average_v"],
        }

    def _run_mzi_point(self, spec, voltage):
        try:
            self._program_mzi_test(spec)
            self._mzi_controller.connect(
                board_ip=self.args.board_ip, local_ip=self.args.local_ip)
            available = set(self._mzi_controller.available_nets())
            missing = sorted(set(spec["nets"]) - available)
            if missing:
                return {"_err": f"heaters not wired in PICO-002 config: {missing}"}
            requested = {net: float(voltage) for net in spec["nets"]}
            self._set_mzi_heater_voltages(requested)
            if spec["settle_s"]:
                time.sleep(spec["settle_s"])
            capture = self._mzi_multisample_with_retries(
                spec, voltage=float(voltage))
            if "_err" in capture:
                return {"_err": capture["_err"]}
            raw_stacks = {
                channel: np.asarray(capture["stack"][channel], dtype=np.int16)
                for channel in spec["adc_channels"]
            }
            if spec.get("mode") == "tone":
                return self._finish_mzi_tone_point(
                    spec, voltage, requested, raw_stacks, capture)
            step_sample = spec["response_start_sample"]
            volts_by_adc = {
                channel: stack.astype(np.float64) * VOLTS_PER_COUNT
                for channel, stack in raw_stacks.items()
            }
            # BCPT repetitions are already hardware-trigger aligned. Average
            # each ADC at its original sample indices; ADC3 is used only to
            # identify the event schedule and optical path latency.
            reference_reps = np.asarray(
                volts_by_adc[spec["reference_adc"]], dtype=np.float64)
            reference = measure_reference_spikes(
                reference_reps, step_sample,
                threshold_sigma=spec["detect_sigma"],
                minimum_peak_distance_samples=max(1, spec["pulse_len"]))
            baseline_end = max(4, step_sample - 10)
            reference_baseline = np.median(
                reference_reps[:, :baseline_end], axis=1)
            reference_average = (
                reference_reps - reference_baseline[:, None]).mean(axis=0)
            reference_polarity = dominant_spike_polarity(reference)
            selected_reference_peaks = reference.peak_indices[
                reference.polarities == reference_polarity]
            channel_work = {}
            valid_lags = []
            for channel in spec["optical_adc_channels"]:
                repetitions = np.asarray(
                    volts_by_adc[channel], dtype=np.float64)
                baseline = np.median(
                    repetitions[:, :baseline_end], axis=1)
                channel_average = (
                    repetitions - baseline[:, None]).mean(axis=0)
                configured_relative_polarity = (
                    -1 if bool(spec["dac_invert"][channel]) !=
                    bool(spec["dac_invert"][spec["reference_adc"]]) else 1)
                try:
                    candidate, relative_polarity = (
                        estimate_main_lobe_lag_auto_polarity(
                            channel_average, reference_average,
                            spec["optical_max_lag"],
                            template_polarity=reference_polarity,
                            template_peak_indices=selected_reference_peaks))
                    candidate_lag = int(candidate.lag_samples)
                    candidate_score = float(candidate.score)
                except ValueError:
                    relative_polarity = configured_relative_polarity
                    candidate_lag = 0
                    candidate_score = 0.0
                stimulus_enabled = spec["xbar_sources"][channel] != "Off"
                correlation_valid = (
                    stimulus_enabled and candidate_score >= 0.10)
                if correlation_valid:
                    valid_lags.append(candidate_lag)
                else:
                    relative_polarity = configured_relative_polarity
                channel_work[channel] = {
                    "repetitions": repetitions,
                    "average": channel_average,
                    "candidate_lag": candidate_lag,
                    "candidate_score": candidate_score,
                    "stimulus_enabled": stimulus_enabled,
                    "relative_polarity": relative_polarity,
                    "correlation_valid": correlation_valid,
                }
            fallback_lag = (
                int(round(float(np.median(valid_lags)))) if valid_lags else 0)
            point_channels = {}
            for channel in spec["optical_adc_channels"]:
                work = channel_work[channel]
                latency = (work["candidate_lag"]
                           if work["correlation_valid"] else fallback_lag)
                timing_valid = bool(
                    work["stimulus_enabled"] and
                    (work["correlation_valid"] or valid_lags))
                timing_source = (
                    "lane correlation" if work["correlation_valid"] else
                    "shared optical-lane latency" if timing_valid else
                    "unavailable")
                nominal, channel_starts, channel_ends, signs = (
                    optical_schedule_from_loopback(
                        reference, work["average"], latency,
                        padding_samples=max(
                            spec["loopback_window_padding"],
                            max(1, spec["pulse_len"] // 2)),
                        response_polarity=work["relative_polarity"]))
                channel_measurement = measure_spikes_in_windows(
                    work["repetitions"], step_sample, nominal,
                    start_indices=channel_starts,
                    end_indices=channel_ends, polarities=signs)
                point_channels[channel] = {
                    "measurement": channel_measurement,
                    "average": channel_measurement.averaged_waveform,
                    "expected_peaks": nominal,
                    "peaks": channel_measurement.peak_indices,
                    "starts": channel_measurement.start_indices,
                    "ends": channel_measurement.end_indices,
                    "amplitudes": channel_measurement.per_peak_height.mean(axis=0),
                    "height": channel_measurement.absolute_height,
                    "source": spec["xbar_sources"][channel],
                    "stimulus_enabled": work["stimulus_enabled"],
                    "optical_latency_samples": latency,
                    "optical_correlation_score": work["candidate_score"],
                    "optical_correlation_valid": timing_valid,
                    "optical_timing_source": timing_source,
                    "loopback_lags": np.zeros(
                        reference_reps.shape[0], dtype=np.int32),
                    "loopback_scores": np.full(
                        reference_reps.shape[0], np.nan),
                }
            primary = point_channels[spec["analysis_adc"]]
            measurement = primary["measurement"]
            average = primary["average"]
            peaks = primary["peaks"]
            starts = primary["starts"]
            ends = primary["ends"]
            amplitudes = primary["amplitudes"]
            per_rep = measurement.per_rep_height
            height = primary["height"]
            detection_error = ""
            reference_latency = int(
                reference.peak_indices[0] - step_sample)
            optical_latency = primary["optical_latency_samples"]
            correlation_score = primary["optical_correlation_score"]
            capture_dir = os.path.abspath(os.path.expanduser(self.args.capture_dir))
            os.makedirs(capture_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            point_name = (spec["net"] if len(spec["nets"]) == 1 else
                          f"{len(spec['nets'])}_heaters")
            path = os.path.join(capture_dir, f"optical_point_{point_name}_{stamp}.npz")
            payload = {
                "averaged_waveform_v": average,
                "peak_indices": peaks,
                "spike_start_indices": starts,
                "spike_end_indices": ends,
                "spike_amplitudes_v": amplitudes,
                "spike_polarities": (measurement.polarities if not detection_error
                                     else np.empty(0, dtype=np.int8)),
                "spike_widths_samples": (measurement.widths_samples if not detection_error
                                         else np.empty(0, dtype=np.int32)),
                "spike_fwhm_samples": (measurement.fwhm_samples if not detection_error
                                       else np.empty(0, dtype=np.int32)),
                "spike_areas_v_ns": (measurement.areas_v_samples if not detection_error
                                     else np.empty(0, dtype=np.float64)),
                "per_rep_height_v": per_rep,
                "heater_voltage_v": voltage,
                "heater_net": spec["net"],
                "heater_nets": np.asarray(spec["nets"]),
                "analysis_adc_channel": spec["analysis_adc"],
                "reference_adc_channel": spec["reference_adc"],
                "loopback_lag_samples": np.zeros(
                    reference_reps.shape[0], dtype=np.int32),
                "loopback_correlation_scores": np.full(
                    reference_reps.shape[0], np.nan),
                "reference_first_peak_latency_samples": reference_latency,
                "optical_latency_from_loopback_samples": optical_latency,
                "optical_loopback_correlation_score": correlation_score,
                "neuron_profiles": np.asarray([
                    spec["profiles"][neuron] for neuron in range(4)]),
                "external_current_ma": spec["current_ma"],
                "external_current_frequency_hz": spec["current_actual_frequency_hz"],
                "static_current_ma": 0.0,
            }
            for channel, stack in raw_stacks.items():
                payload[f"raw_ch{channel}"] = stack
            for channel, channel_result in point_channels.items():
                payload[f"averaged_waveform_ch{channel}_v"] = (
                    channel_result["average"])
                payload[f"expected_peak_indices_ch{channel}"] = (
                    channel_result["expected_peaks"])
                payload[f"peak_indices_ch{channel}"] = channel_result["peaks"]
                payload[f"spike_amplitudes_ch{channel}_v"] = (
                    channel_result["amplitudes"])
                payload[f"optical_latency_ch{channel}_samples"] = (
                    channel_result["optical_latency_samples"])
                payload[f"optical_correlation_ch{channel}"] = (
                    channel_result["optical_correlation_score"])
            np.savez_compressed(path, **payload)
            return {
                "kind": "point", "spec": spec, "voltage": voltage,
                "heater_voltages": requested, "height": height,
                "path": path, "average": average, "peaks": peaks,
                "starts": starts, "ends": ends, "amplitudes": amplitudes,
                "detection_error": detection_error,
                "reference_latency_samples": reference_latency,
                "optical_latency_samples": optical_latency,
                "optical_correlation_score": correlation_score,
                "loopback_lags": np.zeros(
                    reference_reps.shape[0], dtype=np.int32),
                "loopback_scores": primary["loopback_scores"],
                "reference_average": reference_average,
                "reference_measurement": reference,
                "channel_results": point_channels,
            }
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return {"_err": f"{type(exc).__name__}: {exc}"}
    def _clear_mzi_tone_curves(self):
        for curve in getattr(self, "_mzi_tone_curves", []):
            self.mzi_plot.removeItem(curve)
        self._mzi_tone_curves = []
    def _on_mzi_calibrate(self):
        if not self.dac or self._mzi_running:
            return
        try:
            spec = self._mzi_gui_spec()
            spacing = self.mzi_spacing.currentData()
            explicit = (parse_heater_voltages(self.mzi_voltage_list.text())
                        if spacing == "explicit" else None)
            voltages, directions = calibration_voltage_sequence(
                self.mzi_vstart.value(), self.mzi_vstop.value(),
                self.mzi_points.value(), self.mzi_reverse.isChecked(),
                spacing=spacing, explicit=explicit)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid optical sweep", str(exc))
            return
        spec["voltages"] = voltages
        spec["directions"] = directions
        spec["spacing"] = spacing
        self._clear_mzi_tone_curves()
        self.mzi_curve_fwd.setData([], [])
        self.mzi_curve_rev.setData([], [])
        self._mzi_begin(
            len(voltages),
            f"Programming test, then sweeping {spec['sweep_label']}...")
        self._bg(lambda: self.mzi_cal_result.emit(self._run_mzi_calibration(spec)))

    def _on_mzi_cal_cancel(self):
        if self._mzi_running:
            self._mzi_cancel.set()
            self.mzi_status.setText(
                "Stopping after the current trigger-aligned capture...")

    def _run_mzi_calibration(self, spec):
        result = {
            "kind": "sweep", "spec": spec, "heater_dirs": [], "metrics": [],
            "voltages": [], "directions": [], "cancelled": False,
            "tone_points": [],
        }
        experiment_dir = None
        self._mzi_active_sweep_nets = tuple(spec["nets"])
        try:
            self._program_mzi_test(spec)
            if hasattr(self.dac, "get_sources"):
                try:
                    live_sources, xbar_value = self.dac.get_sources()
                    spec["xbar_sources"] = list(live_sources)
                    spec["xbar_register17"] = f"0x{xbar_value:04X}"
                except Exception:
                    pass
            self._mzi_controller.connect(
                board_ip=self.args.board_ip, local_ip=self.args.local_ip)
            available = set(self._mzi_controller.available_nets())
            missing = sorted(set(spec["nets"]) - available)
            if missing:
                return {"_err": f"heaters not wired in PICO-002 config: {missing}",
                        "spec": spec}
            experiment_dir = create_experiment(
                self.args.capture_dir, spec["experiment_name"],
                self._mzi_experiment_metadata(spec))
            result["path"] = str(experiment_dir)

            for index, (voltage, direction) in enumerate(zip(
                    spec["voltages"], spec["directions"])):
                if self._mzi_cancel.is_set():
                    result["cancelled"] = True
                    break
                requested = {
                    net: float(voltage) for net in spec["nets"]}
                self._set_mzi_heater_voltages(requested)
                if spec["settle_s"]:
                    time.sleep(spec["settle_s"])
                capture = self._mzi_multisample_with_retries(
                    spec, voltage=float(voltage))
                if "_err" in capture:
                    raise RuntimeError(f"{voltage:.4f} V: {capture['_err']}")
                raw_stacks = {
                    channel: np.asarray(capture["stack"][channel], dtype=np.int16)
                    for channel in range(4)
                }
                if spec.get("mode") == "tone":
                    tone = analyze_tone_capture(
                        {channel: stack.astype(np.float64) * VOLTS_PER_COUNT
                         for channel, stack in raw_stacks.items()},
                        spec["tone_actual_frequency_hz"],
                        reference_adc=spec["reference_adc"],
                        start_sample=spec["response_start_sample"])
                    metric = float(
                        tone["channels"][spec["analysis_adc"]]["amplitude_v"])
                else:
                    raw = raw_stacks[spec["analysis_adc"]]
                    step_sample = spec["response_start_sample"]
                    volts = raw.astype(np.float64) * VOLTS_PER_COUNT
                    baseline = np.median(
                        volts[:, :max(4, step_sample - 10)], axis=1)
                    average = (volts - baseline[:, None]).mean(axis=0)
                    metric = float(np.max(np.abs(average[step_sample:])))
                heater_dir = save_heater_capture(
                    experiment_dir, index=index, voltage_v=float(voltage),
                    direction=int(direction), stacks=raw_stacks,
                    capture_meta={
                        "burst_command": spec.get("capture_command", "BCPT"),
                        "burst": capture.get("meta", {}),
                        "hardware_offsets":
                            np.asarray(capture.get("offs", [])).tolist(),
                        "trigger_diagnostic": capture.get("diag", {}),
                        "heater_nets": list(spec["nets"]),
                        "heater_voltages_v": requested,
                    })
                if spec.get("mode") == "tone":
                    scalar_channels = {
                        str(channel): {
                            key: float(value)
                            for key, value in channel_result.items()
                            if key not in ("average_v", "fitted_v")
                        }
                        for channel, channel_result in tone["channels"].items()
                    }
                    tone_point = {
                        "voltage_v": float(voltage),
                        "direction": int(direction),
                        "channels": scalar_channels,
                    }
                    result["tone_points"].append(tone_point)
                    with open(
                            os.path.join(heater_dir, "tone_analysis.json"),
                            "w", encoding="utf-8") as handle:
                        json.dump(tone_point, handle, indent=2, sort_keys=True)
                        handle.write("\n")
                result["heater_dirs"].append(str(heater_dir))
                result["metrics"].append(metric)
                result["voltages"].append(float(voltage))
                result["directions"].append(int(direction))
                self.mzi_cal_progress.emit(
                    index + 1, len(spec["voltages"]), float(voltage), metric)

            if result["cancelled"] or not result["heater_dirs"]:
                update_manifest(experiment_dir, capture_status="cancelled",
                                completed_utc=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                return result
            update_manifest(
                experiment_dir, capture_status="complete",
                completed_utc=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            if spec.get("mode") == "tone":
                result["voltages"] = np.asarray(
                    result["voltages"], dtype=np.float64)
                result["directions"] = np.asarray(
                    result["directions"], dtype=np.int8)
                result["tone_amplitudes_v"] = {
                    channel: np.asarray([
                        point["channels"][str(channel)]["amplitude_v"]
                        for point in result["tone_points"]], dtype=np.float64)
                    for channel in range(4)
                }
                result["tone_gains"] = {
                    channel: np.asarray([
                        point["channels"][str(channel)]["gain_vs_reference"]
                        for point in result["tone_points"]], dtype=np.float64)
                    for channel in range(3)
                }
                result["tone_latencies_ns"] = {
                    channel: np.asarray([
                        point["channels"][str(channel)]
                        ["latency_modulo_period_ns"]
                        for point in result["tone_points"]], dtype=np.float64)
                    for channel in range(3)
                }
                np.savez_compressed(
                    os.path.join(experiment_dir, "tone_summary.npz"),
                    voltages_v=result["voltages"],
                    directions=result["directions"],
                    **{
                        f"amplitude_adc{channel}_v": values
                        for channel, values in
                        result["tone_amplitudes_v"].items()},
                    **{
                        f"gain_adc{channel}_vs_adc3": values
                        for channel, values in result["tone_gains"].items()},
                    **{
                        f"latency_adc{channel}_ns": values
                        for channel, values in
                        result["tone_latencies_ns"].items()})
                result["absolute"] = result["tone_amplitudes_v"][0]
                return result
            analysis = process_experiment(experiment_dir)
            result.update(analysis)
            return result
        except Exception as exc:  # noqa: BLE001
            if experiment_dir is not None:
                try:
                    update_manifest(
                        experiment_dir, capture_status="failed",
                        failure=f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
            import traceback
            traceback.print_exc()
            return {"_err": f"{type(exc).__name__}: {exc}", "spec": spec}
        finally:
            if self._mzi_controller.connected:
                try:
                    self._set_mzi_heater_voltages({
                        net: spec["restore_v"] for net in spec["nets"]
                    })
                except Exception:
                    pass

    def _mzi_multisample_with_retries(self, spec, *, voltage, attempts=3):
        """Retry a transient Ethernet failure without advancing the heater.

        Each call performs a complete, newly triggered BCPT/BCPD acquisition.
        Its internal BRDO retries first exhaust rereads of the same DDR image. Only
        incomplete UDP drains and readout handshakes are retried here;
        deterministic capture-engine failures remain fatal.
        """
        retryable = (
            "UDP drain incomplete",
            "BRST registration timed out",
            "BRDO failed",
        )
        attempts = max(1, int(attempts))
        last = None
        for attempt in range(attempts):
            if spec.get("capture_command", "BCPT") == "BCPD":
                last = self._multisample_once(
                    spec["capture_bytes"], spec["reps"], command="BCPD")
            else:
                last = self._multisample_once(
                    spec["capture_bytes"], spec["reps"])
            error = str(last.get("_err", ""))
            if not error:
                return last
            if (attempt + 1 >= attempts or
                    not any(token in error for token in retryable)):
                return last
            self.mzi_setup_progress.emit(
                f"{voltage:.4f} V Ethernet read incomplete; repeating the "
                f"trigger-aligned capture ({attempt + 2}/{attempts})...")
            time.sleep(0.75)
        return last
    def _on_mzi_cal_progress(self, done, total, voltage, height):
        self.mzi_progress.setMaximum(total)
        self.mzi_progress.setValue(done)
        self.mzi_status.setText(
            f"{done}/{total}: selected heaters {voltage:.4f} V, "
            f"response {height * 1e3:.3f} mV")


    def _refresh_mzi_point_preview(self, *_):
        if self._mzi_last_point_result is not None:
            self._show_mzi_point_diagnostics(self._mzi_last_point_result)

    def _show_mzi_tone_point(self, result):
        self.mzi_reference_plot.clear()
        self.mzi_spike_plot.clear()
        spec = result["spec"]
        analysis = result["tone_analysis"]
        reference = analysis["channels"][spec["reference_adc"]]
        reference_mv = np.asarray(reference["average_v"]) * 1e3
        reference_fit_mv = np.asarray(reference["fitted_v"]) * 1e3
        self.mzi_reference_plot.plot(
            reference_mv, pen=pg.mkPen("#E8EDF2", width=1.0))
        self.mzi_reference_plot.plot(
            reference_fit_mv, pen=pg.mkPen("#F6AE2D", width=1.5))
        self.mzi_reference_plot.setTitle(
            f"ADC3 electrical reference | {spec['reps']} aligned capture(s) "
            f"| fitted amplitude {reference['amplitude_v'] * 1e3:.3f} mV")
        self.mzi_reference_plot.setYRange(
            self._mzi_reference_trace_range[0],
            self._mzi_reference_trace_range[1], padding=0.0)

        channel = int(self.mzi_preview_adc.currentData())
        measured = analysis["channels"][channel]
        average_mv = np.asarray(measured["average_v"]) * 1e3
        fitted_mv = np.asarray(measured["fitted_v"]) * 1e3
        self.mzi_spike_plot.plot(
            average_mv, pen=pg.mkPen(CH_COLORS[channel], width=1.0))
        self.mzi_spike_plot.plot(
            fitted_mv, pen=pg.mkPen("#F6AE2D", width=1.5))
        state = "enabled" if channel in spec["tone_inputs"] else "not driven"
        self.mzi_spike_plot.setTitle(
            f"ADC{channel} ({state}) | amplitude "
            f"{measured['amplitude_v'] * 1e3:.3f} mV | gain/ADC3 "
            f"{measured['gain_vs_reference']:.6f} | latency "
            f"{measured['latency_modulo_period_ns']:.3f} ns modulo period")
        self.mzi_spike_plot.setYRange(
            self._mzi_optical_trace_range[0],
            self._mzi_optical_trace_range[1], padding=0.0)

    def _show_mzi_point_diagnostics(self, result):
        """Show exactly what the pre-sweep point acquisition measured."""

        if result.get("mode") == "tone":
            self._show_mzi_tone_point(result)
            return
        self.mzi_reference_plot.clear()
        self.mzi_spike_plot.clear()
        spec = result["spec"]
        repetitions = int(spec["reps"])
        averaging = (
            "single trigger, N=1 (not averaged)" if repetitions == 1 else
            f"arithmetic average of N={repetitions} aligned triggers")

        reference = result["reference_measurement"]
        reference_mv = np.asarray(
            result["reference_average"], dtype=np.float64) * 1e3
        reference_polarity = dominant_spike_polarity(reference)
        reference_peaks = np.asarray(
            reference.peak_indices[
                reference.polarities == reference_polarity],
            dtype=np.int32)
        display_x, display_y = event_preserving_trace(
            reference_mv, reference_peaks)
        self.mzi_reference_plot.plot(
            display_x, display_y, pen=pg.mkPen("#E8EDF2", width=1.1))
        valid_reference = (
            (reference_peaks >= 0) & (reference_peaks < reference_mv.size))
        if np.any(valid_reference):
            peaks = reference_peaks[valid_reference]
            self.mzi_reference_plot.plot(
                peaks, reference_mv[peaks], pen=None, symbol="x",
                symbolSize=7, symbolPen=pg.mkPen("#F6AE2D", width=1.5),
                symbolBrush=None)
        self.mzi_reference_plot.setTitle(
            f"ADC3 electrical reference | {averaging} | "
            f"{reference_peaks.size} main-polarity spikes")
        self.mzi_reference_plot.getViewBox().enableAutoRange(
            axis=pg.ViewBox.YAxis, enable=True)

        channel = int(self.mzi_preview_adc.currentData())
        channel_result = result["channel_results"].get(channel)
        if channel_result is None:
            self.mzi_spike_plot.setTitle(
                f"ADC{channel}: no optical result in this capture")
            return
        average_mv = np.asarray(
            channel_result["average"], dtype=np.float64) * 1e3
        measured = np.asarray(channel_result["peaks"], dtype=np.int32)
        expected = np.asarray(
            channel_result["expected_peaks"], dtype=np.int32)
        important = np.unique(np.concatenate((expected, measured)))
        display_x, display_y = event_preserving_trace(
            average_mv, important)
        self.mzi_spike_plot.plot(
            display_x, display_y,
            pen=pg.mkPen(CH_COLORS[channel], width=1.1))
        expected_valid = (expected >= 0) & (expected < average_mv.size)
        if np.any(expected_valid):
            locations = expected[expected_valid]
            self.mzi_spike_plot.plot(
                locations, average_mv[locations], pen=None, symbol="+",
                symbolSize=7, symbolPen=pg.mkPen("#4FC3F7", width=1.4),
                symbolBrush=None)
        measured_valid = (measured >= 0) & (measured < average_mv.size)
        if (channel_result["optical_correlation_valid"] and
                np.any(measured_valid)):
            locations = measured[measured_valid]
            self.mzi_spike_plot.plot(
                locations, average_mv[locations], pen=None, symbol="x",
                symbolSize=7, symbolPen=pg.mkPen("#F6AE2D", width=1.5),
                symbolBrush=None)

        lag = int(channel_result["optical_latency_samples"])
        score = float(channel_result["optical_correlation_score"])
        validity = (
            "valid" if channel_result["optical_correlation_valid"] else
            "below threshold")
        self.mzi_spike_plot.setTitle(
            f"ADC{channel} from {channel_result['source']} | {averaging} | "
            f"lag {lag:+d} samples | normalized corr {score:.3f} ({validity})")
        self.mzi_spike_plot.setYRange(
            float(self.mzi_trace_y_min.value()),
            float(self.mzi_trace_y_max.value()), padding=0.0)
        if important.size:
            first = int(np.min(important))
            last = int(np.max(important))
            margin = max(32, int(max(1, last - first) * 0.05))
            self.mzi_spike_plot.setXRange(
                max(0, first - margin),
                min(average_mv.size - 1, last + margin), padding=0.0)
    def _show_mzi_spikes(self, average, peaks, starts, ends, amplitudes):
        self.mzi_spike_plot.clear()
        trace_mv = np.asarray(average, dtype=np.float64) * 1e3
        if trace_mv.size == 0:
            return
        self.mzi_spike_plot.plot(
            np.arange(trace_mv.size), trace_mv,
            pen=pg.mkPen("#E8EDF2", width=1.1))
        if len(amplitudes):
            self.mzi_spike_plot.addItem(pg.InfiniteLine(
                pos=float(np.mean(amplitudes)) * 1e3, angle=0, movable=False,
                pen=pg.mkPen("#F6AE2D", width=1.4)))
        for peak, start, end, amplitude in zip(peaks, starts, ends, amplitudes):
            region = pg.LinearRegionItem(
                values=(int(start), int(end)), movable=False,
                brush=pg.mkBrush(79, 195, 247, 38),
                pen=pg.mkPen(79, 195, 247, 150, width=1))
            self.mzi_spike_plot.addItem(region)
            marker = pg.ScatterPlotItem(
                [int(peak)], [trace_mv[int(peak)]], size=7,
                brush=pg.mkBrush("#FFB74D"), pen=pg.mkPen("#101418"))
            self.mzi_spike_plot.addItem(marker)
            label = pg.TextItem(
                f"{float(amplitude) * 1e3:.2f} mV",
                color="#FFCF88", anchor=(0.5, 1.0))
            label.setPos(int(peak), trace_mv[int(peak)])
            self.mzi_spike_plot.addItem(label)
        if len(peaks):
            margin = max(20, int((int(ends[-1]) - int(starts[0])) * 0.04))
            self.mzi_spike_plot.setXRange(
                max(0, int(starts[0]) - margin),
                min(trace_mv.size - 1, int(ends[-1]) + margin), padding=0)

    def _on_mzi_import_experiment(self):
        start = os.path.abspath(os.path.expanduser(self.args.capture_dir))
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Import optical experiment", start)
        if not path:
            return
        self.mzi_import_btn.setEnabled(False)
        self.mzi_dataset_status.setText(
            f"Loading and analyzing {os.path.abspath(path)}...")
        self.mzi_dataset_status.setStyleSheet(
            "color:#FFB74D; font-size:11px;")
        self._bg(lambda: self.mzi_import_result.emit(
            self._run_mzi_import_experiment(path)))

    def _load_mzi_tone_experiment(self, experiment_dir, manifest):
        captures = sorted(
            manifest.get("heater_captures", []),
            key=lambda item: int(item["index"]))
        if not captures:
            raise ValueError("tone experiment has no heater captures")
        stimulus = manifest.get("stimulus", {})
        frequency_hz = float(stimulus["actual_frequency_hz"])
        acquisition = manifest.get("acquisition", {})
        reference_adc = int(
            acquisition.get("reference_adc_channel", 3))
        analyses = []
        voltages = []
        directions = []
        points = []
        for descriptor in captures:
            capture_dir = os.path.join(
                experiment_dir, descriptor["directory"])
            raw_path = os.path.join(capture_dir, "raw_captures.npz")
            with np.load(raw_path, allow_pickle=False) as raw:
                stacks_v = {
                    channel: np.asarray(
                        raw[f"raw_ch{channel}"], dtype=np.float64
                    ) * VOLTS_PER_COUNT
                    for channel in range(4)
                }
            analysis = analyze_tone_capture(
                stacks_v, frequency_hz, reference_adc=reference_adc,
                start_sample=64)
            analyses.append(analysis)
            voltage = float(descriptor["heater_voltage_v"])
            direction = int(
                str(descriptor.get("direction", "forward")).lower()
                == "reverse")
            voltages.append(voltage)
            directions.append(direction)
            points.append({
                "voltage_v": voltage,
                "direction": direction,
                "channels": {
                    str(channel): {
                        key: float(value)
                        for key, value in channel_result.items()
                        if key not in ("average_v", "fitted_v")
                    }
                    for channel, channel_result
                    in analysis["channels"].items()
                },
            })
        result = {
            "kind": "imported_tone_sweep",
            "mode": "tone",
            "manifest": manifest,
            "path": experiment_dir,
            "voltages": np.asarray(voltages, dtype=np.float64),
            "directions": np.asarray(directions, dtype=np.int8),
            "tone_points": points,
            "tone_analyses": analyses,
            "tone_frequency_hz": frequency_hz,
            "reference_adc": reference_adc,
        }
        result["tone_amplitudes_v"] = {
            channel: np.asarray([
                analysis["channels"][channel]["amplitude_v"]
                for analysis in analyses], dtype=np.float64)
            for channel in range(4)
        }
        result["tone_gains"] = {
            channel: np.asarray([
                analysis["channels"][channel]["gain_vs_reference"]
                for analysis in analyses], dtype=np.float64)
            for channel in range(3)
        }
        result["tone_latencies_ns"] = {
            channel: np.asarray([
                analysis["channels"][channel]["latency_modulo_period_ns"]
                for analysis in analyses], dtype=np.float64)
            for channel in range(3)
        }
        return result

    def _run_mzi_import_experiment(self, path):
        try:
            experiment_dir = os.path.abspath(os.path.expanduser(str(path)))
            manifest = load_manifest(experiment_dir)
            if not isinstance(manifest.get("heater_captures"), list):
                raise ValueError(
                    "experiment.json does not contain heater_captures")
            if (manifest.get("stimulus", {}).get("mode")
                    == "shared_dds_pure_tone"):
                return self._load_mzi_tone_experiment(
                    experiment_dir, manifest)
            result = process_experiment(experiment_dir)
            result["kind"] = "imported_sweep"
            result["manifest"] = load_manifest(experiment_dir)
            result["path"] = experiment_dir
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "kind": "imported_sweep",
                "_err": f"{type(exc).__name__}: {exc}",
                "path": os.path.abspath(os.path.expanduser(str(path))),
            }

    def _on_mzi_import_result(self, result):
        self.mzi_import_btn.setEnabled(True)
        if not isinstance(result, dict) or "_err" in result:
            error = (result.get("_err", "no result")
                     if isinstance(result, dict) else "no result")
            self.mzi_dataset_status.setText(
                f"Import failed: {error}")
            self.mzi_dataset_status.setStyleSheet(
                "color:#E57373; font-size:11px;")
            return
        self._remember_mzi_dataset(result)

    def _remember_mzi_dataset(self, result):
        path = os.path.normcase(os.path.abspath(result["path"]))
        if "manifest" not in result:
            try:
                result["manifest"] = load_manifest(path)
            except (OSError, ValueError, json.JSONDecodeError):
                result["manifest"] = {}
        self._mzi_result_datasets[path] = result
        manifest = result["manifest"]
        name = str(manifest.get("experiment_name") or os.path.basename(path))
        label = f"{name} | {os.path.basename(path)}"
        index = self.mzi_dataset_combo.findData(path)
        self.mzi_dataset_combo.blockSignals(True)
        if index < 0:
            self.mzi_dataset_combo.addItem(label, path)
            index = self.mzi_dataset_combo.count() - 1
        else:
            self.mzi_dataset_combo.setItemText(index, label)
        self.mzi_dataset_combo.setCurrentIndex(index)
        self.mzi_dataset_combo.setEnabled(True)
        self.mzi_dataset_combo.blockSignals(False)
        self._display_mzi_dataset(result)

    def _on_mzi_dataset_selected(self, index):
        if index < 0:
            return
        path = self.mzi_dataset_combo.itemData(index)
        result = self._mzi_result_datasets.get(path)
        if result is not None:
            self._display_mzi_dataset(result)

    def _show_mzi_tone_measurements(self, result):
        self._clear_mzi_trace_grid()
        selected_channel = self._selected_mzi_result_channel()
        analyses = list(result.get("tone_analyses", []))
        voltages = np.asarray(result.get("voltages", []), dtype=np.float64)
        directions = np.asarray(result.get("directions", []), dtype=np.int8)
        if not analyses:
            self.mzi_results_summary.setText(
                "No completed pure-tone points are available.")
            return
        columns = 4
        rows = (len(analyses) + columns - 1) // columns
        for column in range(columns):
            self.mzi_trace_grid.setColumnStretch(column, 1)
        amplitudes = []
        gains = []
        latencies = []
        for index, analysis in enumerate(analyses):
            channel_result = analysis["channels"][selected_channel]
            average_mv = np.asarray(
                channel_result["average_v"], dtype=np.float64) * 1e3
            fitted_mv = np.asarray(
                channel_result["fitted_v"], dtype=np.float64) * 1e3
            plot = pg.PlotWidget()
            plot.setMinimumSize(240, 135)
            plot.setMaximumHeight(180)
            plot.setBackground("#101418")
            plot.showGrid(x=True, y=True, alpha=0.18)
            if index % columns == 0:
                plot.setLabel("left", "output", units="mV")
            if index // columns == rows - 1:
                plot.setLabel("bottom", "ADC sample", units="ns")
            display_x, display_y = event_preserving_trace(
                average_mv, np.empty(0, dtype=np.int32))
            plot.plot(
                display_x, display_y,
                pen=pg.mkPen(
                    "#E8EDF2" if selected_channel == 3
                    else CH_COLORS[selected_channel], width=1.0))
            finite = np.isfinite(fitted_mv)
            plot.plot(
                np.flatnonzero(finite), fitted_mv[finite],
                pen=pg.mkPen("#F6AE2D", width=1.5))
            voltage = (
                float(voltages[index])
                if index < voltages.size else float("nan"))
            direction = (
                "R" if index < directions.size and directions[index] else "F")
            amplitude_mv = float(channel_result["amplitude_v"]) * 1e3
            plot.setTitle(
                f"#{index + 1} | {voltage:.4f} V {direction} | "
                f"ADC{selected_channel} {amplitude_mv:.3f} mV",
                size="8pt")
            plot.setYRange(
                float(self.mzi_trace_y_min.value()),
                float(self.mzi_trace_y_max.value()), padding=0.0)
            plot.setToolTip(
                "White/colored trace is the trigger-aligned arithmetic "
                "average; gold is the least-squares fit at the programmed "
                "DDS frequency.")
            self.mzi_trace_grid.addWidget(
                plot, index // columns, index % columns)
            self.mzi_sweep_trace_plots.append(plot)
            amplitudes.append(amplitude_mv)
            if selected_channel != 3:
                gains.append(float(channel_result["gain_vs_reference"]))
                latencies.append(float(
                    channel_result["latency_modulo_period_ns"]))
        if selected_channel == 3:
            self.mzi_results_summary.setText(
                f"ADC3 electrical reference: {len(analyses)} fitted points; "
                f"amplitude {min(amplitudes):.3f}.."
                f"{max(amplitudes):.3f} mV.")
        else:
            self.mzi_results_summary.setText(
                f"ADC{selected_channel}: {len(analyses)} fitted points; "
                f"absolute amplitude {min(amplitudes):.3f}.."
                f"{max(amplitudes):.3f} mV; gain vs ADC3 "
                f"{min(gains):.6f}..{max(gains):.6f}; latency modulo the "
                f"tone period {min(latencies):.3f}.."
                f"{max(latencies):.3f} ns.")

    def _show_mzi_tone_result_curve(self, result):
        plot = self.mzi_result_curve_plot
        plot.clear()
        legend = plot.plotItem.legend
        if legend is None:
            legend = plot.addLegend(offset=(8, 8))
        else:
            legend.clear()
        voltages = np.asarray(result["voltages"], dtype=np.float64)
        directions = np.asarray(result["directions"], dtype=np.int8)
        for channel in range(3):
            values = np.asarray(
                result["tone_amplitudes_v"][channel],
                dtype=np.float64) * 1e3
            for direction, suffix, style in (
                    (0, "forward", QtCore.Qt.SolidLine),
                    (1, "reverse", QtCore.Qt.DashLine)):
                selected = directions == direction
                if not np.any(selected):
                    continue
                plot.plot(
                    voltages[selected], values[selected],
                    pen=pg.mkPen(
                        CH_COLORS[channel], width=1.6, style=style),
                    symbol="o", symbolSize=5,
                    symbolBrush=CH_COLORS[channel],
                    name=f"ADC{channel} {suffix}")
        plot.setTitle("")
        plot.setLabel("left", "fitted tone amplitude", units="mV")
        plot.setLabel("bottom", "heater", units="V")
        plot.enableAutoRange(axis=pg.ViewBox.YAxis)
    def _display_mzi_dataset(self, result):
        if result.get("mode") == "tone":
            self._show_mzi_tone_measurements(result)
            self._show_mzi_tone_result_curve(result)
            manifest = result.get("manifest", {})
            point_count = len(result.get("tone_analyses", []))
        else:
            self._show_mzi_sweep_measurements(result)
            self._show_mzi_result_curve(result)
            manifest = result.get("manifest", {})
            point_count = len(result.get("measurements", []))
        name = str(
            manifest.get("experiment_name") or os.path.basename(result["path"]))
        self.mzi_dataset_status.setText(
            f"Loaded {name}: {point_count} processed point(s). "
            f"Directory: {result['path']}")
        self.mzi_dataset_status.setStyleSheet(
            "color:#81C784; font-size:11px;")
        self.workspace_tabs.setCurrentIndex(2)
        self.mzi_results_tabs.setCurrentIndex(0)
    def _clear_mzi_trace_grid(self):
        while self.mzi_trace_grid.count():
            item = self.mzi_trace_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.mzi_sweep_trace_plots = []

    def _selected_mzi_result_channel(self):
        return int(self.mzi_channel_tabs.currentIndex())

    def _on_mzi_channel_tab_changed(self, _index):
        channel = self._selected_mzi_result_channel()
        lower, upper = (self._mzi_reference_trace_range if channel == 3
                        else self._mzi_optical_trace_range)
        self.mzi_trace_y_min.blockSignals(True)
        self.mzi_trace_y_max.blockSignals(True)
        self.mzi_trace_y_min.setValue(lower)
        self.mzi_trace_y_max.setValue(upper)
        self.mzi_trace_y_min.blockSignals(False)
        self.mzi_trace_y_max.blockSignals(False)
        self.mzi_trace_scale_label.setText(
            "ADC3 reference Y range" if channel == 3 else
            "Optical Y range shared by ADC0-ADC2")
        self._apply_mzi_peak_analysis()

    def _apply_mzi_trace_scale(self):
        lower = float(self.mzi_trace_y_min.value())
        upper = float(self.mzi_trace_y_max.value())
        if lower >= upper:
            self.mzi_results_summary.setText(
                "Trace Y minimum must be lower than the maximum.")
            return
        if self._selected_mzi_result_channel() == 3:
            self._mzi_reference_trace_range = (lower, upper)
        else:
            self._mzi_optical_trace_range = (lower, upper)
        for plot in self.mzi_sweep_trace_plots:
            plot.setYRange(lower, upper, padding=0.0)

    def _fit_mzi_trace_scale(self):
        values = []
        for plot in self.mzi_sweep_trace_plots:
            for item in plot.listDataItems():
                _x, y = item.getData()
                if y is None:
                    continue
                finite = np.asarray(y, dtype=np.float64)
                finite = finite[np.isfinite(finite)]
                if finite.size:
                    values.append(finite)
        if not values:
            return
        combined = np.concatenate(values)
        lower = float(np.min(combined))
        upper = float(np.max(combined))
        span = upper - lower
        padding = 0.05 * span if span > 0.0 else max(abs(upper) * 0.05, 1.0)
        self.mzi_trace_y_min.setValue(lower - padding)
        self.mzi_trace_y_max.setValue(upper + padding)
        self._apply_mzi_trace_scale()

    def _apply_mzi_peak_analysis(self):
        index = self.mzi_dataset_combo.currentIndex()
        if index < 0:
            return
        path = self.mzi_dataset_combo.itemData(index)
        result = self._mzi_result_datasets.get(path)
        if result is None:
            return
        active_tab = self.mzi_results_tabs.currentIndex()
        if result.get("mode") == "tone":
            self._show_mzi_tone_measurements(result)
            self._show_mzi_tone_result_curve(result)
        else:
            self._show_mzi_sweep_measurements(result)
            self._show_mzi_result_curve(result)
        self.mzi_results_tabs.setCurrentIndex(active_tab)

    @staticmethod
    def _mzi_channel_result(result, channel):
        channel_results = result.get("channel_results", {})
        selected = channel_results.get(channel)
        if selected is None:
            selected = channel_results.get(str(channel))
        if selected is not None:
            return selected
        if int(channel) == int(result.get("primary_optical_adc", 0)):
            return result
        return None

    @staticmethod
    def _mzi_lane_timing_text(channel_result):
        latency = int(channel_result.get("optical_latency_samples", 0))
        score = channel_result.get("optical_correlation_score")
        score_text = (
            "n/a" if score is None or not np.isfinite(float(score))
            else f"{abs(float(score)):.3f}")
        timing_source = str(
            channel_result.get("optical_timing_source", "lane correlation"))
        validity = (
            f"valid, {timing_source}"
            if channel_result.get("optical_correlation_valid", False)
            else "INVALID - no correlated response")
        return (
            f"latency {latency:+d} samples ({latency:+d} ns @ 1 GS/s), "
            f"|corr|={score_text} ({validity})")
    def _mzi_peak_analyses(self, result, channel=None):
        if channel is None:
            channel = self._selected_mzi_result_channel()
        channel_result = self._mzi_channel_result(result, channel)
        if channel_result is None:
            channel_result = result
        measurements = list(channel_result.get("measurements", []))
        detected = list(channel_result.get("independent_measurements", []))
        polarity = str(self.mzi_peak_polarity.currentData())
        sigma_limit = float(self.mzi_outlier_sigma.value())
        filter_enabled = bool(self.mzi_outlier_filter.isChecked())
        return [
            analyze_optical_peaks(
                measurement,
                detected[index] if index < len(detected) else None,
                polarity=polarity, sigma_limit=sigma_limit,
                filter_enabled=filter_enabled)
            for index, measurement in enumerate(measurements)
        ]

    @staticmethod
    def _normalize_mzi_values(values, constant_voltage):
        values = np.asarray(values, dtype=np.float64)
        if constant_voltage:
            return np.full_like(values, np.nan)
        minimum = float(np.min(values))
        span = float(np.max(values) - minimum)
        return ((values - minimum) / span
                if span > np.finfo(np.float64).eps
                else np.zeros_like(values))

    def _show_mzi_sweep_measurements(self, result):
        self._clear_mzi_trace_grid()
        selected_channel = self._selected_mzi_result_channel()
        channel_results = {
            channel: self._mzi_channel_result(result, channel)
            for channel in range(3)
        }
        channel_results = {
            channel: value for channel, value in channel_results.items()
            if value is not None
        }
        primary = self._mzi_channel_result(
            result, int(result.get("primary_optical_adc", 0))) or result
        point_count = len(primary.get("measurements", []))
        voltages = np.asarray(result.get("voltages", []), dtype=np.float64)
        directions = np.asarray(result.get("directions", []), dtype=np.int8)
        reference_averages = list(result.get("reference_averages", []))
        if not point_count:
            self.mzi_results_summary.setText(
                "No completed post-processed sweep points are available.")
            return

        columns = 4
        rows = (point_count + columns - 1) // columns
        for column in range(columns):
            self.mzi_trace_grid.setColumnStretch(column, 1)
        analyses_by_channel = {
            channel: self._mzi_peak_analyses(result, channel)
            for channel in channel_results
        }

        for index in range(point_count):
            plot = pg.PlotWidget()
            plot.setMinimumSize(240, 135)
            plot.setMaximumHeight(180)
            plot.setBackground("#101418")
            plot.showGrid(x=True, y=True, alpha=0.18)
            if index % columns == 0:
                plot.setLabel("left", "output", units="mV")
            if index // columns == rows - 1:
                plot.setLabel("bottom", "ADC sample", units="ns")

            if selected_channel == 3:
                if index < len(reference_averages):
                    reference_mv = np.asarray(
                        reference_averages[index], dtype=np.float64) * 1e3
                    reference = result.get("reference_measurement")
                    if reference is None:
                        reference_peaks = np.empty(0, dtype=np.int32)
                    else:
                        reference_polarity = dominant_spike_polarity(reference)
                        reference_peaks = np.asarray(
                            reference.peak_indices[
                                reference.polarities == reference_polarity],
                            dtype=np.int32)
                    display_x, display_y = event_preserving_trace(
                        reference_mv, reference_peaks)
                    plot.plot(
                        display_x, display_y,
                        pen=pg.mkPen("#E8EDF2", width=1.1))
                    if reference is not None:
                        peaks = reference_peaks
                        valid = (peaks >= 0) & (peaks < reference_mv.size)
                        plot.plot(
                            peaks[valid], reference_mv[peaks[valid]],
                            pen=None, symbol="x", symbolSize=6,
                            symbolPen=pg.mkPen("#F6AE2D", width=1.4),
                            symbolBrush=None)
                plot.setToolTip(
                    "ADC3 is averaged independently and supplies the clean "
                    "main-polarity event schedule used to measure each optical "
                    "lane's latency. Opposite AC-recovery detections are hidden; "
                    "no captured trace is shifted.")
            else:
                channel_result = channel_results.get(selected_channel)
                if channel_result is not None:
                    measurement = channel_result["measurements"][index]
                    analysis = analyses_by_channel[selected_channel][index]
                    average_mv = np.asarray(
                        measurement.averaged_waveform, dtype=np.float64) * 1e3
                    display_x, display_y = event_preserving_trace(
                        average_mv, analysis.peak_indices)
                    plot.plot(
                        display_x, display_y,
                        pen=pg.mkPen(CH_COLORS[selected_channel], width=1.1))
                    correlation_valid = bool(
                        channel_result.get("optical_correlation_valid", False))
                    accepted = analysis.accepted
                    rejected = ~accepted
                    if correlation_valid and np.any(accepted):
                        plot.addItem(pg.InfiniteLine(
                            pos=analysis.filtered_mean_v * 1e3, angle=0,
                            movable=False,
                            pen=pg.mkPen("#F6AE2D", width=1.5)))
                        plot.plot(
                            analysis.peak_indices[accepted],
                            analysis.waveform_values_v[accepted] * 1e3,
                            pen=None, symbol="x", symbolSize=7,
                            symbolPen=pg.mkPen("#F6AE2D", width=1.5),
                            symbolBrush=None)
                    if (correlation_valid and
                            self.mzi_outlier_filter.isChecked() and
                            np.any(rejected)):
                        plot.plot(
                            analysis.peak_indices[rejected],
                            analysis.waveform_values_v[rejected] * 1e3,
                            pen=None, symbol="x", symbolSize=7,
                            symbolPen=pg.mkPen("#EF5350", width=1.5),
                            symbolBrush=None)
                    plot.setToolTip(
                        f"ADC{selected_channel}; DAC source "
                        f"{channel_result.get('source', 'unknown')}; "
                        f"{self._mzi_lane_timing_text(channel_result)}; "
                        f"{analysis.source}; accepted "
                        f"{int(np.count_nonzero(accepted))}/"
                        f"{analysis.peak_indices.size} peaks.")

            voltage = (float(voltages[index])
                       if index < voltages.size else float("nan"))
            direction = (
                "R" if index < directions.size and directions[index] else "F")
            view_label = (
                "ADC3 ref" if selected_channel == 3 else
                f"ADC{selected_channel}")
            plot.setTitle(
                f"#{index + 1} | {voltage:.4f} V {direction} | {view_label}",
                size="8pt")
            plot.setYRange(
                float(self.mzi_trace_y_min.value()),
                float(self.mzi_trace_y_max.value()), padding=0.0)
            self.mzi_trace_grid.addWidget(
                plot, index // columns, index % columns)
            self.mzi_sweep_trace_plots.append(plot)

        if selected_channel == 3:
            self.mzi_results_summary.setText(
                "ADC3 is the DAC3 loopback timing reference. It is not an "
                "optical response and is never averaged with ADC0-ADC2.")
        else:
            channel_result = channel_results.get(selected_channel, {})
            analyses = analyses_by_channel.get(selected_channel, [])
            values = np.asarray([
                analysis.filtered_mean_v for analysis in analyses])
            valid = bool(
                channel_result.get("optical_correlation_valid", False))
            marker_note = (
                "Gold x marks measured spike peaks."
                if valid else
                "No peak marks or optical curve are shown because this lane "
                "has no valid ADC3 correlation.")
            self.mzi_results_summary.setText(
                f"ADC{selected_channel} from "
                f"{channel_result.get('source', 'unknown')}: "
                f"{self._mzi_lane_timing_text(channel_result)}; "
                f"{len(analyses)} points, mean-peak span "
                f"{float(np.ptp(values)) * 1e3 if values.size else 0.0:.3f} mV. "
                f"{marker_note}")

    def _show_mzi_result_curve(self, result):
        plot = self.mzi_result_curve_plot
        plot.clear()
        legend = plot.plotItem.legend
        if legend is not None:
            legend.clear()
        voltage = np.asarray(result.get("voltages", []), dtype=np.float64)
        direction = np.asarray(result.get("directions", []), dtype=np.int8)
        selected_channel = self._selected_mzi_result_channel()
        constant = bool(result.get("constant_voltage_control"))
        use_filter = bool(self.mzi_outlier_filter.isChecked())
        if selected_channel == 3:
            plot.setLabel("left", "ADC3 timing reference")
            plot.setLabel("bottom", "heater", units="V")
            plot.setYRange(-0.05, 1.05, padding=0.0)
            return

        channels = (selected_channel,)
        visible_values = []
        for channel in channels:
            channel_result = self._mzi_channel_result(result, channel)
            if channel_result is None:
                continue
            if not channel_result.get("optical_correlation_valid", False):
                plot.setTitle(
                    f"ADC{channel}: no valid ADC3 correlation; "
                    "optical curve unavailable")
                continue
            plot.setTitle("")
            analyses = self._mzi_peak_analyses(result, channel)
            if not analyses:
                continue
            raw = np.asarray([
                analysis.raw_mean_v for analysis in analyses])
            filtered = np.asarray([
                analysis.filtered_mean_v for analysis in analyses])
            source = channel_result.get("source", "unknown")
            color = CH_COLORS[channel]
            if constant:
                x = np.arange(filtered.size)
                values = filtered * 1e3
                visible_values.append(values)
                plot.plot(
                    x, values, pen=pg.mkPen(color, width=1.6),
                    symbol="o", symbolSize=4, symbolBrush=color,
                    name=f"ADC{channel} {source}")
                if use_filter:
                    plot.plot(
                        x, raw * 1e3,
                        pen=pg.mkPen("#90A4AE", width=1.0,
                                     style=QtCore.Qt.DashLine),
                        symbol="o", symbolSize=3,
                        symbolBrush="#90A4AE", name="unfiltered")
            else:
                filtered_mv = filtered * 1e3
                raw_mv = raw * 1e3
                visible_values.append(filtered_mv)
                if use_filter:
                    visible_values.append(raw_mv)
                forward = direction == 0
                reverse = direction == 1
                for selected, suffix, style in (
                        (forward, "forward", QtCore.Qt.SolidLine),
                        (reverse, "reverse", QtCore.Qt.DashLine)):
                    if not np.any(selected):
                        continue
                    plot.plot(
                        voltage[selected], filtered_mv[selected],
                        pen=pg.mkPen(color, width=1.7, style=style),
                        symbol="o", symbolSize=4, symbolBrush=color,
                        name=f"ADC{channel} {source} {suffix}")
                    if use_filter:
                        plot.plot(
                            voltage[selected], raw_mv[selected],
                            pen=pg.mkPen("#90A4AE", width=1.0,
                                         style=QtCore.Qt.DotLine),
                            name=f"unfiltered {suffix}")

        shared_values = []
        for channel in range(3):
            channel_result = self._mzi_channel_result(result, channel)
            if (channel_result is None or
                    not channel_result.get("optical_correlation_valid", False)):
                continue
            analyses = self._mzi_peak_analyses(result, channel)
            if not analyses:
                continue
            selected = np.asarray([
                analysis.filtered_mean_v if use_filter else analysis.raw_mean_v
                for analysis in analyses], dtype=np.float64) * 1e3
            shared_values.append(selected)

        if constant:
            held = float(voltage[0]) if voltage.size else float("nan")
            plot.setLabel("left", "mean peak amplitude", units="mV")
            plot.setLabel(
                "bottom", f"capture index (heater held at {held:.4f} V)")
            if shared_values or visible_values:
                combined = np.concatenate(shared_values or visible_values)
                lower = float(np.min(combined))
                upper = float(np.max(combined))
                padding = max(0.05 * (upper - lower), 0.01)
                plot.setYRange(lower - padding, upper + padding, padding=0.0)
        else:
            plot.setLabel(
                "left", "mean detected spike peak", units="mV")
            plot.setLabel("bottom", "heater", units="V")
            if shared_values or visible_values:
                combined = np.concatenate(shared_values or visible_values)
                lower = float(np.min(combined))
                upper = float(np.max(combined))
                padding = max(0.05 * (upper - lower), 0.01)
                plot.setYRange(
                    lower - padding, upper + padding, padding=0.0)
    def _show_mzi_tone_sweep(self, result):
        self.mzi_curve_fwd.setData([], [])
        self.mzi_curve_rev.setData([], [])
        self._clear_mzi_tone_curves()
        voltages = np.asarray(result["voltages"], dtype=np.float64)
        directions = np.asarray(result["directions"], dtype=np.int8)
        self.mzi_plot.setLabel("left", "fitted tone amplitude", units="mV")
        self.mzi_plot.setLabel("bottom", "heater", units="V")
        self.mzi_plot.addLegend(offset=(8, 8))
        for channel in range(3):
            values = np.asarray(
                result["tone_amplitudes_v"][channel], dtype=np.float64) * 1e3
            for direction, suffix, style in (
                    (0, "forward", QtCore.Qt.SolidLine),
                    (1, "reverse", QtCore.Qt.DashLine)):
                selected = directions == direction
                if not np.any(selected):
                    continue
                pen = pg.mkPen(
                    CH_COLORS[channel], width=1.6, style=style)
                curve = self.mzi_plot.plot(
                    voltages[selected], values[selected], pen=pen,
                    symbol="o", symbolSize=5,
                    symbolBrush=CH_COLORS[channel],
                    name=f"ADC{channel} {suffix}")
                self._mzi_tone_curves.append(curve)
        self.mzi_plot.enableAutoRange(axis=pg.ViewBox.YAxis)

    def _on_mzi_cal_result(self, result):
        self._mzi_running = False
        enabled = bool(self.dac)
        self.mzi_program_btn.setEnabled(enabled)
        self.mzi_quick_btn.setEnabled(enabled)
        self.mzi_point_btn.setEnabled(enabled)
        self.mzi_run_btn.setEnabled(enabled)
        self.mzi_cancel_btn.setEnabled(False)
        for control in self._mzi_heater_controls:
            control.setEnabled(True)
        if self._mzi_resume_autosample and self.dac and self.stream_btn.isChecked():
            self._autosample_busy = False
            self.autosample_timer.start()
        self._mzi_resume_autosample = False
        if self._mzi_resume_tap and self.dac:
            self.dac.start_stream(self.args.decim, self.args.cic)
            try:
                self.tap = StreamTap(
                    self.args.board_ip, self.args.cmd_port, self.args.local_ip,
                    self.args.local_port, self.args.window, self.args.rcvbuf)
            except OSError:
                self.tap = None
        self._mzi_resume_tap = False
        if not isinstance(result, dict) or "_err" in result:
            error = result.get("_err", "no result") if isinstance(result, dict) else "no result"
            failed_kind = result.get("kind") if isinstance(result, dict) else None
            if failed_kind in ("pico_init", "pico_test"):
                self.mzi_pico_status.setText(f"Pico: FAIL - {error}")
                self.mzi_pico_status.setStyleSheet(
                    "color:#E57373; font-size:11px;")
            elif failed_kind == "heater_set":
                self.mzi_write_status.setText(f"Heater write failed: {error}")
                self.mzi_write_status.setStyleSheet(
                    "color:#E57373; font-size:11px;")
            self.mzi_status.setText(f"Optical test failed: {error}")
            return
        kind = result.get("kind")
        spec = result.get("spec", {})
        if kind == "pico_init":
            self.mzi_pico_status.setText(
                f"Pico: READY - PICO-002 initialized; {result['heater_count']} heaters mapped")
            self.mzi_pico_status.setStyleSheet(
                "color:#81C784; font-size:11px;")
            self.mzi_status.setText(
                "PyDAQ initialization passed. No heater voltages were changed.")
            return
        if kind == "pico_test":
            self.mzi_pico_status.setText(
                f"Pico: PASS - {result['probes']}/5 handshakes, "
                f"mean {result['mean_ms']:.1f} ms, max {result['max_ms']:.1f} ms")
            self.mzi_pico_status.setStyleSheet(
                "color:#81C784; font-size:11px;")
            self.mzi_status.setText(
                "PICO-002 connection passed without changing heater outputs.")
            return
        if kind == "heater_set":

            if (self._mzi_staged_heater_voltages is not None and
                    all(self._mzi_heater_voltages[net] is not None and
                        abs(self._mzi_heater_voltages[net] - staged) < 0.5e-6
                        for net, staged in
                        self._mzi_staged_heater_voltages.items())):
                name = self._mzi_active_config_name or "configuration"
                self._mzi_staged_heater_voltages = None
                self.mzi_config_status.setText(f"Applied {name}")
            self._refresh_mzi_heater_map()
            self.mzi_write_status.setText(
                f"PASS: PICO-002 acknowledged all {len(result['voltages'])} "
                "heater write(s).")
            self.mzi_write_status.setStyleSheet(
                "color:#81C784; font-size:11px;")
            self.mzi_status.setText(
                f"Programmed {len(result['voltages'])} heater output(s); "
                f"settled for {result.get('settle_s', 0.0) * 1000:.1f} ms.")
            return

        if spec.get("mode") == "tone" and kind in (
                "configured", "point", "sweep"):
            for channel, label in enumerate(spec["xbar_sources"]):
                self.src_cbs[channel].setCurrentText(label)
                self._applied_label[channel] = label
            self._refresh_xbar_preview()
            if kind == "configured":
                enabled_inputs = ", ".join(
                    f"DAC{channel}" for channel in spec["tone_inputs"])
                self.mzi_status.setText(
                    f"Pure-tone setup programmed: {enabled_inputs}; "
                    f"{spec['tone_actual_frequency_hz'] / 1000.0:.3f} kHz, "
                    "0 V offset, full bipolar range; DAC3/ADC3 reference.")
                return
            if kind == "point":
                self.mzi_progress.setValue(1)
                self._mzi_last_point_result = result
                self._show_mzi_point_diagnostics(result)
                lane_text = "; ".join(
                    f"ADC{channel} {values['amplitude_v'] * 1e3:.3f} mV, "
                    f"gain {values['gain_vs_reference']:.6f}, "
                    f"latency {values['latency_modulo_period_ns']:.3f} ns"
                    for channel, values in
                    sorted(result["channel_results"].items()))
                self.mzi_status.setText(
                    f"Captured and coherently averaged {spec['reps']} "
                    f"phase-aligned tone repetition(s) at "
                    f"{result['voltage']:.4f} V. {lane_text}. "
                    f"Saved to {result['path']}")
                return
            self._show_mzi_tone_sweep(result)
            points = len(result.get("tone_points", []))
            lane_text = "; ".join(
                f"ADC{channel} amplitude "
                f"{np.min(values) * 1e3:.3f}.."
                f"{np.max(values) * 1e3:.3f} mV"
                for channel, values in
                sorted(result["tone_amplitudes_v"].items())
                if channel < 3)
            self.mzi_status.setText(
                f"Pure-tone sweep complete: {points} heater point(s); "
                f"{lane_text}. Raw captures and tone_summary.npz saved in "
                f"{result['path']}")
            return

        if spec and kind in ("configured", "point", "sweep"):
            for channel in range(4):
                label = spec["xbar_sources"][channel]
                self.src_cbs[channel].setCurrentText(label)
                self._applied_label[channel] = label
            self._refresh_xbar_preview()
            current_samples, current_cps, actual_frequency = gen_current_wave(
                "Square", spec["current_ma"], spec["current_frequency_hz"],
                square_duty=spec["current_duty_percent"])
            self._set_current_preview(
                "Square", current_samples, current_cps, actual_frequency,
                programmed=True)

        if kind == "configured":
            profile_text = ", ".join(
                f"N{neuron}={spec['profiles'][neuron]}" for neuron in range(4))
            player = spec["current_player_readback"]
            self.mzi_status.setText(
                f"Setup programmed and current player verified RUNNING "
                f"(RW16=0x{player['raw']:08X}): {profile_text}; "
                "every i/iconst=0 mA; "
                f"{spec['current_ma']:.1f} mA, "
                f"{spec['current_actual_frequency_hz'] / 1000.0:.3f} kHz square; "
                "DAC0-DAC2 routes applied independently; DAC3/ADC3 is the "
                "loopback reference.")
            return
        if kind == "point":
            self.mzi_progress.setValue(1)

            self._mzi_last_point_result = result
            self._show_mzi_point_diagnostics(result)
            lane_timing = "; ".join(
                f"ADC{channel} {self._mzi_lane_timing_text(channel_result)}"
                for channel, channel_result in
                sorted(result.get("channel_results", {}).items()))
            self.mzi_status.setText(
                f"Captured {spec['reps']} hardware-triggered "
                f"{'sample (not averaged)' if spec['reps'] == 1 else 'samples and averaged each ADC independently'} at "
                f"{result['voltage']:.4f} V: {len(result['peaks'])} spikes, "
                f"mean {result['height'] * 1e3:.3f} mV; ADC3 first peak "
                f"{result['reference_latency_samples']} samples after guard; "
                f"{lane_timing}. Saved to {result['path']}")
            return
        voltage = np.asarray(result.get("voltages", []), dtype=float)
        direction = np.asarray(result.get("directions", []), dtype=np.int8)
        amplitude_mv = np.asarray(
            result.get("absolute", []), dtype=np.float64) * 1e3
        if result.get("constant_voltage_control"):
            self.mzi_plot.setLabel(
                "left", "mean detected spike peak", units="mV")
            self.mzi_plot.setLabel("bottom", "capture index")
            self.mzi_plot.enableAutoRange()
            self.mzi_curve_fwd.setData(np.arange(voltage.size), amplitude_mv)
            self.mzi_curve_rev.setData([], [])
        else:
            self.mzi_plot.setLabel(
                "left", "mean detected spike peak", units="mV")
            self.mzi_plot.setLabel("bottom", "heater", units="V")
            self.mzi_plot.enableAutoRange(axis=pg.ViewBox.YAxis)
            self.mzi_curve_fwd.setData(
                voltage[direction == 0], amplitude_mv[direction == 0])
            self.mzi_curve_rev.setData(
                voltage[direction == 1], amplitude_mv[direction == 1])
        if result.get("measurements"):
            self._remember_mzi_dataset(result)
        reference = result.get("reference_measurement")
        if reference is not None:
            self._show_mzi_spikes(
                reference.averaged_waveform, reference.peak_indices,
                reference.start_indices, reference.end_indices,
                reference.per_peak_height.mean(axis=0))
        state = "stopped" if result.get("cancelled") else "complete"
        if voltage.size and "min_height" in result:
            if result.get("constant_voltage_control"):
                detail = (
                    f"constant {voltage[0]:.4f} V control; amplitude mean "
                    f"{np.mean(result['absolute']) * 1e3:.3f} mV; "
                    f"peak-to-peak variation "
                    f"{result['repeatability_peak_to_peak_v'] * 1e3:.3f} mV")
            else:
                detail = (f"min {result['min_height'] * 1e3:.3f} mV at "
                          f"{result['min_voltage']:.4f} V; max "
                          f"{result['max_height'] * 1e3:.3f} mV; "
                          f"extinction {result['extinction_db']:.2f} dB")
        else:
            detail = "no completed points"

        timing = ""
        if result.get("loopback_reference_adc") is not None:
            lane_timing = []
            for channel, channel_result in sorted(
                    result.get("channel_results", {}).items()):
                lane_timing.append(
                    f"ADC{channel} "
                    f"{self._mzi_lane_timing_text(channel_result)}")
            timing = (
                f"; ADC3 reference peak "
                f"{result.get('reference_first_peak_latency_samples')} samples "
                f"after guard; lane latency: " + ", ".join(lane_timing))
        self.mzi_status.setText(
            f"Sweep {state}: {detail}{timing}. "
            f"Saved to {result.get('path', '(not saved)')}")

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

    def _multisample_once(self, nbytes, reps, command="BCPT"):
        """BCPT/BCPD + BRDO, sliced into raw hardware-aligned repetitions.
        Correlation offsets are reported as diagnostics but are never applied.
        Runs in a worker thread. Returns
        {'stack': {ch: float64[N,L]}, 'offs': [...], 'meta': {...}} or {'_err'}."""
        if command not in ("BCPT", "BCPD"):
            return {"_err": f"unsupported burst command {command!r}"}
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
            bcpt = self.dac.cmd(
                f"{command} {kb}k {reps}",
                ok=(f"OK {command}", "ERR"), timeout=180.0)
            if not bcpt:
                return {"_err": describe_burst_capture_failure(command, bcpt)}
            if not bcpt.startswith(f"OK {command}"):
                return {"_err": describe_burst_capture_failure(command, bcpt)}
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
                return {"_err": f"unparseable {command} reply: {bcpt!r}"}
            total = meta["total_per_chip"]
            try:
                asm = Reassembler(self.args.board_ip, self.args.cmd_port,
                                  self.args.local_ip, self.args.local_port, total)
            except OSError as exc:
                return {"_err": (f"UDP bind failed on {self.args.local_ip}:"
                                 f"{self.args.local_port}: {exc}")}
            # Drain with retry: the UDP readout occasionally drops a packet
            # (historically chip 1, same quirk _burst_collect retries around).
            # The capture itself is intact in DDR -- BCAP/BCPT and BRDO are
            # decoupled -- so a fresh BRST registration + BRDO re-drains the
            # SAME repetitions without re-triggering anything.
            drain_tries = 3
            # Every BRDO below rereads the same immutable DDR image. Preserve
            # each request's received slots so packet loss from separate drains
            # can be filled in rather than clearing all prior progress.
            combined_buf = [bytearray(total), bytearray(total)]
            combined_cov = [
                np.zeros(asm.nslot, dtype=bool),
                np.zeros(asm.nslot, dtype=bool),
            ]
            for attempt in range(drain_tries):
                if attempt:
                    time.sleep(0.4)   # human-paced settle; fast re-issue races the A53
                asm.begin_request()
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
                with asm.lock:
                    request_cov = [asm.cov[0].copy(), asm.cov[1].copy()]
                    request_buf = [bytes(asm.buf[0]), bytes(asm.buf[1])]
                for chip in range(2):
                    for slot in np.flatnonzero(request_cov[chip]):
                        start = int(slot) * asm.slot
                        end = min(total, start + asm.slot)
                        combined_buf[chip][start:end] = (
                            request_buf[chip][start:end])
                    combined_cov[chip] |= request_cov[chip]
                if combined_cov[0].all() and combined_cov[1].all():
                    break
            coverage = [float(bits.mean()) for bits in combined_cov]
            cov = min(coverage)
            if not (combined_cov[0].all() and combined_cov[1].all()):
                return {"_err": (f"UDP drain incomplete after {drain_tries} "
                                 f"attempts: chip0 {100 * coverage[0]:.1f}%, "
                                 f"chip1 {100 * coverage[1]:.1f}% combined coverage")}
            chans = {}
            chans.update(decode_chip(combined_buf[0], 0))
            chans.update(decode_chip(combined_buf[1], 2))

            # slice the strided DDR layout: per channel 4 bytes/sample
            spr = meta["bytes_per_rep"] // 4      # wanted samples per rep
            sps = meta["stride"] // 4             # rep-to-rep stride in samples
            n = meta["reps"]
            stack = {ch: np.stack([chans[ch][r * sps: r * sps + spr]
                                   for r in range(n)]).astype(np.float64)
                     for ch in range(4)}

            # Do not realign in software. A featureless zero-current ADC0 used
            # to produce arbitrary lags here and np.roll would move valid ADC1
            # neuron spikes. Pick the strongest repeatable channel solely for
            # reporting whether the hardware trigger remained at sample zero.
            diag = trigger_offset_diagnostics(stack)
            offs = diag["offsets"]
            meta["cov"] = cov
            meta["drain_attempts"] = attempt + 1
            return {"stack": stack, "offs": offs, "diag": diag, "meta": meta}
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
        diag = res.get("diag", {"anchor": None, "observable": False, "score": 0.0})
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
            self._apply_time_plot_range(p, ch)
            if draw_reps:
                for i in range(n):
                    col = pg.mkColor(CH_COLORS[ch]); col.setAlpha(45)
                    p.plot(t, stack[ch][i] * VOLTS_PER_COUNT,
                           pen=pg.mkPen(col, width=0.6))
            p.plot(t, avg[ch] * VOLTS_PER_COUNT,
                   pen=pg.mkPen("#ffffff", width=1.4))
        omin, omax = min(offs), max(offs)
        diag_txt = (f"diagnostic ADC{diag['anchor']} offsets {omin}..{omax} samples"
                    if diag.get("observable") else
                    "offset diagnostic unavailable (no repeatable timing feature)")
        win.addLabel(f"N={n} hardware-triggered Ethernet bursts averaged at raw "
                     f"sample indices (no software shift; x = ns @ 1 GS/s; "
                     f"{diag_txt}; "
                     f"UDP coverage {100 * meta.get('cov', 1.0):.1f}%)",
                     row=4, col=0)
        win.show()
        self._msamp_popup = win
        caps = [{ch: stack[ch][i].astype(np.int16) for ch in range(4)}
                for i in range(n)]
        sub = self._save_burst(caps, avg, offs, diag)
        where = f"  -> {sub}" if sub else "  (save FAILED)"
        health = (f"diagnostic ADC{diag['anchor']} lag {omin}..{omax}"
                  if diag.get("observable") else "lag not observable")
        self.status.setText(
            f"Multisample: N={n} x {L} samp/ch, raw hardware alignment, "
            f"{health}.{where}")

    # ---- live triggered averaging: continuous BCPT batches -> rolling mean --
    def _on_liveavg_toggle(self, checked):
        if not self.dac:
            self.liveavg_btn.setChecked(False)
            return
        if checked:
            self._resume_autosample = self.autosample_timer.isActive()
            if self._resume_autosample:
                self.autosample_timer.stop()
            self._liveavg_resume_tap = self.tap is not None
            if self.tap:
                self.tap.close()
                self.tap = None
            nbytes, lbl = COLLECT_SIZE_OPTIONS[self.collect_mb_cb.currentIndex()]
            self._liveavg_bytes = nbytes
            self._liveavg_window = self.liveavg_window.value()
            self._liveavg_stacks = {
                ch: deque(maxlen=self._liveavg_window) for ch in range(4)}
            self._liveavg_total = 0
            self._liveavg_errors = 0
            self._liveavg_busy = False
            self._liveavg_started = time.perf_counter()
            self._liveavg_render_count = 0
            self._liveavg_last_snapshot = None
            self._liveavg_build_window()
            self.liveavg_btn.setText("Stop Live Trig Avg")
            for w in (self.msamp_btn, self.collect_btn, self.stream_btn,
                      self.burst_btn):
                w.setEnabled(False)
            self.status.setText(
                f"Live Trig Avg: rolling window of {self._liveavg_window}, "
                f"{lbl} per rep...")
            self.liveavg_window.setEnabled(False)
            self.liveavg_timer.start()
            self._on_liveavg_tick()
        else:
            self._stop_liveavg()

    def _stop_liveavg(self):
        self.liveavg_timer.stop()
        self.liveavg_btn.setChecked(False)
        self.liveavg_btn.setText("Start Live Trig Avg")
        self.liveavg_window.setEnabled(True)
        if getattr(self, "_controls_enabled", False):
            for w in (self.msamp_btn, self.collect_btn, self.stream_btn,
                      self.burst_btn):
                w.setEnabled(True)
        if (getattr(self, "_resume_autosample", False)
                and self.dac and self.stream_btn.isChecked()):
            self._autosample_busy = False
            self.autosample_timer.start()
        self._resume_autosample = False
        if getattr(self, "_liveavg_resume_tap", False) and self.dac:
            self.dac.start_stream(self.args.decim, self.args.cic)
            try:
                self.tap = StreamTap(self.args.board_ip, self.args.cmd_port,
                                     self.args.local_ip, self.args.local_port,
                                     self.args.window, self.args.rcvbuf)
            except OSError:
                self.tap = None
        self._liveavg_resume_tap = False

    def _liveavg_build_window(self):
        win = pg.GraphicsLayoutWidget()
        win.setWindowTitle("Live triggered average")
        win.setBackground("#101418")
        win.resize(900, 720)
        self._liveavg_plots = []
        self._liveavg_ghosts = [[] for _ in range(4)]
        self._liveavg_means = []
        for ch in range(4):
            p = win.addPlot(row=ch, col=0)
            p.showGrid(x=True, y=True, alpha=0.25)
            p.setLabel("left", f"ch{ch}", units="V")
            if ch > 0:
                p.setXLink(self._liveavg_plots[0])
            if ch < 3:
                p.getAxis("bottom").setStyle(showValues=False)
            else:
                p.setLabel("bottom", "sample", units="ns")
            p.enableAutoRange("y", False)
            p.setYRange(*self.liveavg_y_ranges[ch], padding=0)
            self._liveavg_plots.append(p)
            mean_curve = p.plot([], [], pen=pg.mkPen("#ffffff", width=1.5))
            mean_curve.setZValue(10)
            self._liveavg_means.append(mean_curve)
        self._liveavg_label = win.addLabel("waiting for the first batch...",
                                           row=4, col=0)
        win.show()
        self._liveavg_win = win
        self._apply_liveavg_axes()

    def _ensure_liveavg_ghosts(self):
        for ch, curves in enumerate(self._liveavg_ghosts):
            if curves:
                continue
            col = pg.mkColor(CH_COLORS[ch])
            col.setAlpha(40)
            self._liveavg_ghosts[ch] = [
                self._liveavg_plots[ch].plot(
                    [], [], pen=pg.mkPen(col, width=0.6))
                for _ in range(self._liveavg_window)]
            for curve in self._liveavg_ghosts[ch]:
                curve.setZValue(0)

    def _on_liveavg_display_options(self, *_):
        if not hasattr(self, "_liveavg_ghosts"):
            return
        show_ghosts = self.liveavg_ghosts_chk.isChecked()
        if show_ghosts:
            self._ensure_liveavg_ghosts()
        for curves in self._liveavg_ghosts:
            for curve in curves:
                curve.setVisible(show_ghosts)
        if self._liveavg_last_snapshot is not None:
            self._render_liveavg(self._liveavg_last_snapshot)

    def _on_liveavg_axes(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Live-average axes")
        layout = QtWidgets.QVBoxLayout(dlg)
        note = QtWidgets.QLabel(
            "Auto Y uses PyQtGraph's original visible-trace scaling. The mean "
            "and any visible ghost captures all participate. Select two or "
            "more Link Y boxes to keep those axes locked during zoom/pan.")
        note.setWordWrap(True)
        layout.addWidget(note)

        grid = QtWidgets.QGridLayout()
        for col, text in enumerate(
                ("Channel", "Auto visible", "Y minimum", "Y maximum",
                 "Link Y")):
            grid.addWidget(QtWidgets.QLabel(text), 0, col)
        editors = []
        for ch in range(4):
            auto = QtWidgets.QCheckBox()
            auto.setChecked(self.liveavg_auto_y[ch])
            lo = QtWidgets.QDoubleSpinBox()
            hi = QtWidgets.QDoubleSpinBox()
            for box, value in ((lo, self.liveavg_y_ranges[ch][0]),
                               (hi, self.liveavg_y_ranges[ch][1])):
                box.setRange(-100.0, 100.0)
                box.setDecimals(5)
                box.setSingleStep(0.01)
                box.setSuffix(" V")
                box.setValue(value)
                box.setEnabled(not auto.isChecked())
            auto.toggled.connect(
                lambda checked, boxes=(lo, hi):
                    [box.setEnabled(not checked) for box in boxes])
            link = QtWidgets.QCheckBox()
            link.setChecked(self.liveavg_y_link[ch])
            grid.addWidget(QtWidgets.QLabel(f"ADC{ch}"), ch + 1, 0)
            grid.addWidget(auto, ch + 1, 1)
            grid.addWidget(lo, ch + 1, 2)
            grid.addWidget(hi, ch + 1, 3)
            grid.addWidget(link, ch + 1, 4)
            editors.append((auto, lo, hi, link))
        layout.addLayout(grid)

        x_box = QtWidgets.QGroupBox("Shared X axis (all four live waveforms)")
        x_grid = QtWidgets.QGridLayout(x_box)
        x_auto = QtWidgets.QCheckBox("Auto fit current average length")
        x_auto.setChecked(self.liveavg_x_range is None)
        default_x = self.liveavg_x_range or (0.0, float(self.args.time_span))
        x_lo = QtWidgets.QDoubleSpinBox()
        x_hi = QtWidgets.QDoubleSpinBox()
        for box, value in zip((x_lo, x_hi), default_x):
            box.setRange(0.0, 1.0e9)
            box.setDecimals(1)
            box.setSuffix(" samples")
            box.setValue(value)
            box.setEnabled(not x_auto.isChecked())
        x_auto.toggled.connect(
            lambda checked:
                [box.setEnabled(not checked) for box in (x_lo, x_hi)])
        x_grid.addWidget(x_auto, 0, 0, 1, 2)
        x_grid.addWidget(QtWidgets.QLabel("Minimum"), 1, 0)
        x_grid.addWidget(x_lo, 1, 1)
        x_grid.addWidget(QtWidgets.QLabel("Maximum"), 2, 0)
        x_grid.addWidget(x_hi, 2, 1)
        layout.addWidget(x_box)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        ranges = [(lo.value(), hi.value()) for _auto, lo, hi, _link
                  in editors]
        if any(lo >= hi for lo, hi in ranges):
            QtWidgets.QMessageBox.warning(
                self, "Invalid Y range",
                "Every manual Y minimum must be below its maximum.")
            return
        if not x_auto.isChecked() and x_lo.value() >= x_hi.value():
            QtWidgets.QMessageBox.warning(
                self, "Invalid X range",
                "The shared X minimum must be below its maximum.")
            return
        self.liveavg_auto_y = [auto.isChecked()
                               for auto, _lo, _hi, _link in editors]
        self.liveavg_y_ranges = ranges
        self.liveavg_y_link = [link.isChecked()
                               for _auto, _lo, _hi, link in editors]
        self.liveavg_x_range = (
            None if x_auto.isChecked() else (x_lo.value(), x_hi.value()))
        self._apply_liveavg_axes()

    def _render_liveavg(self, snapshot):
        if not snapshot or not snapshot.get("held"):
            return 0
        show_ghosts = self.liveavg_ghosts_chk.isChecked()
        if show_ghosts:
            self._ensure_liveavg_ghosts()
        held = snapshot["held"]
        mean_traces = snapshot.get("mean_traces", {})
        ghost_traces = snapshot.get("ghost_traces", {})
        for ch in range(4):
            if show_ghosts:
                traces = ghost_traces.get(ch, [])
                for i, curve in enumerate(self._liveavg_ghosts[ch]):
                    if i < len(traces):
                        ghost_x, ghost_y = traces[i]
                        curve.setData(
                            ghost_x, ghost_y * VOLTS_PER_COUNT,
                            skipFiniteCheck=True)
                    else:
                        curve.setData([], [])
            trace = mean_traces.get(ch)
            if trace is not None:
                mean_x, mean_y = trace
                self._liveavg_means[ch].setData(
                    mean_x, mean_y * VOLTS_PER_COUNT,
                    skipFiniteCheck=True)
        self._apply_liveavg_axes()
        return held

    def _apply_liveavg_axes(self):
        """Apply live-average axes using every visible plot trace."""
        plots = getattr(self, "_liveavg_plots", None)
        if not plots:
            return
        linked = [ch for ch, enabled in enumerate(self.liveavg_y_link) if enabled]
        for ch, plot in enumerate(plots):
            plot.setYLink(None)
            if self.liveavg_auto_y[ch]:
                plot.enableAutoRange("y", True)
            else:
                plot.enableAutoRange("y", False)
                plot.setYRange(*self.liveavg_y_ranges[ch], padding=0)
        if len(linked) >= 2:
            anchor = plots[linked[0]]
            for ch in linked[1:]:
                plots[ch].setYLink(anchor)
        if self.liveavg_x_range is None:
            plots[0].enableAutoRange("x", True)
        else:
            plots[0].enableAutoRange("x", False)
            plots[0].setXRange(*self.liveavg_x_range, padding=0)

    def _on_liveavg_tick(self):
        if self._liveavg_busy or not self.dac:
            return
        if self._liveavg_win is None or not self._liveavg_win.isVisible():
            self._stop_liveavg()
            return
        self._liveavg_busy = True
        reps = max(2, min(4, self._liveavg_window))
        nbytes = self._liveavg_bytes
        self._bg(lambda: self.liveavg_result.emit(
            self._multisample_once(nbytes, reps)))

    def _on_liveavg_batch(self, res):
        self._liveavg_busy = False
        if not self.liveavg_btn.isChecked():
            return
        if not isinstance(res, dict) or "stack" not in res:
            self._liveavg_errors += 1
            message = (
                res.get("_err", "no data")
                if isinstance(res, dict) else "invalid capture result")
            self.status.setText(
                f"Live Trig Avg: transient batch failure "
                f"({self._liveavg_errors} consecutive), retrying: {message}")
            return

        self._liveavg_errors = 0
        stack = res["stack"]
        n_new = stack[0].shape[0]
        for ch in range(4):
            for i in range(n_new):
                self._liveavg_stacks[ch].append(
                    np.asarray(stack[ch][i], dtype=np.int16).copy())
        self._liveavg_total += n_new

        held = len(self._liveavg_stacks[0])
        use_downsample = self.liveavg_downsample_chk.isChecked()
        include_ghosts = self.liveavg_ghosts_chk.isChecked()
        mean_traces = {}
        ghost_traces = {}
        for ch in range(4):
            reps = list(self._liveavg_stacks[ch])
            mean = np.mean(np.stack(reps), axis=0)
            if use_downsample:
                mean_traces[ch] = peak_envelope(mean)
            else:
                mean_traces[ch] = (
                    np.arange(mean.size, dtype=np.int64), mean)
            if include_ghosts:
                ghost_traces[ch] = [
                    peak_envelope(rep) if use_downsample else (
                        np.arange(rep.size, dtype=np.int64), rep)
                    for rep in reps]

        elapsed = max(
            1.0e-9, time.perf_counter() - self._liveavg_started)
        snapshot = {
            "kind": "data",
            "held": held,
            "mean_traces": mean_traces,
            "ghost_traces": ghost_traces,
            "total": self._liveavg_total,
            "capture_rate": self._liveavg_total / elapsed,
            "downsample": use_downsample,
            "ghosts": include_ghosts,
        }
        self._liveavg_last_snapshot = snapshot
        rendered = self._render_liveavg(snapshot)
        if rendered:
            self._liveavg_render_count += 1
            display_rate = self._liveavg_render_count / elapsed
            self._liveavg_label.setText(
                f"running mean of the last {rendered} triggered captures "
                f"(window {self._liveavg_window}) -- "
                f"{self._liveavg_total} total -- capture "
                f"{snapshot['capture_rate']:.1f}/s, display "
                f"{display_rate:.1f} FPS (x = ns @ 1 GS/s)")

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
                # Demo setup explicitly changed these two routes and received
                # successful NSRC replies, so keep the live XBAR display honest.
                self._set_applied_route(0, "Current source")
                self._set_applied_route(1, "Spike 0")
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
        """Draw a captured {ch: int16[]} set into the four main plots."""
        if span is None:
            span = self.args.time_span
        fulls = {ch: self._deint(chans[ch].astype(np.float64))
                 for ch in range(4)}
        time_slices = None if self.fft_view else self._aligned_time_slices(fulls, span)
        for ch in range(4):
            full = fulls[ch]
            if self.fft_view:
                v = full[-span:] * VOLTS_PER_COUNT
                v = v - v.mean()
                w = np.hanning(len(v))
                Y = np.abs(np.fft.rfft(v * w)) / (np.sum(w) / 2.0)
                f = np.fft.rfftfreq(len(v), 1.0 / fs)
                db = 20.0 * np.log10(np.maximum(Y, 1e-9))
                self.curves[ch].setData(f, db)

            else:
                y = time_slices[ch]
                t = np.arange(len(y)) / fs
                v = y * VOLTS_PER_COUNT
                self.curves[ch].setData(t, v)


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
            self._apply_time_plot_range(p, ch)
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
        ranges = self.fft_y_ranges if self.fft_view else self.time_y_ranges
        x_range = self.fft_x_range if self.fft_view else self.time_x_range
        for ch, p in enumerate(self.plots):
            p.setYLink(None)
            p.enableAutoRange("y", False)
            if self.fft_view:
                p.setLabel("left", f"ch{ch}", units="dB")
                p.setLabel("bottom", "frequency", units="Hz")
            else:
                p.setLabel("left", f"ch{ch}", units="V")
                if ch == 3:
                    p.setLabel("bottom", "time", units="s")
            p.setYRange(*ranges[ch], padding=0)
        linked = [ch for ch, enabled in enumerate(self.main_y_link) if enabled]
        if len(linked) >= 2:
            anchor = self.plots[linked[0]]
            for ch in linked[1:]:
                self.plots[ch].setYLink(anchor)
        if x_range is None:
            self.plots[0].enableAutoRange("x", True)
        else:
            self.plots[0].enableAutoRange("x", False)
            self.plots[0].setXRange(*x_range, padding=0)

    def _apply_time_plot_range(self, plot, ch):
        plot.enableAutoRange("y", False)
        plot.setYRange(*self.time_y_ranges[ch], padding=0)

    @staticmethod
    def _fitted_y_range(values, min_span=0.04, padding=0.10):
        """Return a padded finite-data range suitable for one fixed Y axis."""
        if values is None:
            return None
        finite = np.asarray(values)[np.isfinite(values)]
        if finite.size == 0:
            return None
        lo = float(finite.min())
        hi = float(finite.max())
        center = 0.5 * (lo + hi)
        span = max(float(min_span), hi - lo)
        pad = max(0.0, float(padding)) * span
        return (center - 0.5 * span - pad, center + 0.5 * span + pad)

    def _trig_start(self, yfull, span):
        """Return one trigger start index; callers reuse it for every channel."""
        n = len(yfull)
        if n < span + 2:
            return max(0, n - span)
        thr = yfull.mean()
        if yfull.std() < 8.0:
            return max(0, n - span)
        lo = max(1, n - 2 * span)
        hi = n - span
        a, b = yfull[lo - 1:hi - 1], yfull[lo:hi]
        cross = np.where((a < thr) & (b >= thr))[0]
        if len(cross) == 0:
            return max(0, n - span)
        return lo + int(cross[-1])

    def _aligned_time_slices(self, fulls, span):
        """Slice every channel at one shared sample origin.

        The channel with the largest recent peak-to-peak swing supplies the
        trigger because an open/quiet input is a poor reference.  Crucially,
        its start index is then applied unchanged to all four channels, keeping
        real inter-channel latency visible instead of triggering each trace
        independently and accidentally erasing it.
        """
        common_n = min(len(fulls[ch]) for ch in range(4))
        width = min(int(span), common_n)
        if width <= 0:
            return {ch: fulls[ch][:0] for ch in range(4)}
        if self.trigger:
            tail = min(common_n, max(width + 2, 2 * width))
            scores = {
                ch: float(np.ptp(fulls[ch][common_n - tail:common_n]))
                for ch in range(4)
            }
            ref = max(scores, key=scores.get)
            start = self._trig_start(fulls[ref][:common_n], width)
        else:
            start = common_n - width
        start = max(0, min(int(start), common_n - width))
        return {ch: fulls[ch][start:start + width] for ch in range(4)}

    def _trig_slice(self, yfull, span):
        """Legacy single-waveform helper; main plots use aligned slices."""
        start = self._trig_start(yfull, span)
        return yfull[start:start + span]

    def _update(self):
        if self.paused or self.tap is None:
            return
        snap = self.tap.snapshot()
        decim = max(1, self.tap.decim)
        fs = 1.0e9 / decim
        span = self.args.time_span
        fulls = {ch: snap[ch].astype(np.float64) for ch in range(4)}
        time_slices = None if self.fft_view else self._aligned_time_slices(fulls, span)
        for ch in range(4):
            full = fulls[ch]
            if self.fft_view:
                v = full[-span:] * VOLTS_PER_COUNT
                v = v - v.mean()
                w = np.hanning(len(v))
                Y = np.abs(np.fft.rfft(v * w)) / (np.sum(w) / 2.0)
                f = np.fft.rfftfreq(len(v), 1.0 / fs)
                db = 20.0 * np.log10(np.maximum(Y, 1e-9))
                self.curves[ch].setData(f, db)

            else:
                y = time_slices[ch]
                t = np.arange(len(y)) / fs
                v = y * VOLTS_PER_COUNT
                self.curves[ch].setData(t, v)

        self.status.setText(
            f"decim={decim}  {1000.0/decim:.2f} MS/s/ch  "
            f"Nyq {500.0/decim:.3f} MHz | pkts={self.tap.packets} "
            f"drops={self.tap.drops} | ch2/3={'CIC' if self.cic_chk.isChecked() else 'keep'}"
        )

    def closeEvent(self, ev):
        self.timer.stop()
        self.autosample_timer.stop()
        self.liveavg_timer.stop()
        for w in (self._cur_win, self._pulse_win, self._mzi_neuron_win,
                  self._liveavg_win):
            if w is not None:
                w.close()
        self._mzi_cancel.set()
        if self._mzi_controller.connected:
            try:
                self._mzi_controller.close()
            except Exception:
                pass
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
    ap.add_argument(
        "--initial", default="DDS", choices=SOURCE_LABELS,
        help="offline staged XBar selection before connect; live reg17 routes "
             "replace it on connect and are never changed automatically")
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
