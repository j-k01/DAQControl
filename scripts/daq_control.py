#!/usr/bin/env python3
"""Shared board-control helpers for DAQ_LAUNCH host tools.

This module is intentionally small and boring: it wraps the stable board actions
that agents and humans keep needing, without hiding the underlying UART/SSH
commands.  The CLI and MCP server both import this file.
"""

from __future__ import annotations

import json
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


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)
