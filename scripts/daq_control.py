#!/usr/bin/env python3
"""Shared board-control helpers for DAQ_LAUNCH host tools.

This module is intentionally small and boring: it wraps the stable board actions
that agents and humans keep needing, without hiding the underlying UART/SSH
commands.  The CLI and MCP server both import this file.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PORT = "COM10"
DEFAULT_BAUD = 115200
DEFAULT_UART_TIMEOUT = 4.0

DEFAULT_REMOTE_HOST = "capitolpeak.ece.ucdavis.edu"
DEFAULT_REMOTE_USER = "jkincaid"
DEFAULT_REMOTE_REPO = "/home/jkincaid/DAQControl"
DEFAULT_BRANCH = "merge-stream-neuron"
DEFAULT_XILINX_WRAPPER = "/home/jkincaid/bin/with_xilinx_2024_1"

# Ethernet data plane (A53 PS-eth): one-shot burst capture + UDP readout.
DEFAULT_BOARD_IP = "192.168.2.10"
DEFAULT_CMD_PORT = 5006
DEFAULT_LOCAL_IP = "192.168.2.1"
DEFAULT_LOCAL_PORT = 5005
DEFAULT_CAPTURE_DIR = REPO_ROOT / "captures"

VOLTS_PER_COUNT = 1.9 / 65536.0
ADC_SAMPLE_RATE_HZ = 1.0e9          # full-rate ADC / DAC sample clock
DDS_SAMPLE_RATE_HZ = 1.0e9
DDS_PHASE_BITS = 24                  # freq = inc / 2**24 * 1 GS/s
CURRENT_PLAYER_CLK_HZ = 50.0e6       # cur_wave player advances in clk_50
CURRENT_WAVE_DEPTH = 1024
CURRENT_MA_TO_Q16 = 65536.0          # 1 mA == 1.0 Izhikevich I unit
CURRENT_Q16_POS_MAX = 0x7FFFFFFF
CURRENT_MAX_MA = CURRENT_Q16_POS_MAX / CURRENT_MA_TO_Q16
CURRENT_GAIN_Q8_8_ONE = 0x0100

# Firmware NEUR surface: built-in profiles + the two param classes. Physical
# params are converted to Q16.16; dt/period/reset pass through as raw integers.
NEURON_PROFILES = ("regular", "bursting", "chattering", "fast")
NEURON_PHYS_PARAMS = ("a", "b", "c", "d", "i", "iconst")
NEURON_RAW_PARAMS = ("dt", "period", "reset")

GENERATED_REMOTE_PATHS = (
    "ip_repo/AXI4_register_file_1_0/component.xml",
    "project/DAQ_LAUNCH.runs/impl_1/top.bit",
    "reports/ip_status_after_create.rpt",
    "hw/DAQ_LAUNCH.ltx",
)

DAC_SOURCES: Dict[str, int] = {
    "off": 0,
    "dds": 1,
    "bram0": 2,
    "bram1": 3,
    "bram2": 4,
    "bram3": 5,
    "spike0": 6,
    "spike1": 7,
    "spike2": 8,
    "spike3": 9,
    "monitor0": 10,
    "monitor1": 11,
    "monitor2": 12,
    "monitor3": 13,
    "tag": 14,
    "current": 15,
}

SOURCE_ALIASES = {
    "bram": "bram0",
    "cur": "current",
    "current_source": "current",
    "mon0": "monitor0",
    "mon1": "monitor1",
    "mon2": "monitor2",
    "mon3": "monitor3",
}

CODE_TO_SOURCE = {code: name for name, code in DAC_SOURCES.items()}


class DaqControlError(RuntimeError):
    """Raised when a board-control operation cannot be completed."""


@dataclass(frozen=True)
class RemoteConfig:
    host: str = DEFAULT_REMOTE_HOST
    user: str = DEFAULT_REMOTE_USER
    repo: str = DEFAULT_REMOTE_REPO
    branch: str = DEFAULT_BRANCH
    ssh_key: Optional[Path] = None
    xilinx_wrapper: str = DEFAULT_XILINX_WRAPPER

    @property
    def login(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def key_path(self) -> Path:
        if self.ssh_key is not None:
            return self.ssh_key
        return Path.home() / ".ssh" / "capitolpeak_auto"


def normalize_source(source: str) -> str:
    key = source.strip().lower().replace(" ", "_").replace("-", "_")
    key = SOURCE_ALIASES.get(key, key)
    if key not in DAC_SOURCES:
        valid = ", ".join(sorted(DAC_SOURCES))
        raise DaqControlError(f"unknown DAC source {source!r}; valid sources: {valid}")
    return key


def decode_dac_xbar(value: int) -> Dict[str, str]:
    decoded: Dict[str, str] = {}
    for ch in range(4):
        code = (value >> (4 * ch)) & 0xF
        decoded[f"dac{ch}"] = CODE_TO_SOURCE.get(code, f"unknown{code}")
    return decoded


def parse_status(text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"raw": text}
    match = re.search(r"dac_xbar=0x([0-9a-fA-F]+)", text)
    if match:
        value = int(match.group(1), 16)
        result["dac_xbar"] = f"0x{value:08X}"
        result["dac_routes"] = decode_dac_xbar(value)
    for name in ("RW0", "RW1", "RW2", "RW3", "RW4", "RW5", "RW6", "RW7"):
        match = re.search(rf"{name}\s*=\s*0x([0-9a-fA-F]{{8}})", text)
        if match:
            result[name.lower()] = f"0x{int(match.group(1), 16):08X}"
    return result


def _load_serial_module():
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise DaqControlError("pyserial is required; run `pip install -r requirements.txt`") from exc
    return serial


def list_uart_ports() -> List[Dict[str, str]]:
    serial = _load_serial_module()
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError:
        return []
    ports: List[Dict[str, str]] = []
    for port in list_ports.comports():
        ports.append(
            {
                "device": str(port.device),
                "description": str(port.description),
                "hwid": str(port.hwid),
            }
        )
    return ports


def _read_until(
    port: Any,
    expected_prefixes: Sequence[str],
    timeout: float,
) -> List[str]:
    lines: List[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = port.readline()
        if not line:
            continue
        text = line.decode("ascii", errors="replace").strip()
        if not text:
            continue
        lines.append(text)
        if text.startswith("ERR"):
            break
        if expected_prefixes and any(text.startswith(prefix) for prefix in expected_prefixes):
            break
    return lines


def send_uart_commands(
    commands: Sequence[str],
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
    expected: Optional[Mapping[str, Sequence[str]]] = None,
) -> List[Dict[str, Any]]:
    """Send line-oriented firmware commands and return decoded line responses."""

    serial = _load_serial_module()
    results: List[Dict[str, Any]] = []
    with serial.Serial(port_name, baud, timeout=0.2, write_timeout=timeout) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        for command in commands:
            prefixes = tuple((expected or {}).get(command, ()))
            port.write((command + "\n").encode("ascii"))
            port.flush()
            lines = _read_until(port, prefixes, timeout)
            error_lines = [line for line in lines if line.startswith("ERR")]
            if prefixes and not any(line.startswith(prefixes) for line in lines) and not error_lines:
                raise DaqControlError(
                    f"timed out waiting for {prefixes!r} after {command!r}; got {lines!r}"
                )
            if error_lines:
                raise DaqControlError(f"board returned {error_lines[-1]!r} after {command!r}")
            results.append({"command": command, "lines": lines})
    return results


def set_dac_routes(
    routes: Mapping[int, str],
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    commands: List[str] = []
    expected: Dict[str, Tuple[str, ...]] = {}
    normalized: Dict[str, str] = {}
    for channel, source in sorted(routes.items()):
        if channel < 0 or channel > 3:
            raise DaqControlError(f"DAC channel must be 0..3, got {channel}")
        source_name = normalize_source(source)
        command = f"NSRC {channel} {source_name}"
        commands.append(command)
        expected[command] = ("DAC xbar",)
        normalized[f"dac{channel}"] = source_name

    status_command = "STAT"
    commands.append(status_command)
    expected[status_command] = ("UART:",)
    responses = send_uart_commands(commands, port_name, baud, timeout, expected)
    status_text = "\n".join(line for item in responses for line in item["lines"])
    status = parse_status(status_text)
    return {"requested": normalized, "responses": responses, "status": status}


def read_status(
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    responses = send_uart_commands(
        ["STAT"],
        port_name=port_name,
        baud=baud,
        timeout=timeout,
        expected={"STAT": ("UART:",)},
    )
    text = "\n".join(line for item in responses for line in item["lines"])
    status = parse_status(text)
    status["responses"] = responses
    return status


def _quote_for_remote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(REPO_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def program_board_via_capitolpeak(
    config: Optional[RemoteConfig] = None,
    timeout: float = 600.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    cfg = config or RemoteConfig()
    generated = " ".join(_quote_for_remote(path) for path in GENERATED_REMOTE_PATHS)
    remote = "\n".join(
        [
            "set -e",
            f"cd {_quote_for_remote(cfg.repo)}",
            f"dirty=$(git status --short -- {generated})",
            'if [ -n "$dirty" ]; then',
            f"  git stash push -m pre-program-generated-artifacts -- {generated}",
            "fi",
            f"git pull --ff-only origin {_quote_for_remote(cfg.branch)}",
            f"{_quote_for_remote(cfg.xilinx_wrapper)} xsct program_board.tcl",
        ]
    )
    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        str(cfg.key_path),
        cfg.login,
        remote,
    ]
    if dry_run:
        return {"argv": argv, "remote_script": remote}
    completed = _run(argv, timeout)
    ok = completed.returncode == 0 and "== DONE:" in completed.stdout
    result = {
        "ok": ok,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
    }
    if not ok:
        raise DaqControlError("capitolpeak program_board failed:\n" + completed.stdout)
    return result


# --------------------------------------------------------------- DDS frequency
def izh_to_q16(value: float) -> int:
    """Physical Izhikevich value -> signed Q16.16 packed as a 32-bit word."""
    return int(round(float(value) * 65536.0)) & 0xFFFFFFFF


def dds_freq_to_inc(freq_hz: float) -> int:
    """DDS frequency (Hz) -> 24-bit phase increment, clamped to 0..0xFFFFFF."""
    inc = int(round(float(freq_hz) / DDS_SAMPLE_RATE_HZ * (1 << DDS_PHASE_BITS)))
    return max(0, min(0x00FFFFFF, inc))


def dds_inc_to_freq(inc: int) -> float:
    return (int(inc) & 0x00FFFFFF) / float(1 << DDS_PHASE_BITS) * DDS_SAMPLE_RATE_HZ


def set_dds(
    freq_hz: Optional[float] = None,
    inc: Optional[int] = None,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Set the DDS phase increment via DDSI.

    Provide either ``freq_hz`` (converted to a phase increment) or a raw ``inc``
    (0..0xFFFFFF; 0 selects the HDL default).  freq = inc / 2**24 * 1 GS/s, so
    0x100000 -> 62.5 MHz.  Route a DAC to ``dds`` to emit the tone.
    """
    if inc is None:
        if freq_hz is None:
            raise DaqControlError("set_dds requires freq_hz or inc")
        inc = dds_freq_to_inc(freq_hz)
    inc = int(inc)
    if inc < 0 or inc > 0x00FFFFFF:
        raise DaqControlError("DDS inc must be 0..0xFFFFFF (0 = HDL default)")
    command = f"DDSI 0x{inc:06X}"
    responses = send_uart_commands(
        [command], port_name, baud, timeout, expected={command: ("DDS inc=",)}
    )
    return {
        "command": command,
        "inc": f"0x{inc:06X}",
        "requested_freq_hz": freq_hz,
        "actual_freq_hz": dds_inc_to_freq(inc),
        "responses": responses,
    }


