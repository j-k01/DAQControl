#!/usr/bin/env python3
"""Headless one-input, one-heater-at-a-time MZI characterization.

This is the non-Qt counterpart of optical-experiment Mode B. It routes the
shared DDS to one selected photonic input, routes the same DDS to DAC3 as the
ADC3 electrical reference, performs phase-reset BCPD captures, and saves each
heater sweep in the same ``daq_optical_sweep`` format understood by the GUI.

Every heater is explicitly commanded to 0 V before every individual heater
sweep. Only the selected heater is then changed. A 0 V heater drive is an
electrical baseline; it is not interpreted as zero optical transfer weight.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import sys
import threading
import time
from typing import Callable, Mapping

import numpy as np
import serial

from burst_capture import Reassembler, decode_chip, parse_brdo_request
from mzi_calibration import PydaqMziController, calibration_voltage_sequence
from mzi_heater_map import MZI_NET_NAMES, ordered_heater_nets
from optical_experiment import (
    create_experiment,
    experiment_slug,
    save_heater_capture,
    update_manifest,
    write_json,
)
from tone_calibration import analyze_tone_capture, dds_phase_increment


VOLTS_PER_COUNT = 1.9 / 65536.0
DAC_FULLSCALE_V = 32767 * VOLTS_PER_COUNT
MAX_TOTAL_BYTES_PER_CHIP = 16 << 20
CAPTURE_GUARD_SAMPLES = 64
MIN_CAPTURED_CYCLES = 1.5

SOURCE_TOKENS = {"Off": "off", "DDS": "dds"}
SOURCE_CODES = {"Off": 0, "DDS": 1}
RETRYABLE_CAPTURE_ERRORS = (
    "UDP drain incomplete",
    "BRST registration timed out",
    "BRDO failed",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_metadata(reply: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for token in str(reply or "").split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        try:
            result[key] = int(value, 0)
        except ValueError:
            continue
    required = ("reps", "bytes_per_rep", "stride", "total_per_chip")
    missing = [key for key in required if key not in result]
    if missing:
        raise RuntimeError(
            f"unparseable BCPD reply {reply!r}; missing {', '.join(missing)}")
    return result


class DaqToneControl:
    """Minimal UART controller for headless Mode B acquisition."""

    def __init__(self, port: str, baud: int = 115200, serial_factory=None):
        factory = serial.Serial if serial_factory is None else serial_factory
        self.serial = factory(
            port, baud, timeout=2, write_timeout=3)
        self.lock = threading.Lock()
        self.port = str(port)
        time.sleep(0.2)

    def close(self) -> None:
        self.serial.close()

    def _readuntil(self, prefixes, timeout: float) -> str:
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            line = self.serial.readline().decode(
                "ascii", errors="replace").strip()
            if line.startswith(tuple(prefixes)):
                return line
        return ""

    def cmd(self, command: str, *, ok=("OK", "ERR"),
            timeout: float = 4.0) -> str:
        with self.lock:
            self.serial.reset_input_buffer()
            self.serial.write((command + "\n").encode("ascii"))
            self.serial.flush()
            return self._readuntil(ok, timeout)

    def stop_stream(self) -> None:
        reply = self.cmd("STRM STOP", ok=("OK STRM", "ERR"))
        if not reply or reply.startswith("ERR"):
            raise RuntimeError(
                f"STRM STOP failed: {reply or 'no UART reply'}")

    def set_source(self, channel: int, source: str) -> None:
        token = SOURCE_TOKENS[source]
        reply = self.cmd(
            f"NSRC {int(channel)} {token}", ok=("DAC xbar", "ERR"))
        if not reply or reply.startswith("ERR"):
            raise RuntimeError(
                f"DAC{channel} route failed: {reply or 'no UART reply'}")
        readback = self.cmd("RDRW 17", ok=("REG17", "ERR"))
        if not readback.startswith("REG17"):
            raise RuntimeError(
                f"DAC crossbar readback failed: {readback or 'no UART reply'}")
        try:
            value = int(readback.split("=", 1)[1].strip(), 0)
        except (IndexError, ValueError) as exc:
            raise RuntimeError(
                f"malformed DAC crossbar readback: {readback!r}") from exc
        actual = (value >> (4 * int(channel))) & 0xF
        if actual != SOURCE_CODES[source]:
            raise RuntimeError(
                f"DAC{channel} route readback is {actual}, expected "
                f"{SOURCE_CODES[source]} (reg17=0x{value & 0xFFFF:04X})")

    def configure_tone(self, input_dac: int,
                       frequency_hz: float) -> dict[str, object]:
        increment, actual = dds_phase_increment(frequency_hz)
        reply = self.cmd(
            f"DDSI 0x{increment:06X}", ok=("DDS inc=", "ERR"))
        if not reply or reply.startswith("ERR"):
            raise RuntimeError(
                f"DDS programming failed: {reply or 'no UART reply'}")
        sources = [
            "DDS" if channel == int(input_dac) else "Off"
            for channel in range(3)
        ] + ["DDS"]
        for channel, source in enumerate(sources):
            self.set_source(channel, source)
        readback = self.cmd("RDRW 17", ok=("REG17", "ERR"))
        if not readback.startswith("REG17"):
            raise RuntimeError(
                f"final DAC crossbar readback failed: "
                f"{readback or 'no UART reply'}")
        value = int(readback.split("=", 1)[1].strip(), 0) & 0xFFFF
        return {
            "sources": sources,
            "register17": value,
            "phase_increment": increment,
            "actual_frequency_hz": actual,
            "dds_reply": reply,
        }


@dataclass(frozen=True)
class CharacterizationConfig:
    port: str = "COM9"
    baud: int = 115200
    board_ip: str = "192.168.2.10"
    command_port: int = 5006
    local_ip: str = "192.168.2.1"
    local_port: int = 5005
    input_dac: int = 0
    frequency_hz: float = 100.0e3
    repetitions: int = 16
    capture_kb: int = 64
    heater_nets: tuple[str, ...] = MZI_NET_NAMES
    start_v: float = 0.0
    stop_v: float = 1.0
    points: int = 20
    reverse: bool = True
    spacing: str = "voltage"
    point_settle_s: float = 0.020
    baseline_settle_s: float = 0.100
    retries: int = 3
    capture_root: Path = Path("captures")
    name: str = "one_input_heater_characterization"

    @property
    def capture_bytes(self) -> int:
        return int(self.capture_kb) * 1024


def _parse_number_set(text: str, *, minimum: int, maximum: int) -> set[int]:
    selected: set[int] = set()
    for item in str(text).split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_text, stop_text = item.split("-", 1)
            start, stop = int(start_text), int(stop_text)
            if start > stop:
                raise ValueError(f"invalid descending range {item!r}")
            selected.update(range(start, stop + 1))
        else:
            selected.add(int(item))
    invalid = sorted(
        value for value in selected if not minimum <= value <= maximum)
    if invalid:
        raise ValueError(
            f"values outside {minimum}..{maximum}: {invalid}")
    return selected


def select_heaters(heaters: str | None, rows: str | None) -> tuple[str, ...]:
    if heaters and rows:
        raise ValueError("use either --heaters or --rows, not both")
    if rows:
        selected_rows = _parse_number_set(rows, minimum=1, maximum=9)
        return tuple(
            net for net in MZI_NET_NAMES
            if int(net.split("_")[1]) in selected_rows)
    if not heaters or heaters.strip().lower() == "all":
        return tuple(MZI_NET_NAMES)
    requested = [item.strip() for item in heaters.split(",") if item.strip()]
    return ordered_heater_nets(requested)


def automatic_capture_kb(frequency_hz: float) -> int:
    samples = CAPTURE_GUARD_SAMPLES + math.ceil(
        MIN_CAPTURED_CYCLES * 1.0e9 / float(frequency_hz))
    raw_bytes = samples * 4
    block = 64 * 1024
    return max(64, math.ceil(raw_bytes / block) * 64)


def validate_config(config: CharacterizationConfig) -> None:
    if config.input_dac not in (0, 1, 2):
        raise ValueError("input DAC must be 0, 1, or 2; DAC3 is the reference")
    if not 10.0e3 <= config.frequency_hz <= 10.0e6:
        raise ValueError("tone frequency must be in the 10 kHz..10 MHz range")
    if config.repetitions < 1:
        raise ValueError("repetitions must be positive")
    if config.capture_kb < 1:
        raise ValueError("capture size must be positive")
    if config.capture_bytes * config.repetitions > MAX_TOTAL_BYTES_PER_CHIP:
        raise ValueError(
            "capture size times repetitions exceeds the 16 MiB/chip DDR "
            "burst region")
    captured_cycles = (
        (config.capture_bytes // 4 - CAPTURE_GUARD_SAMPLES) *
        config.frequency_hz / 1.0e9)
    if captured_cycles < MIN_CAPTURED_CYCLES:
        raise ValueError(
            f"capture contains only {captured_cycles:.3f} tone cycles; "
            f"use at least --capture-kb "
            f"{automatic_capture_kb(config.frequency_hz)}")
    if not config.heater_nets:
        raise ValueError("select at least one heater")
    ordered_heater_nets(config.heater_nets)
    calibration_voltage_sequence(
        config.start_v, config.stop_v, config.points, config.reverse,
        spacing=config.spacing)
    if config.point_settle_s < 0.0 or config.baseline_settle_s < 0.0:
        raise ValueError("settling times cannot be negative")
    if config.retries < 1:
        raise ValueError("capture retries must be positive")


def _copy_request_into_combined(
    assembler: Reassembler,
    combined_buf: list[bytearray],
    combined_cov: list[np.ndarray],
) -> None:
    with assembler.lock:
        request_cov = [assembler.cov[0].copy(), assembler.cov[1].copy()]
        request_buf = [bytes(assembler.buf[0]), bytes(assembler.buf[1])]
    for chip in range(2):
        for slot in np.flatnonzero(request_cov[chip]):
            start = int(slot) * assembler.slot
            end = min(assembler.bytes_per_chip, start + assembler.slot)
            combined_buf[chip][start:end] = request_buf[chip][start:end]
        combined_cov[chip] |= request_cov[chip]


def capture_phase_aligned(
    daq: DaqToneControl,
    config: CharacterizationConfig,
    *,
    assembler_factory=Reassembler,
) -> dict[str, object]:
    """Perform one BCPD capture and losslessly re-drain its DDR image."""

    daq.stop_stream()
    reply = daq.cmd(
        f"BCPD {config.capture_kb}k {config.repetitions}",
        ok=("OK BCPD", "ERR"), timeout=180.0)
    if not reply or not reply.startswith("OK BCPD"):
        raise RuntimeError(
            f"BCPD failed: {reply or 'no UART reply'}")
    metadata = _read_metadata(reply)
    total = metadata["total_per_chip"]
    assembler = assembler_factory(
        config.board_ip, config.command_port,
        config.local_ip, config.local_port, total)
    try:
        combined_buf = [bytearray(total), bytearray(total)]
        combined_cov = [
            np.zeros(assembler.nslot, dtype=bool),
            np.zeros(assembler.nslot, dtype=bool),
        ]
        drain_attempt = 0
        for drain_attempt in range(3):
            if drain_attempt:
                time.sleep(0.4)
            assembler.begin_request()
            if not assembler.register(timeout=2.0):
                raise RuntimeError(
                    "BRST registration timed out (no BRST_READY from A53)")
            brdo = daq.cmd("BRDO", ok=("OK BRDO", "ERR"))
            request_id = parse_brdo_request(brdo)
            if not brdo.startswith("OK BRDO") or request_id is None:
                raise RuntimeError(
                    f"BRDO failed: {brdo or 'no UART reply'}")
            assembler.set_request_id(request_id)
            deadline = time.time() + max(10.0, (2.0 * total / 70.0e6) + 4.0)
            while time.time() < deadline and not assembler.complete():
                started = (
                    assembler.coverage(0) > 0.0 or
                    assembler.coverage(1) > 0.0)
                if started and assembler.idle(0.8):
                    break
                time.sleep(0.05)
            _copy_request_into_combined(
                assembler, combined_buf, combined_cov)
            if combined_cov[0].all() and combined_cov[1].all():
                break
        coverage = [float(bits.mean()) for bits in combined_cov]
        if not (combined_cov[0].all() and combined_cov[1].all()):
            raise RuntimeError(
                "UDP drain incomplete after 3 attempts: "
                f"chip0 {100.0 * coverage[0]:.1f}%, "
                f"chip1 {100.0 * coverage[1]:.1f}% combined coverage")

        channels: dict[int, np.ndarray] = {}
        channels.update(decode_chip(combined_buf[0], 0))
        channels.update(decode_chip(combined_buf[1], 2))
        samples_per_rep = metadata["bytes_per_rep"] // 4
        stride_samples = metadata["stride"] // 4
        repetitions = metadata["reps"]
        stacks = {
            channel: np.stack([
                channels[channel][
                    repetition * stride_samples:
                    repetition * stride_samples + samples_per_rep]
                for repetition in range(repetitions)
            ]).astype(np.int16, copy=False)
            for channel in range(4)
        }
        metadata.update({
            "coverage_by_chip": coverage,
            "drain_attempts": drain_attempt + 1,
        })
        return {"stack": stacks, "meta": metadata}
    finally:
        assembler.close()


def capture_with_retries(
    capture: Callable[[], dict[str, object]],
    attempts: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    for attempt in range(max(1, int(attempts))):
        try:
            return capture()
        except RuntimeError as exc:
            retryable = any(token in str(exc)
                            for token in RETRYABLE_CAPTURE_ERRORS)
            if not retryable or attempt + 1 >= attempts:
                raise
            print(
                f"  transient Ethernet readout failure; repeating BCPD "
                f"capture ({attempt + 2}/{attempts}): {exc}",
                file=sys.stderr, flush=True)
            sleep(0.75)
    raise AssertionError("unreachable")


def _tone_metadata(
    config: CharacterizationConfig,
    heater: str,
    voltages: np.ndarray,
    directions: np.ndarray,
    tone_setup: Mapping[str, object],
    all_zero: Mapping[str, float],
) -> dict[str, object]:
    actual_frequency = float(tone_setup["actual_frequency_hz"])
    return {
        "hardware": {
            "board_ip": config.board_ip,
            "local_ip": config.local_ip,
            "uart_port": config.port,
            "sample_rate_hz": 1.0e9,
        },
        "acquisition": {
            "transport": "BCPD DDS-phase-aligned capture + BRDO UDP Ethernet",
            "capture_bytes_per_chip_per_repetition": config.capture_bytes,
            "samples_per_channel_per_repetition": config.capture_bytes // 4,
            "repetitions_per_heater_capture": config.repetitions,
            "adc_channels": [0, 1, 2, 3],
            "optical_adc_channels": [0, 1, 2],
            "reference_adc_channel": 3,
            "reference_dac_channel": 3,
            "channels_averaged_independently": True,
        },
        "xbar": {
            "sources_by_dac": {
                f"DAC{channel}": source for channel, source in
                enumerate(tone_setup["sources"])
            },
            "register17_readback":
                f"0x{int(tone_setup['register17']):04X}",
            "fixed_during_sweep": True,
        },
        "stimulus": {
            "mode": "shared_dds_pure_tone",
            "requested_frequency_hz": config.frequency_hz,
            "actual_frequency_hz": actual_frequency,
            "phase_increment": int(tone_setup["phase_increment"]),
            "offset_v": 0.0,
            "range_v": [-DAC_FULLSCALE_V, DAC_FULLSCALE_V],
            "enabled_photonic_dacs": [config.input_dac],
            "electrical_reference": "DAC3 to ADC3",
            "phase_restarted_for_every_repetition": True,
            "captured_tone_cycles": (
                (config.capture_bytes // 4 - CAPTURE_GUARD_SAMPLES) *
                actual_frequency / 1.0e9),
        },
        "heater_sweep": {
            "heater_nets": [heater],
            "primary_heater_net": heater,
            "shared_sweep_voltage": False,
            "spacing": config.spacing,
            "heater_voltages_before_sweep": dict(all_zero),
            "planned_voltages_v": voltages,
            "planned_directions": directions,
            "settle_seconds": config.point_settle_s,
            "restore_voltage_v": 0.0,
            "electrical_baseline": "all 54 heater drives explicitly set to 0 V",
            "baseline_interpretation":
                "0 V heater drive is not assumed to mean zero optical weight",
        },
        "analysis": {
            "method": "coherent average then least-squares sine fit",
            "reported_per_adc": [
                "amplitude_v", "gain_vs_reference",
                "phase_vs_reference_rad", "latency_modulo_period_ns",
            ],
            "latency_note": "single-tone latency is modulo one tone period",
        },
        "software": {
            "capture_application": "scripts/headless_tone_characterization.py",
            "processor": "scripts/tone_calibration.py",
        },
    }


def _save_tone_point(point_dir: Path, voltage: float, direction: int,
                     analysis: Mapping[str, object]) -> dict[str, object]:
    scalar_channels = {
        str(channel): {
            key: float(value)
            for key, value in channel_result.items()
            if key not in ("average_v", "fitted_v")
        }
        for channel, channel_result in analysis["channels"].items()
    }
    result = {
        "voltage_v": float(voltage),
        "direction": int(direction),
        "channels": scalar_channels,
    }
    (point_dir / "tone_analysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return result


def _save_tone_summary(experiment_dir: Path, points: list[dict[str, object]]) -> None:
    voltages = np.asarray(
        [point["voltage_v"] for point in points], dtype=np.float64)
    directions = np.asarray(
        [point["direction"] for point in points], dtype=np.int8)
    amplitudes = {
        channel: np.asarray([
            point["channels"][str(channel)]["amplitude_v"]
            for point in points
        ], dtype=np.float64)
        for channel in range(4)
    }
    gains = {
        channel: np.asarray([
            point["channels"][str(channel)]["gain_vs_reference"]
            for point in points
        ], dtype=np.float64)
        for channel in range(3)
    }
    latencies = {
        channel: np.asarray([
            point["channels"][str(channel)]["latency_modulo_period_ns"]
            for point in points
        ], dtype=np.float64)
        for channel in range(3)
    }
    np.savez_compressed(
        experiment_dir / "tone_summary.npz",
        voltages_v=voltages,
        directions=directions,
        **{f"amplitude_adc{channel}_v": values
           for channel, values in amplitudes.items()},
        **{f"gain_adc{channel}_vs_adc3": values
           for channel, values in gains.items()},
        **{f"latency_adc{channel}_ns": values
           for channel, values in latencies.items()},
    )


def _create_batch_directory(root: Path, name: str) -> Path:
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = root / f"{stamp}_{experiment_slug(name)}"
    result = base
    suffix = 2
    while result.exists():
        result = Path(f"{base}_{suffix}")
        suffix += 1
    result.mkdir()
    return result


def run_characterization(
    config: CharacterizationConfig,
    *,
    daq=None,
    heater_controller=None,
    capture_function=None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Run the headless characterization and return its batch directory."""

    validate_config(config)
    voltages, directions = calibration_voltage_sequence(
        config.start_v, config.stop_v, config.points, config.reverse,
        spacing=config.spacing)
    all_zero = {net: 0.0 for net in MZI_NET_NAMES}
    owns_daq = daq is None
    owns_heaters = heater_controller is None
    daq = daq or DaqToneControl(config.port, config.baud)
    heater_controller = heater_controller or PydaqMziController()
    batch_dir: Path | None = None
    current_experiment: Path | None = None
    try:
        tone_setup = daq.configure_tone(config.input_dac, config.frequency_hz)
        if owns_heaters:
            heater_controller.connect(
                board_ip=config.board_ip, local_ip=config.local_ip)
        available = set(heater_controller.available_nets())
        missing = sorted(set(MZI_NET_NAMES) - available)
        if missing:
            raise RuntimeError(
                "PICO-002 configuration is missing heater nets required for "
                f"the explicit all-zero baseline: {missing}")

        batch_dir = _create_batch_directory(
            config.capture_root,
            f"{config.name}_dac{config.input_dac}")
        batch_manifest = {
            "schema": "daq_mzi_one_input_characterization",
            "schema_version": 1,
            "created_utc": utc_now(),
            "capture_status": "running",
            "experiment_name": config.name,
            "input_dac": config.input_dac,
            "selected_heaters": list(config.heater_nets),
            "heater_baseline_v": all_zero,
            "baseline_interpretation":
                "0 V heater drive is not assumed to mean zero optical weight",
            "experiments": [],
        }
        write_json(batch_dir / "characterization.json", batch_manifest)

        capture_impl = capture_function or (
            lambda: capture_phase_aligned(daq, config))
        records = []
        for heater_index, heater in enumerate(config.heater_nets, start=1):
            print(
                f"[{heater_index}/{len(config.heater_nets)}] {heater}: "
                "commanding every heater drive to 0.000 V",
                flush=True)
            heater_controller.set_voltages(all_zero)
            if config.baseline_settle_s:
                sleep(config.baseline_settle_s)
            metadata = _tone_metadata(
                config, heater, voltages, directions, tone_setup, all_zero)
            current_experiment = create_experiment(
                batch_dir, f"{config.name}_{heater}", metadata)
            tone_points: list[dict[str, object]] = []
            try:
                for point_index, (voltage, direction) in enumerate(
                        zip(voltages, directions)):
                    voltage = float(voltage)
                    direction = int(direction)
                    heater_controller.set_voltage(heater, voltage)
                    if config.point_settle_s:
                        sleep(config.point_settle_s)
                    capture = capture_with_retries(
                        capture_impl, config.retries, sleep=sleep)
                    raw_stacks = {
                        channel: np.asarray(
                            capture["stack"][channel], dtype=np.int16)
                        for channel in range(4)
                    }
                    analysis = analyze_tone_capture(
                        {
                            channel: stack.astype(np.float64) * VOLTS_PER_COUNT
                            for channel, stack in raw_stacks.items()
                        },
                        float(tone_setup["actual_frequency_hz"]),
                        reference_adc=3,
                        start_sample=CAPTURE_GUARD_SAMPLES)
                    live_voltages = dict(all_zero)
                    live_voltages[heater] = voltage
                    point_dir = save_heater_capture(
                        current_experiment,
                        index=point_index,
                        voltage_v=voltage,
                        direction=direction,
                        stacks=raw_stacks,
                        capture_meta={
                            "burst_command": "BCPD",
                            "burst": capture.get("meta", {}),
                            "heater_nets": [heater],
                            "heater_voltages_v": live_voltages,
                            "all_other_heater_drives_v": 0.0,
                        })
                    point = _save_tone_point(
                        point_dir, voltage, direction, analysis)
                    tone_points.append(point)
                    amplitudes_mv = [
                        1.0e3 * float(
                            point["channels"][str(channel)]["amplitude_v"])
                        for channel in range(3)
                    ]
                    print(
                        f"  {point_index + 1:02d}/{len(voltages)} "
                        f"{voltage:.4f} V "
                        f"{'reverse' if direction else 'forward'} | "
                        f"ADC0..2 {amplitudes_mv[0]:.4f}, "
                        f"{amplitudes_mv[1]:.4f}, "
                        f"{amplitudes_mv[2]:.4f} mV",
                        flush=True)
                _save_tone_summary(current_experiment, tone_points)
                update_manifest(
                    current_experiment,
                    capture_status="complete",
                    completed_utc=utc_now())
            except Exception as exc:
                update_manifest(
                    current_experiment,
                    capture_status="failed",
                    failure=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                heater_controller.set_voltages(all_zero)
                if config.baseline_settle_s:
                    sleep(config.baseline_settle_s)

            relative = current_experiment.relative_to(batch_dir).as_posix()
            record = {
                "heater_net": heater,
                "directory": relative,
                "points": len(tone_points),
                "capture_status": "complete",
            }
            records.append(record)
            batch_manifest["experiments"] = records
            write_json(batch_dir / "characterization.json", batch_manifest)
            current_experiment = None

        batch_manifest.update({
            "capture_status": "complete",
            "completed_utc": utc_now(),
            "experiments": records,
        })
        write_json(batch_dir / "characterization.json", batch_manifest)
        return batch_dir
    except Exception as exc:
        if batch_dir is not None:
            try:
                manifest_path = batch_dir / "characterization.json"
                batch_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8"))
                batch_manifest.update({
                    "capture_status": "failed",
                    "failure": f"{type(exc).__name__}: {exc}",
                    "failed_utc": utc_now(),
                })
                write_json(manifest_path, batch_manifest)
            except Exception:
                pass
        raise
    finally:
        try:
            if heater_controller.connected:
                heater_controller.set_voltages(all_zero)
        except Exception as restore_error:
            print(
                f"WARNING: final all-heater 0 V restore failed: "
                f"{restore_error}", file=sys.stderr)
        if owns_heaters:
            heater_controller.close()
        if owns_daq:
            daq.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="COM9")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--board-ip", default="192.168.2.10")
    parser.add_argument("--cmd-port", type=int, default=5006)
    parser.add_argument("--local-ip", default="192.168.2.1")
    parser.add_argument("--local-port", type=int, default=5005)
    parser.add_argument("--input-dac", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument(
        "--frequency-khz", type=float, default=100.0,
        help="shared DDS frequency, 10..10000 kHz (default: 100)")
    parser.add_argument("--reps", type=int, default=16)
    parser.add_argument(
        "--capture-kb", type=int, default=0,
        help="bytes/chip/repetition in KiB; 0 chooses the shortest capture "
             "with at least 1.5 tone cycles")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--heaters",
        help="comma-separated nets such as h_1_1,h_7_3, or 'all' (default)")
    selection.add_argument(
        "--rows",
        help="comma-separated physical rows/ranges such as 1,3,7-9")
    parser.add_argument("--start-v", type=float, default=0.0)
    parser.add_argument("--stop-v", type=float, default=1.0)
    parser.add_argument("--points", type=int, default=20)
    parser.add_argument(
        "--forward-only", action="store_true",
        help="omit the reverse sweep")
    parser.add_argument(
        "--spacing", choices=("voltage", "power"), default="voltage")
    parser.add_argument("--settle-ms", type=float, default=20.0)
    parser.add_argument(
        "--baseline-settle-ms", type=float, default=100.0,
        help="settle after explicitly commanding all 54 heaters to 0 V")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--capture-root", default="captures")
    parser.add_argument("--name", default="one_input_heater_characterization")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate and print the experiment without opening hardware")
    return parser


