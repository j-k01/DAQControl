#!/usr/bin/env python3
"""Extract per-heater MZI calibrations from headless Mode B batches.

Pass one or more batch directories produced by
``headless_tone_characterization.py``. The physical mapping is DAC0 to
photonic row 7, DAC1 to row 8, and DAC2 to row 9. A complete calibration
therefore contains six row-7 sweeps from DAC0, six row-8 sweeps from DAC1,
and six row-9 sweeps from DAC2. Off-row sweeps may characterize crosstalk but
are never selected as the primary calibration path.

The primary reported response remains fitted tone amplitude in millivolts.
ADC3 is used only to compensate capture-to-capture gain drift while preserving
the median physical millivolt scale; no 0..1 optical-weight normalization is
performed. The fitted MZI model is ``a + b*cos(k*V^2) + c*sin(k*V^2)``.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import csv
import json
import math
from pathlib import Path
import sys
from typing import Iterable

import numpy as np

from mzi_heater_map import (
    PHOTONIC_INPUT_DAC_BY_ROW,
    PHOTONIC_INPUT_ROW_BY_DAC,
    calibration_heaters_for_dac,
)
from optical_experiment import load_manifest, write_json


@dataclass(frozen=True)
class PowerCurveFit:
    offset_mv: float
    cos_coefficient_mv: float
    sin_coefficient_mv: float
    modulation_mv: float
    phase_offset_rad: float
    phase_slope_rad_per_v2: float
    rmse_mv: float
    r_squared: float
    predicted_mv: np.ndarray


@dataclass(frozen=True)
class MonotonicBranch:
    start_index: int
    stop_index: int
    voltage_start_v: float
    voltage_stop_v: float
    amplitude_start_mv: float
    amplitude_stop_mv: float
    span_mv: float
    increasing: bool
    isotonic_rmse_mv: float
    voltage_v: np.ndarray
    amplitude_mv: np.ndarray


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jsonable(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _unique_voltage_curve(voltage_v, amplitude_mv):
    voltage = np.asarray(voltage_v, dtype=np.float64)
    amplitude = np.asarray(amplitude_mv, dtype=np.float64)
    valid = np.isfinite(voltage) & np.isfinite(amplitude)
    voltage, amplitude = voltage[valid], amplitude[valid]
    if voltage.size < 4:
        raise ValueError("MZI curve fit requires at least four finite points")
    order = np.argsort(voltage)
    voltage, amplitude = voltage[order], amplitude[order]
    unique, inverse = np.unique(voltage, return_inverse=True)
    if unique.size != voltage.size:
        sums = np.bincount(inverse, weights=amplitude)
        counts = np.bincount(inverse)
        amplitude = sums / counts
        voltage = unique
    return voltage, amplitude


def fit_power_curve(voltage_v, amplitude_mv, *, max_fringes=4.0,
                    grid_points=1600) -> PowerCurveFit:
    """Fit a thermal MZI cosine with phase linear in heater power (V^2)."""

    if not np.isfinite(max_fringes) or max_fringes <= 0.0:
        raise ValueError("max_fringes must be a positive finite value")
    if int(grid_points) < 2:
        raise ValueError("grid_points must be at least two")
    voltage, amplitude = _unique_voltage_curve(voltage_v, amplitude_mv)
    power = voltage * voltage
    total = float(np.sum((amplitude - np.mean(amplitude)) ** 2))
    minimum_slope = 0.20 * math.pi / max(float(np.ptp(power)), 1.0e-9)
    maximum_slope = (
        2.0 * math.pi * float(max_fringes) /
        max(float(np.ptp(power)), 1.0e-9))
    slopes = np.linspace(minimum_slope, maximum_slope, int(grid_points))
    best = None
    for slope in slopes:
        design = np.column_stack((
            np.ones(power.size),
            np.cos(slope * power),
            np.sin(slope * power),
        ))
        coefficients = np.linalg.lstsq(design, amplitude, rcond=None)[0]
        predicted = design @ coefficients
        squared_error = float(np.sum((amplitude - predicted) ** 2))
        if best is None or squared_error < best[0]:
            best = (squared_error, slope, coefficients)

    _error, coarse_slope, _coefficients = best
    coarse_step = float(slopes[1] - slopes[0])
    refine = np.linspace(
        max(minimum_slope, coarse_slope - 2.0 * coarse_step),
        min(maximum_slope, coarse_slope + 2.0 * coarse_step), 201)
    for slope in refine:
        design = np.column_stack((
            np.ones(power.size),
            np.cos(slope * power),
            np.sin(slope * power),
        ))
        coefficients = np.linalg.lstsq(design, amplitude, rcond=None)[0]
        predicted = design @ coefficients
        squared_error = float(np.sum((amplitude - predicted) ** 2))
        if squared_error < best[0]:
            best = (squared_error, slope, coefficients)

    squared_error, slope, coefficients = best
    predicted = (
        coefficients[0] + coefficients[1] * np.cos(slope * power) +
        coefficients[2] * np.sin(slope * power))
    modulation = float(np.hypot(coefficients[1], coefficients[2]))
    phase = float(np.arctan2(-coefficients[2], coefficients[1]))
    return PowerCurveFit(
        offset_mv=float(coefficients[0]),
        cos_coefficient_mv=float(coefficients[1]),
        sin_coefficient_mv=float(coefficients[2]),
        modulation_mv=modulation,
        phase_offset_rad=phase,
        phase_slope_rad_per_v2=float(slope),
        rmse_mv=float(np.sqrt(squared_error / amplitude.size)),
        r_squared=(
            float(1.0 - squared_error / total) if total > 0.0 else 0.0),
        predicted_mv=np.asarray(predicted, dtype=np.float64),
    )


def _isotonic_increasing(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    levels: list[float] = []
    weights: list[int] = []
    starts: list[int] = []
    for index, value in enumerate(values):
        levels.append(float(value))
        weights.append(1)
        starts.append(index)
        while len(levels) >= 2 and levels[-2] > levels[-1]:
            weight = weights[-2] + weights[-1]
            level = (
                levels[-2] * weights[-2] + levels[-1] * weights[-1]) / weight
            levels[-2:] = [level]
            weights[-2:] = [weight]
            starts[-2:] = [starts[-2]]
    result = np.empty(values.size, dtype=np.float64)
    for block, (start, weight) in enumerate(zip(starts, weights)):
        result[start:start + weight] = levels[block]
    return result


def choose_monotonic_branch(voltage_v, amplitude_mv) -> MonotonicBranch:
    """Choose the measured branch with the largest useful monotonic swing."""

    voltage, amplitude = _unique_voltage_curve(voltage_v, amplitude_mv)
    if amplitude.size >= 3:
        smoothed = np.convolve(
            np.pad(amplitude, (1, 1), mode="edge"),
            np.ones(3) / 3.0, mode="valid")
    else:
        smoothed = amplitude.copy()
    differences = np.diff(smoothed)
    tolerance = max(float(np.ptp(smoothed)) * 0.02, 1.0e-12)
    signs = np.sign(differences)
    signs[np.abs(differences) <= tolerance] = 0
    for index in range(1, signs.size):
        if signs[index] == 0:
            signs[index] = signs[index - 1]
    for index in range(signs.size - 2, -1, -1):
        if signs[index] == 0:
            signs[index] = signs[index + 1]
    extrema = [0]
    extrema.extend(
        index for index in range(1, signs.size)
        if signs[index] != signs[index - 1])
    extrema.append(amplitude.size - 1)
    extrema = sorted(set(extrema))
    candidates = []
    for start, stop in zip(extrema[:-1], extrema[1:]):
        if stop - start < 2:
            continue
        raw = amplitude[start:stop + 1]
        increasing = bool(raw[-1] >= raw[0])
        fitted = (
            _isotonic_increasing(raw) if increasing else
            -_isotonic_increasing(-raw))
        span = float(abs(fitted[-1] - fitted[0]))
        correction = float(np.sqrt(np.mean((raw - fitted) ** 2)))
        candidates.append((span - correction, span, start, stop,
                           increasing, correction, fitted))
    if not candidates:
        start, stop = 0, amplitude.size - 1
        raw = amplitude
        increasing = bool(raw[-1] >= raw[0])
        fitted = (
            _isotonic_increasing(raw) if increasing else
            -_isotonic_increasing(-raw))
        correction = float(np.sqrt(np.mean((raw - fitted) ** 2)))
        candidates.append((
            float(abs(fitted[-1] - fitted[0])) - correction,
            float(abs(fitted[-1] - fitted[0])), start, stop,
            increasing, correction, fitted))
    _score, span, start, stop, increasing, correction, fitted = max(
        candidates, key=lambda item: item[0])
    return MonotonicBranch(
        start_index=int(start),
        stop_index=int(stop),
        voltage_start_v=float(voltage[start]),
        voltage_stop_v=float(voltage[stop]),
        amplitude_start_mv=float(fitted[0]),
        amplitude_stop_mv=float(fitted[-1]),
        span_mv=float(span),
        increasing=bool(increasing),
        isotonic_rmse_mv=float(correction),
        voltage_v=voltage[start:stop + 1].copy(),
        amplitude_mv=np.asarray(fitted, dtype=np.float64),
    )


def branch_lut(branch: MonotonicBranch, levels: int = 33):
    count = max(2, int(levels))
    x = branch.amplitude_mv
    y = branch.voltage_v
    if x[-1] < x[0]:
        x, y = x[::-1], y[::-1]
    unique_x, unique_indices = np.unique(x, return_index=True)
    unique_y = y[unique_indices]
    targets = np.linspace(float(unique_x[0]), float(unique_x[-1]), count)
    voltages = np.interp(targets, unique_x, unique_y)
    return targets, voltages


def _load_experiment(experiment_dir: Path, input_dac: int):
    manifest = load_manifest(experiment_dir)
    heater = str(manifest["heater_sweep"]["primary_heater_net"])
    captures = sorted(
        manifest["heater_captures"], key=lambda item: int(item["index"]))
    points = []
    for descriptor in captures:
        point_dir = experiment_dir / descriptor["directory"]
        analysis = json.loads(
            (point_dir / "tone_analysis.json").read_text(encoding="utf-8"))
        points.append(analysis)
    return {
        "input_dac": int(input_dac),
        "heater_net": heater,
        "experiment_dir": experiment_dir,
        "repetitions": int(
            manifest["acquisition"]["repetitions_per_heater_capture"]),
        "voltage_v": np.asarray(
            [point["voltage_v"] for point in points], dtype=np.float64),
        "direction": np.asarray(
            [point["direction"] for point in points], dtype=np.int8),
        "channels": {
            channel: {
                key: np.asarray([
                    point["channels"][str(channel)][key] for point in points
                ], dtype=np.float64)
                for key in (
                    "amplitude_v", "amplitude_std_v", "residual_rms_v",
                    "gain_vs_reference", "phase_vs_reference_rad",
                    "latency_modulo_period_ns")
            }
            for channel in range(4)
        },
    }


def load_batches(batch_directories: Iterable[Path]):
    experiments = []
    batches = []
    for batch in batch_directories:
        batch = Path(batch).expanduser().resolve()
        manifest_path = batch / "characterization.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("capture_status") != "complete":
            raise ValueError(f"batch is not complete: {batch}")
        input_dac = int(manifest["input_dac"])
        batches.append({"path": batch, "input_dac": input_dac})
        for descriptor in manifest["experiments"]:
            experiment_dir = batch / descriptor["directory"]
            experiments.append(_load_experiment(experiment_dir, input_dac))
    seen = set()
    for experiment in experiments:
        key = (experiment["input_dac"], experiment["heater_net"])
        if key in seen:
            raise ValueError(
                f"duplicate input/heater experiment: DAC{key[0]} {key[1]}")
        seen.add(key)
    return batches, experiments


def _reverse_hysteresis(voltage, direction, response):
    forward = direction == 0
    reverse = direction == 1
    if np.count_nonzero(forward) < 2 or np.count_nonzero(reverse) < 2:
        return float("nan")
    vf, yf = _unique_voltage_curve(voltage[forward], response[forward])
    vr, yr = _unique_voltage_curve(voltage[reverse], response[reverse])
    lower = max(float(vf.min()), float(vr.min()))
    upper = min(float(vf.max()), float(vr.max()))
    sample = np.linspace(lower, upper, max(vf.size, vr.size))
    difference = np.interp(sample, vf, yf) - np.interp(sample, vr, yr)
    return float(np.sqrt(np.mean(difference * difference)))


def analyze_experiment(experiment, *, max_fringes=4.0, lut_levels=33):
    voltage = experiment["voltage_v"]
    direction = experiment["direction"]
    reference = experiment["channels"][3]["amplitude_v"] * 1.0e3
    valid_reference = np.isfinite(reference) & (reference > 0.0)
    if not np.all(valid_reference):
        raise ValueError(
            f"ADC3 reference is invalid for DAC{experiment['input_dac']} "
            f"{experiment['heater_net']}")
    reference_scale = float(np.median(reference))
    results = []
    for channel in range(3):
        absolute = experiment["channels"][channel]["amplitude_v"] * 1.0e3
        corrected = absolute * reference_scale / reference
        forward = direction == 0
        fit = fit_power_curve(
            voltage[forward], corrected[forward], max_fringes=max_fringes)
        branch = choose_monotonic_branch(
            voltage[forward], corrected[forward])
        target_mv, target_voltage_v = branch_lut(branch, lut_levels)
        repetitions = max(1, int(experiment["repetitions"]))
        point_uncertainty = (
            experiment["channels"][channel]["amplitude_std_v"] * 1.0e3 /
            math.sqrt(repetitions))
        uncertainty = max(
            float(np.median(point_uncertainty[np.isfinite(point_uncertainty)])),
            1.0e-6)
        observed_span = float(np.ptp(corrected[forward]))
        hysteresis = _reverse_hysteresis(voltage, direction, corrected)
        hysteresis_ratio = (
            hysteresis / observed_span
            if np.isfinite(hysteresis) and observed_span > 0.0 else float("nan"))
        span_snr = observed_span / uncertainty
        quality_score = (
            observed_span * math.sqrt(max(0.0, fit.r_squared)) /
            (uncertainty + fit.rmse_mv + 1.0e-9))
        if (fit.r_squared >= 0.95 and span_snr >= 10.0 and
                (not np.isfinite(hysteresis_ratio) or hysteresis_ratio <= 0.10)):
            grade = "excellent"
        elif (fit.r_squared >= 0.85 and span_snr >= 5.0 and
              (not np.isfinite(hysteresis_ratio) or hysteresis_ratio <= 0.25)):
            grade = "good"
        elif fit.r_squared >= 0.65 and span_snr >= 3.0:
            grade = "usable"
        else:
            grade = "weak_or_unresolved"
        results.append({
            "input_dac": int(experiment["input_dac"]),
            "heater_net": experiment["heater_net"],
            "output_adc": channel,
            "absolute_amplitude_mv": absolute,
            "reference_corrected_amplitude_mv": corrected,
            "adc3_reference_amplitude_mv": reference,
            "voltage_v": voltage,
            "direction": direction,
            "forward_observed_span_mv": observed_span,
            "forward_point_uncertainty_mv": uncertainty,
            "forward_span_snr": span_snr,
            "reverse_hysteresis_rms_mv": hysteresis,
            "reverse_hysteresis_fraction_of_span": hysteresis_ratio,
            "quality_score": quality_score,
            "grade": grade,
            "fit": fit,
            "branch": branch,
            "lut_target_amplitude_mv": target_mv,
            "lut_heater_voltage_v": target_voltage_v,
            "experiment_dir": experiment["experiment_dir"],
        })
    primary = max(results, key=lambda result: result["quality_score"])
    for result in results:
        result["primary_for_input_heater"] = result is primary
        result["crosstalk_vs_primary_span"] = (
            result["forward_observed_span_mv"] /
            max(primary["forward_observed_span_mv"], 1.0e-12))
    return results


def _serializable_result(result):
    data = {
        key: value for key, value in result.items()
        if key not in (
            "absolute_amplitude_mv", "reference_corrected_amplitude_mv",
            "adc3_reference_amplitude_mv", "voltage_v", "direction",
            "fit", "branch", "lut_target_amplitude_mv",
            "lut_heater_voltage_v", "experiment_dir")
    }
    fit = asdict(result["fit"])
    fit.pop("predicted_mv")
    branch = asdict(result["branch"])
    branch.pop("voltage_v")
    branch.pop("amplitude_mv")
    data["fit"] = fit
    data["monotonic_branch"] = branch
    data["lookup_table"] = {
        "source": "measured forward monotonic branch",
        "target_amplitude_mv": result["lut_target_amplitude_mv"],
        "heater_voltage_v": result["lut_heater_voltage_v"],
    }
    data["source_experiment"] = str(result["experiment_dir"])
    return _jsonable(data)


def _write_summary_csv(path: Path, results):
    fields = [
        "heater_net", "input_dac", "output_adc", "primary_for_input_heater",
        "primary_for_element", "grade", "forward_observed_span_mv",
        "forward_point_uncertainty_mv", "forward_span_snr", "fit_r_squared",
        "fit_rmse_mv", "phase_slope_rad_per_v2", "modulation_mv",
        "reverse_hysteresis_rms_mv", "reverse_hysteresis_fraction_of_span",
        "crosstalk_vs_primary_span", "quality_score",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({
                "heater_net": result["heater_net"],
                "input_dac": result["input_dac"],
                "output_adc": result["output_adc"],
                "primary_for_input_heater": result["primary_for_input_heater"],
                "primary_for_element": result.get("primary_for_element", False),
                "grade": result["grade"],
                "forward_observed_span_mv": result["forward_observed_span_mv"],
                "forward_point_uncertainty_mv":
                    result["forward_point_uncertainty_mv"],
                "forward_span_snr": result["forward_span_snr"],
                "fit_r_squared": result["fit"].r_squared,
                "fit_rmse_mv": result["fit"].rmse_mv,
                "phase_slope_rad_per_v2":
                    result["fit"].phase_slope_rad_per_v2,
                "modulation_mv": result["fit"].modulation_mv,
                "reverse_hysteresis_rms_mv":
                    result["reverse_hysteresis_rms_mv"],
                "reverse_hysteresis_fraction_of_span":
                    result["reverse_hysteresis_fraction_of_span"],
                "crosstalk_vs_primary_span":
                    result["crosstalk_vs_primary_span"],
                "quality_score": result["quality_score"],
            })


def _write_lut_csv(path: Path, element_results):
    with path.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "heater_net", "input_dac", "output_adc", "level_index",
            "target_amplitude_mv", "heater_voltage_v", "grade",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in element_results:
            for index, (amplitude, voltage) in enumerate(zip(
                    result["lut_target_amplitude_mv"],
                    result["lut_heater_voltage_v"])):
                writer.writerow({
                    "heater_net": result["heater_net"],
                    "input_dac": result["input_dac"],
                    "output_adc": result["output_adc"],
                    "level_index": index,
                    "target_amplitude_mv": float(amplitude),
                    "heater_voltage_v": float(voltage),
                    "grade": result["grade"],
                })


def _plot_results(output_dir: Path, results, element_results, *, show=False):
    if not show:
        import matplotlib
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    grade_codes = {
        "weak_or_unresolved": 0,
        "usable": 1,
        "good": 2,
        "excellent": 3,
    }
    grade_grid = np.full((9, 6), np.nan)
    span_grid = np.full((9, 6), np.nan)
    labels = [["" for _ in range(6)] for _ in range(9)]
    for result in element_results:
        _h, row_text, column_text = result["heater_net"].split("_")
        row, column = int(row_text) - 1, int(column_text) - 1
        grade_grid[row, column] = grade_codes[result["grade"]]
        span_grid[row, column] = result["forward_observed_span_mv"]
        labels[row][column] = (
            f"D{result['input_dac']}->A{result['output_adc']}\n"
            f"{result['forward_observed_span_mv']:.3g} mV\n"
            f"R2 {result['fit'].r_squared:.2f}")
    figure, axes = plt.subplots(1, 2, figsize=(14, 8), constrained_layout=True)
    grade_image = axes[0].imshow(
        grade_grid, vmin=0, vmax=3, cmap="viridis", aspect="auto")
    axes[0].set_title("Best observable calibration per heater")
    span_image = axes[1].imshow(span_grid, cmap="magma", aspect="auto")
    axes[1].set_title("Best forward modulation span (mV)")
    for axis in axes:
        axis.set_xticks(range(6), range(1, 7))
        axis.set_yticks(range(9), range(1, 10))
        axis.set_xlabel("heater column")
        axis.set_ylabel("heater row")
    for row in range(9):
        for column in range(6):
            if labels[row][column]:
                axes[0].text(
                    column, row, labels[row][column], ha="center", va="center",
                    fontsize=6, color="white")
    figure.colorbar(
        grade_image, ax=axes[0], ticks=[0, 1, 2, 3],
        label="0 unresolved, 1 usable, 2 good, 3 excellent")
    figure.colorbar(span_image, ax=axes[1], label="mV")
    figure.savefig(output_dir / "mzi_calibration_overview.png", dpi=160)

    with PdfPages(output_dir / "mzi_calibration_curves.pdf") as pdf:
        grouped = {}
        for result in results:
            grouped.setdefault(
                (result["input_dac"], result["heater_net"]), []).append(result)
        for (input_dac, heater), group in sorted(grouped.items()):
            fig, axes = plt.subplots(
                3, 1, figsize=(10, 10), sharex=True, constrained_layout=True)
            fig.suptitle(f"{heater}, input DAC{input_dac}")
            for channel, (axis, result) in enumerate(zip(axes, group)):
                voltage = result["voltage_v"]
                direction = result["direction"]
                response = result["reference_corrected_amplitude_mv"]
                forward = direction == 0
                reverse = direction == 1
                axis.plot(
                    voltage[forward], response[forward], "o-", label="forward")
                if np.any(reverse):
                    axis.plot(
                        voltage[reverse], response[reverse], "s--",
                        label="reverse")
                vf = voltage[forward]
                order = np.argsort(vf)
                axis.plot(
                    vf[order], result["fit"].predicted_mv, color="black",
                    linewidth=1.3, label="cosine fit")
                branch = result["branch"]
                axis.plot(
                    branch.voltage_v, branch.amplitude_mv, color="gold",
                    linewidth=2.0, label="calibration branch")
                axis.set_ylabel(f"ADC{channel} amplitude (mV)")
                axis.grid(True, alpha=0.25)
                axis.legend(loc="best", fontsize=8)
                axis.set_title(
                    f"{result['grade']}; span "
                    f"{result['forward_observed_span_mv']:.4g} mV; "
                    f"R2={result['fit'].r_squared:.3f}")
            axes[-1].set_xlabel("heater drive (V)")
            pdf.savefig(fig)
            plt.close(fig)
    if show:
        plt.show()
    else:
        plt.close(figure)


def analyze_batches(batch_directories, output_dir: Path, *, max_fringes=4.0,
                    lut_levels=33, show=False):
    batches, experiments = load_batches(batch_directories)
    results = []
    for experiment in experiments:
        results.extend(analyze_experiment(
            experiment, max_fringes=max_fringes, lut_levels=lut_levels))
    by_heater = {}
    for result in results:
        by_heater.setdefault(result["heater_net"], []).append(result)
    element_results = []
    expected_heaters = tuple(
        net for dac in sorted(PHOTONIC_INPUT_ROW_BY_DAC)
        for net in calibration_heaters_for_dac(dac))
    for heater in expected_heaters:
        row = int(heater.split("_")[1])
        expected_dac = PHOTONIC_INPUT_DAC_BY_ROW[row]
        candidates = [
            result for result in by_heater.get(heater, [])
            if result["input_dac"] == expected_dac
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda result: result["quality_score"])
        best["primary_for_element"] = True
        element_results.append(best)
    for result in results:
        result.setdefault("primary_for_element", False)

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_summary_csv(output_dir / "mzi_calibration_summary.csv", results)
    _write_lut_csv(output_dir / "mzi_voltage_lut.csv", element_results)
    payload = {
        "schema": "daq_mzi_element_calibration",
        "schema_version": 1,
        "created_utc": utc_now(),
        "source_batches": batches,
        "input_dacs_present": sorted({item["input_dac"] for item in batches}),
        "complete_input_coverage":
            {item["input_dac"] for item in batches} >= {0, 1, 2},
        "physical_input_mapping": {
            f"DAC{dac}": f"photonic_row_{row}"
            for dac, row in PHOTONIC_INPUT_ROW_BY_DAC.items()
        },
        "expected_calibration_heaters": list(expected_heaters),
        "complete_element_coverage": (
            {result["heater_net"] for result in element_results} >=
            set(expected_heaters)),
        "reported_response": (
            "ADC3 drift-corrected fitted tone amplitude in mV; the raw "
            "absolute amplitudes remain in each source tone_analysis.json"),
        "reference_correction": (
            "ADC3 amplitude drift correction preserving median mV scale; "
            "no 0..1 normalization"),
        "model": "a + b*cos(k*V^2) + c*sin(k*V^2)",
        "elements": [_serializable_result(result) for result in element_results],
        "all_input_output_fits": [
            _serializable_result(result) for result in results],
    }
    write_json(output_dir / "mzi_calibration.json", payload)
    _plot_results(output_dir, results, element_results, show=show)
    return output_dir, payload


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "batches", nargs="+",
        help="one or more characterization batch directories")
    parser.add_argument(
        "--output",
        help="output directory (default: calibration_analysis beside first batch)")
    parser.add_argument("--max-fringes", type=float, default=4.0)
    parser.add_argument("--lut-levels", type=int, default=33)
    parser.add_argument(
        "--no-show", action="store_true",
        help="save plots without opening the summary popup")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    batches = [Path(item).expanduser().resolve() for item in args.batches]
    output = (
        Path(args.output).expanduser().resolve() if args.output else
        batches[0].parent /
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_mzi_calibration")
    try:
        output, payload = analyze_batches(
            batches, output, max_fringes=args.max_fringes,
            lut_levels=args.lut_levels, show=not args.no_show)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    grades = {}
    for element in payload["elements"]:
        grades[element["grade"]] = grades.get(element["grade"], 0) + 1
    print(f"Calibration written to {output}")
    print("Element grades: " + ", ".join(
        f"{grade}={count}" for grade, count in sorted(grades.items())))
    if not payload["complete_element_coverage"]:
        print(
            "WARNING: mapped DAC0/row7, DAC1/row8, and DAC2/row9 batches were "
            "not all supplied; the 18 driven MZIs are not fully calibrated.",
            file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