# ----------------------------------------------------------- current source
def current_ma_to_q16(value_ma: float) -> int:
    """Unipolar mA -> positive Q16.16 current word for the current source."""
    value = max(0.0, min(CURRENT_MAX_MA, float(value_ma)))
    return max(0, min(CURRENT_Q16_POS_MAX, int(round(value * CURRENT_MA_TO_Q16))))


def current_gain_to_q8_8(gain: float) -> int:
    return max(0, min(0xFFFF, int(round(float(gain) * 256.0))))


def choose_current_timing(
    freq_hz: float,
    n_max: int = CURRENT_WAVE_DEPTH,
    n_min: int = 16,
) -> Tuple[int, int, float]:
    """Choose sample count + cycles/sample for the 50 MHz current player."""
    freq = float(freq_hz)
    if not math.isfinite(freq) or freq <= 0.0:
        freq = CURRENT_PLAYER_CLK_HZ / (65535.0 * max(1, n_max))
    target_ticks = CURRENT_PLAYER_CLK_HZ / freq
    n_min = max(1, int(n_min))
    max_n = int(max(n_min, min(n_max, round(target_ticks))))
    best: Optional[Tuple[float, int, int, float]] = None
    for n in range(n_min, max_n + 1):
        cps = int(round(target_ticks / n))
        cps = max(1, min(65535, cps))
        actual = CURRENT_PLAYER_CLK_HZ / (cps * n)
        err = abs(actual - freq)
        if best is None or err < best[0] or (err == best[0] and n > best[1]):
            best = (err, n, cps, actual)
    assert best is not None
    _, n, cps, actual = best
    return n, cps, actual