def config_from_args(args) -> CharacterizationConfig:
    frequency_hz = float(args.frequency_khz) * 1000.0
    capture_kb = int(args.capture_kb)
    if capture_kb == 0:
        capture_kb = automatic_capture_kb(frequency_hz)
    return CharacterizationConfig(
        port=args.port,
        baud=args.baud,
        board_ip=args.board_ip,
        command_port=args.cmd_port,
        local_ip=args.local_ip,
        local_port=args.local_port,
        input_dac=args.input_dac,
        frequency_hz=frequency_hz,
        repetitions=args.reps,
        capture_kb=capture_kb,
        heater_nets=select_heaters(args.heaters, args.rows),
        start_v=args.start_v,
        stop_v=args.stop_v,
        points=args.points,
        reverse=not args.forward_only,
        spacing=args.spacing,
        point_settle_s=args.settle_ms / 1000.0,
        baseline_settle_s=args.baseline_settle_ms / 1000.0,
        retries=args.retries,
        capture_root=Path(args.capture_root),
        name=args.name,
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = config_from_args(args)
        validate_config(config)
    except ValueError as exc:
        parser.error(str(exc))
    voltages, _directions = calibration_voltage_sequence(
        config.start_v, config.stop_v, config.points, config.reverse,
        spacing=config.spacing)
    print(
        f"Input DAC{config.input_dac}; DAC3/ADC3 electrical reference; "
        f"{config.frequency_hz / 1000.0:.3f} kHz; "
        f"{config.repetitions} repetitions; {config.capture_kb} KiB/chip/rep")
    print(
        f"{len(config.heater_nets)} heater(s) x {len(voltages)} point(s) = "
        f"{len(config.heater_nets) * len(voltages)} averaged captures")
    print(
        "Before every individual heater sweep: explicitly command all 54 "
        "heater drive voltages to 0.000 V. This is not a zero-weight claim.")
    if args.dry_run:
        print("Dry run only; no hardware commands were sent.")
        print("Selected heaters: " + ", ".join(config.heater_nets))
        return 0
    try:
        output = run_characterization(config)
    except KeyboardInterrupt:
        print("Interrupted; all-heater 0 V restore was attempted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Complete: {output}")
    print(
        "In the GUI, use Optical results -> Import experiment and select any "
        "heater experiment subdirectory inside that batch directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