def choose_current_square_timing(
    freq_hz: float,
    duty_percent: float = 50.0,
) -> Tuple[int, int, int, float]:
    """Return (low_count, high_count, cps, actual_hz) for a square wave."""
    n, cps, actual = choose_current_timing(freq_hz, n_min=2)
    duty = max(1.0, min(99.0, float(duty_percent))) / 100.0
    high = int(round(n * duty))
    high = max(1, min(n - 1, high))
    low = n - high
    return low, high, cps, actual


def set_current_gain(
    gain: float,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Set DAC-only current-source visibility gain (CURG, Q8.8)."""
    raw = current_gain_to_q8_8(gain)
    command = f"CURG 0x{raw:04X}"
    responses = send_uart_commands(
        [command], port_name, baud, timeout, expected={command: ("OK CURG",)}
    )
    return {"command": command, "gain": raw / 256.0, "raw_q8_8": f"0x{raw:04X}", "responses": responses}


def program_current_square(
    amp_ma: float,
    freq_hz: Optional[float] = None,
    duty_percent: float = 50.0,
    cps: Optional[int] = None,
    low_count: Optional[int] = None,
    high_count: Optional[int] = None,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Program a looping unipolar square wave via firmware ``CURS ... loop``
    (zero_count low samples then high_count amp samples, repeated).

    Either provide ``freq_hz`` + optional ``duty_percent`` or exact ``cps``,
    ``low_count``, and ``high_count``.
    """
    if cps is None or low_count is None or high_count is None:
        if freq_hz is None:
            raise DaqControlError("square requires freq_hz or exact cps/low_count/high_count")
        low_count, high_count, cps, actual = choose_current_square_timing(freq_hz, duty_percent)
    else:
        cps = int(cps)
        low_count = int(low_count)
        high_count = int(high_count)
        if cps < 1 or cps > 65535:
            raise DaqControlError("cps must be 1..65535")
        if low_count < 1 or high_count < 1 or low_count + high_count > CURRENT_WAVE_DEPTH:
            raise DaqControlError("low_count and high_count must be nonzero and sum to <=1024")
        actual = CURRENT_PLAYER_CLK_HZ / (cps * (low_count + high_count))
    amp_q16 = current_ma_to_q16(amp_ma)
    command = f"CURS {cps} {low_count} {high_count} 0x{amp_q16:08X} loop"
    responses = send_uart_commands(
        [command], port_name, baud, timeout, expected={command: ("OK CURS",)}
    )
    return {
        "command": command,
        "amp_ma": max(0.0, min(CURRENT_MAX_MA, float(amp_ma))),
        "amp_q16": f"0x{amp_q16:08X}",
        "requested_freq_hz": freq_hz,
        "actual_freq_hz": actual,
        "duty_percent": 100.0 * high_count / (low_count + high_count),
        "cps": cps,
        "low_count": low_count,
        "high_count": high_count,
        "responses": responses,
    }


def program_current_step(
    amp_ma: float,
    cps: int,
    zero_count: int,
    high_count: int,
    hold_last: bool = True,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Program a 0 -> amp step through firmware CURS."""
    cps = max(1, min(65535, int(cps)))
    zero_count = max(0, min(CURRENT_WAVE_DEPTH, int(zero_count)))
    high_count = max(0, min(CURRENT_WAVE_DEPTH - zero_count, int(high_count)))
    if zero_count + high_count <= 0:
        high_count = 1
    amp_q16 = current_ma_to_q16(amp_ma)
    mode = "hold" if hold_last else "loop"
    command = f"CURS {cps} {zero_count} {high_count} 0x{amp_q16:08X} {mode}"
    responses = send_uart_commands(
        [command], port_name, baud, timeout, expected={command: ("OK CURS",)}
    )
    return {
        "command": command,
        "amp_ma": max(0.0, min(CURRENT_MAX_MA, float(amp_ma))),
        "amp_q16": f"0x{amp_q16:08X}",
        "cps": cps,
        "zero_count": zero_count,
        "high_count": high_count,
        "hold_last": bool(hold_last),
        "responses": responses,
    }


def stop_current_source(
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Stop the current-source player (CURP off)."""
    responses = send_uart_commands(
        ["CURP off"], port_name, baud, timeout, expected={"CURP off": ("CURP off",)}
    )
    return {"command": "CURP off", "responses": responses}


# ------------------------------------------------------------------- neurons
def _normalize_neuron_target(target: Any) -> str:
    if isinstance(target, str) and target.strip().lower() == "all":
        return "all"
    try:
        channel = int(target)
    except (TypeError, ValueError):
        raise DaqControlError(f"neuron target must be 0..3 or 'all', got {target!r}")
    if channel < 0 or channel > 3:
        raise DaqControlError("neuron target must be 0..3 or 'all'")
    return str(channel)


def program_neuron(
    target: Any = "all",
    profile: Optional[str] = None,
    params: Optional[Mapping[str, float]] = None,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Program a neuron (channel 0..3 or 'all') with a built-in profile and/or
    explicit parameters.

    ``profile`` is one of NEURON_PROFILES.  ``params`` maps firmware NEUR params
    to values: a/b/c/d/i/iconst are PHYSICAL Izhikevich units (converted to
    Q16.16); dt/period/reset are raw integers.  Each NEUR write resets + reloads
    the target so it runs fresh with exactly these values.
    """
    tgt = _normalize_neuron_target(target)
    commands: List[str] = []
    expected: Dict[str, Tuple[str, ...]] = {}
    applied: Dict[str, Any] = {}

    if profile is not None:
        prof = str(profile).strip().lower()
        if prof not in NEURON_PROFILES:
            valid = ", ".join(NEURON_PROFILES)
            raise DaqControlError(f"unknown profile {profile!r}; valid: {valid}")
        command = f"NEUR {tgt} {prof}"
        commands.append(command)
        expected[command] = ("OK NEUR",)
        applied["profile"] = prof

    for name, value in dict(params or {}).items():
        key = str(name).strip().lower()
        if key in NEURON_PHYS_PARAMS:
            q16 = izh_to_q16(value)
            command = f"NEUR {tgt} {key} 0x{q16:08X}"
            applied[key] = {"physical": float(value), "q16": f"0x{q16:08X}"}
        elif key in NEURON_RAW_PARAMS:
            raw = int(value)
            command = f"NEUR {tgt} {key} {raw}"
            applied[key] = {"raw": raw}
        else:
            raise DaqControlError(
                f"unknown neuron param {name!r}; physical={NEURON_PHYS_PARAMS}, "
                f"raw={NEURON_RAW_PARAMS}"
            )
        commands.append(command)
        expected[command] = ("OK NEUR",)

    if not commands:
        raise DaqControlError("program_neuron requires a profile or params")
    responses = send_uart_commands(commands, port_name, baud, timeout, expected)
    return {"target": tgt, "applied": applied, "responses": responses}


# ------------------------------------------------------------------- streaming
def start_stream(
    decim: int = 128,
    cic: bool = False,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Start the cyclic UDP ADC stream (STRM <decim> [cic]).  decim must be a
    multiple of 4 in 4..65532; sample rate per channel = 1 GS/s / decim."""
    decim = int(decim)
    if decim < 4 or decim > 65532 or decim % 4 != 0:
        raise DaqControlError("decim must be a multiple of 4 in 4..65532")
    command = f"STRM {decim}{' cic' if cic else ''}"
    responses = send_uart_commands(
        [command], port_name, baud, timeout, expected={command: ("OK STRM",)}
    )
    return {
        "command": command,
        "decim": decim,
        "cic": bool(cic),
        "sample_rate_hz": ADC_SAMPLE_RATE_HZ / decim,
        "responses": responses,
    }


def stop_stream(
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Stop the cyclic UDP stream (STRM STOP)."""
    responses = send_uart_commands(
        ["STRM STOP"], port_name, baud, timeout, expected={"STRM STOP": ("OK STRM",)}
    )
    return {"responses": responses}


def set_cic(
    on: bool,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """Toggle the chip-1 (ch2/3) CIC anti-alias filter (STRM CIC on|off).
    The firmware applies this to the running stream, so start a stream first."""
    command = f"STRM CIC {'on' if on else 'off'}"
    responses = send_uart_commands(
        [command], port_name, baud, timeout, expected={command: ("OK STRM",)}
    )
    return {"command": command, "cic": bool(on), "responses": responses}


# ------------------------------------------------------------------- ethernet
def ping_board(
    ip: str = DEFAULT_BOARD_IP,
    count: int = 3,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """ICMP-ping the board's A53 ethernet interface to confirm the PS-eth app
    is up (UART works even when the A53/ethernet is down, so this is a useful
    independent check)."""
    count = max(1, int(count))
    if os.name == "nt":
        argv = ["ping", "-n", str(count), ip]
    else:
        argv = ["ping", "-c", str(count), ip]
    completed = subprocess.run(
        argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout, check=False,
    )
    out = completed.stdout or ""
    reachable = completed.returncode == 0 and "ttl=" in out.lower()
    return {
        "ip": ip,
        "reachable": reachable,
        "returncode": completed.returncode,
        "output": out,
    }


def _load_numpy():
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise DaqControlError(
            "numpy is required for captures; run `pip install -r requirements.txt`"
        ) from exc
    return np


def _load_burst_module():
    try:
        import burst_capture  # type: ignore
    except ImportError as exc:
        raise DaqControlError(
            "burst_capture not importable; run the server from the scripts/ dir"
        ) from exc
    return burst_capture


def summarize_channels(
    chans: Mapping[int, Any],
    fs_hz: float = ADC_SAMPLE_RATE_HZ,
    freq_floor_hz: float = 1.0e6,
) -> List[Dict[str, Any]]:
    """Per-channel stats (min/max/mean/rms, Vpp, dominant tone) for a captured
    {ch: int16[]} set -- a compact summary instead of returning raw samples."""
    np = _load_numpy()
    out: List[Dict[str, Any]] = []
    for ch in range(4):
        x = np.asarray(chans[ch]).astype(np.float64)
        item: Dict[str, Any] = {"ch": ch, "samples": int(x.size)}
        if x.size:
            mean = float(x.mean())
            item.update(
                min=float(x.min()), max=float(x.max()), mean=mean,
                rms_counts=float(np.sqrt(np.mean((x - mean) ** 2))),
                vpp=float((x.max() - x.min()) * VOLTS_PER_COUNT),
            )
            if x.size >= 16:
                v = (x - mean) * np.hanning(x.size)
                spec = np.abs(np.fft.rfft(v))
                freqs = np.fft.rfftfreq(x.size, 1.0 / fs_hz)
                lo = int(np.searchsorted(freqs, freq_floor_hz))
                if lo < spec.size:
                    k = lo + int(np.argmax(spec[lo:]))
                    item["dominant_freq_mhz"] = float(freqs[k] / 1e6)
        out.append(item)
    return out


def _save_capture_npz(capture_dir: Path, label: str, chans: Mapping[int, Any],
                      fs_hz: float, **meta: Any) -> Optional[Path]:
    np = _load_numpy()
    try:
        capture_dir.mkdir(parents=True, exist_ok=True)
        stem = f"cap_{time.strftime('%Y%m%d_%H%M%S')}_{label}"
        arrays = {f"ch{ch}": np.asarray(chans[ch], dtype=np.int16) for ch in range(4)}
        path = capture_dir / (stem + ".npz")
        np.savez_compressed(
            path, fs_hz=np.float64(fs_hz),
            **arrays, **{k: np.asarray(v) for k, v in meta.items()},
        )
        return path
    except Exception:  # noqa: BLE001
        return None


def collect_ethernet_burst(
    kb: int = 64,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    board_ip: str = DEFAULT_BOARD_IP,
    cmd_port: int = DEFAULT_CMD_PORT,
    local_ip: str = DEFAULT_LOCAL_IP,
    local_port: int = DEFAULT_LOCAL_PORT,
    drain_timeout: Optional[float] = None,
    save: bool = True,
    capture_dir: Optional[str] = None,
    label: str = "mcp",
    retries: int = 1,
    uart_timeout: float = DEFAULT_UART_TIMEOUT,
) -> Dict[str, Any]:
    """One-shot full-rate burst capture over Ethernet (BCAP + BRDO + UDP drain).

    Captures ``kb`` KB/chip (both chips, sample-aligned: chip0=ch0/1, chip1=
    ch2/3), reassembles the UDP readout, and returns a per-channel summary plus
    coverage.  The raw int16 channels are saved to a .npz under ``capture_dir``
    (default <repo>/captures) unless ``save`` is false.  This is the reliable way
    for an agent to actually SEE the ADC, far better than the cyclic stream.
    """
    serial = _load_serial_module()
    burst = _load_burst_module()
    kb = int(kb)
    if kb < 1:
        raise DaqControlError("kb must be >= 1")
    bytes_per_chip = kb * 1024
    if drain_timeout is None:
        drain_timeout = max(8.0, (2.0 * bytes_per_chip / 70.0e6) + 4.0)

    attempts = 1 + max(0, int(retries))
    asm = None
    ser = None
    best: Optional[Tuple[float, bool, float, float, Dict[int, Any]]] = None
    used = 0
    try:
        ser = serial.Serial(port_name, baud, timeout=5, write_timeout=5)
        time.sleep(0.2)
        asm = burst.Reassembler(board_ip, cmd_port, local_ip, local_port,
                                bytes_per_chip)
        # a cyclic stream and a one-shot burst can't share the DMA; stop it first
        # (STRM STOP always acks, even when idle).
        burst.uart_cmd(ser, "STRM STOP", ("OK STRM", "ERR"), timeout=uart_timeout)
        # Each attempt is a fresh BCAP+BRDO; retry only if the UDP drain dropped
        # packets (the data is in DDR, the loss is in readout). Breaks on the
        # first 100%-coverage capture, so the common case costs one pass.
        for attempt in range(attempts):
            used = attempt + 1
            if not asm.register(timeout=2.0):
                raise DaqControlError("BRST registration timed out (no BRST_READY from A53)")
            bcap = burst.uart_cmd(ser, f"BCAP {kb}k", ("OK BCAP", "ERR"), timeout=30.0)
            if not bcap.startswith("OK BCAP"):
                raise DaqControlError(f"BCAP failed: {bcap or '(no UART reply)'}")
            brdo = burst.uart_cmd(ser, "BRDO", ("OK BRDO", "ERR"), timeout=10.0)
            req = burst.parse_brdo_request(brdo)
            if not brdo.startswith("OK BRDO") or req is None:
                raise DaqControlError(f"BRDO failed: {brdo or '(no UART reply)'}")
            asm.set_request_id(req)            # clears the buffers for this request
            deadline = time.time() + drain_timeout
            while time.time() < deadline and not asm.complete():
                time.sleep(0.05)
            cov0, cov1 = asm.coverage(0), asm.coverage(1)
            complete = asm.complete()
            chans = {}
            chans.update(burst.decode_chip(asm.buf[0], 0))
            chans.update(burst.decode_chip(asm.buf[1], 2))
            mincov = min(cov0, cov1)
            if best is None or mincov > best[0]:
                best = (mincov, complete, cov0, cov1, chans)
            if complete:
                break
    finally:
        if asm is not None:
            asm.close()
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass

    _, complete, cov0, cov1, chans = best  # type: ignore[misc]
    result: Dict[str, Any] = {
        "kb_per_chip": kb,
        "bytes_per_chip": bytes_per_chip,
        "complete": bool(complete),
        "attempts": used,
        "coverage": {"chip0": round(cov0, 4), "chip1": round(cov1, 4)},
        "channels": summarize_channels(chans, ADC_SAMPLE_RATE_HZ),
    }
    if save:
        cap_dir = Path(capture_dir) if capture_dir else DEFAULT_CAPTURE_DIR
        path = _save_capture_npz(
            cap_dir, label, chans, ADC_SAMPLE_RATE_HZ,
            coverage=min(cov0, cov1), bytes_per_chip=bytes_per_chip,
        )
        result["saved"] = str(path) if path else None
    if not complete:
        result["warning"] = (
            "UDP drain incomplete; the capture is in DDR but readout lost packets"
        )
    return result


def uart_capture_snapshot(
    frames: int = 512,
    port_name: str = DEFAULT_PORT,
    baud: int = DEFAULT_BAUD,
    timeout: float = DEFAULT_UART_TIMEOUT,
    save: bool = True,
    capture_dir: Optional[str] = None,
    label: str = "uart",
) -> Dict[str, Any]:
    """Grab a 4-channel ADC snapshot over UART (PCAP) -- no Ethernet needed.
    Each frame is 4 samples/channel, so samples/ch = frames * 4 (frames 1..4096).
    Returns a per-channel summary and saves the raw int16 channels to a .npz."""
    np = _load_numpy()
    serial = _load_serial_module()
    frames = int(frames)
    if frames < 1 or frames > 4096:
        raise DaqControlError("frames must be 1..4096")
    sync = b"\xFE\x10\xCA\xFE"
    need = frames * 8 * 4
    data = bytearray()
    with serial.Serial(port_name, baud, timeout=2, write_timeout=timeout) as ser:
        time.sleep(0.2)
        ser.reset_input_buffer()
        ser.write((f"PCAP {frames}\n").encode("ascii"))
        ser.flush()
        win = bytearray()
        deadline = time.time() + max(15.0, float(timeout))
        synced = False
        while time.time() < deadline:
            b = ser.read(1)
            if not b:
                continue
            win += b
            if len(win) > 4:
                del win[0]
            if bytes(win) == sync:
                synced = True
                break
        if not synced:
            raise DaqControlError("PCAP: no FE10CAFE sync (timeout)")
        while len(data) < need:
            chunk = ser.read(need - len(data))
            if not chunk:
                break
            data += chunk
    if len(data) < need:
        raise DaqControlError(f"PCAP: short read {len(data)}/{need} bytes")
    arr = np.frombuffer(bytes(data), dtype="<u4").reshape(-1, 8)
    chans: Dict[int, Any] = {}
    for ch in range(4):
        w0, w1 = arr[:, 2 * ch], arr[:, 2 * ch + 1]
        s = np.empty(len(arr) * 4, dtype=np.int16)
        s[0::4] = (w0 & 0xFFFF).astype(np.int16)
        s[1::4] = ((w0 >> 16) & 0xFFFF).astype(np.int16)
        s[2::4] = (w1 & 0xFFFF).astype(np.int16)
        s[3::4] = ((w1 >> 16) & 0xFFFF).astype(np.int16)
        chans[ch] = s
    result: Dict[str, Any] = {
        "frames": frames,
        "samples_per_ch": int(frames * 4),
        "channels": summarize_channels(chans, ADC_SAMPLE_RATE_HZ),
    }
    if save:
        cap_dir = Path(capture_dir) if capture_dir else DEFAULT_CAPTURE_DIR
        path = _save_capture_npz(cap_dir, label, chans, ADC_SAMPLE_RATE_HZ)
        result["saved"] = str(path) if path else None
    return result


def describe() -> Dict[str, Any]:
    """Static capability/defaults summary for discovery (no board I/O)."""
    return {
        "server": "daq-launch-control",
        "uart": {"default_port": DEFAULT_PORT, "baud": DEFAULT_BAUD},
        "ethernet": {
            "board_ip": DEFAULT_BOARD_IP, "cmd_port": DEFAULT_CMD_PORT,
            "local_ip": DEFAULT_LOCAL_IP, "local_port": DEFAULT_LOCAL_PORT,
        },
        "dac_sources": sorted(DAC_SOURCES),
        "source_aliases": dict(SOURCE_ALIASES),
        "neuron_profiles": list(NEURON_PROFILES),
        "neuron_params": {
            "physical_q16": list(NEURON_PHYS_PARAMS),
            "raw_int": list(NEURON_RAW_PARAMS),
        },
        "dds": {
            "sample_rate_hz": DDS_SAMPLE_RATE_HZ,
            "phase_bits": DDS_PHASE_BITS,
            "example": "inc 0x100000 -> 62.5 MHz",
        },
        "current_source": {
            "player_clock_hz": CURRENT_PLAYER_CLK_HZ,
            "wave_depth_samples": CURRENT_WAVE_DEPTH,
            "max_ma": CURRENT_MAX_MA,
            "commands": ["CURW arbitrary", "CURS step/square(loop)", "CURG gain", "CURP off"],
        },
        "remote": {
            "host": DEFAULT_REMOTE_HOST, "user": DEFAULT_REMOTE_USER,
            "repo": DEFAULT_REMOTE_REPO, "branch": DEFAULT_BRANCH,
        },
        "capture_dir": str(DEFAULT_CAPTURE_DIR),
    }


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)
