#!/usr/bin/env python3
"""Process a named optical-sweep experiment directory into a weight curve."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from mzi_calibration import measure_spikes_at_indices, measure_triggered_spikes
from optical_experiment import load_manifest, update_manifest, utc_now, write_json


VOLTS_PER_COUNT = 1.9 / 65536.0


def _load_heater_capture(experiment_dir: Path, descriptor: dict, adc_channel: int):
    heater_dir = experiment_dir / descriptor["directory"]
    with np.load(heater_dir / "raw_captures.npz", allow_pickle=False) as data:
        raw = np.asarray(data[f"raw_ch{adc_channel}"], dtype=np.int16)
    return heater_dir, raw


def _write_heater_analysis(heater_dir: Path, measurement, descriptor: dict,
                          detected=None) -> None:
    amplitudes = measurement.per_peak_height.mean(axis=0)
    detected_count = 0 if detected is None else int(detected.peak_indices.size)
    np.savez_compressed(
        heater_dir / "processed.npz",
        artifact_kind="derived_optical_heater_capture_analysis",
        synthetic=np.bool_(False),
        averaged_waveform_v=measurement.averaged_waveform,
        per_rep_mean_amplitude_v=measurement.per_rep_height,
        per_rep_per_spike_amplitude_v=measurement.per_peak_height,
        spike_peak_indices=measurement.peak_indices,
        spike_start_indices=measurement.start_indices,
        spike_end_indices=measurement.end_indices,
        spike_polarities=measurement.polarities,
        spike_amplitudes_v=amplitudes,
        spike_widths_samples=measurement.widths_samples,
        spike_fwhm_samples=measurement.fwhm_samples,
        spike_areas_v_ns=measurement.areas_v_samples,
        independently_detected_spike_count=np.int32(detected_count),
        independently_detected_peak_indices=(
            np.empty(0, dtype=np.int32) if detected is None else detected.peak_indices),
        independently_detected_start_indices=(
            np.empty(0, dtype=np.int32) if detected is None else detected.start_indices),
        independently_detected_end_indices=(
            np.empty(0, dtype=np.int32) if detected is None else detected.end_indices),
        independently_detected_amplitudes_v=(
            np.empty(0, dtype=np.float64) if detected is None else
            detected.per_peak_height.mean(axis=0)),
    )
    with (heater_dir / "spike_measurements.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "spike", "start_sample", "peak_sample", "end_sample", "polarity",
            "amplitude_mv", "width_ns", "fwhm_ns", "area_mv_ns",
            "repetition_mean_mv", "repetition_std_mv",
        ])
        for spike in range(measurement.peak_indices.size):
            reps_mv = measurement.per_peak_height[:, spike] * 1e3
            writer.writerow([
                spike, int(measurement.start_indices[spike]),
                int(measurement.peak_indices[spike]),
                int(measurement.end_indices[spike]),
                int(measurement.polarities[spike]), amplitudes[spike] * 1e3,
                int(measurement.widths_samples[spike]),
                int(measurement.fwhm_samples[spike]),
                measurement.areas_v_samples[spike] * 1e3,
                float(np.mean(reps_mv)), float(np.std(reps_mv)),
            ])
    with (heater_dir / "independently_detected_spikes.csv").open(
            "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "spike", "start_sample", "peak_sample", "end_sample", "polarity",
            "amplitude_mv", "width_ns", "fwhm_ns", "area_mv_ns",
        ])
        if detected is not None:
            detected_amplitudes = detected.per_peak_height.mean(axis=0)
            for spike in range(detected.peak_indices.size):
                writer.writerow([
                    spike, int(detected.start_indices[spike]),
                    int(detected.peak_indices[spike]),
                    int(detected.end_indices[spike]),
                    int(detected.polarities[spike]),
                    detected_amplitudes[spike] * 1e3,
                    int(detected.widths_samples[spike]),
                    int(detected.fwhm_samples[spike]),
                    detected.areas_v_samples[spike] * 1e3,
                ])
    heater_meta_path = heater_dir / "heater.json"
    heater_meta = json.loads(heater_meta_path.read_text(encoding="utf-8"))
    heater_meta["analysis"] = {
        "processed_utc": utc_now(),
        "processed_file": "processed.npz",
        "measurements_file": "spike_measurements.csv",
        "spike_count": int(measurement.peak_indices.size),
        "signed_mean_amplitude_v": float(measurement.signed_height),
        "absolute_mean_amplitude_v": float(measurement.absolute_height),
        "reference_boundaries": True,
        "independently_detected_spike_count": detected_count,
        "independent_detection_file": "independently_detected_spikes.csv",
    }
    write_json(heater_meta_path, heater_meta)


def _write_curve_plot(path: Path, voltages: np.ndarray, directions: np.ndarray,
                      normalized: np.ndarray, absolute_v: np.ndarray) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(8.5, 7), sharex=True)
    styles = ((0, "forward", "#20A4F3"), (1, "reverse", "#F6AE2D"))
    for code, label, color in styles:
        selected = directions == code
        if np.any(selected):
            axes[0].plot(voltages[selected], normalized[selected], "o-",
                         color=color, linewidth=1.5, markersize=4, label=label)
            axes[1].plot(voltages[selected], absolute_v[selected] * 1e3, "o-",
                         color=color, linewidth=1.5, markersize=4, label=label)
    axes[0].set_ylabel("normalized optical weight")
    axes[0].set_ylim(-0.05, 1.05)
    axes[1].set_ylabel("mean spike amplitude (mV)")
    axes[1].set_xlabel("heater voltage (V)")
    for axis in axes:
        axis.grid(alpha=0.25)
        axis.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=170)
    plt.close(fig)


def process_experiment(experiment_dir: Path, *, make_plot: bool = True) -> dict:
    experiment_dir = Path(experiment_dir).expanduser().resolve()
    manifest = load_manifest(experiment_dir)
    heater_captures = sorted(manifest.get("heater_captures", []),
                             key=lambda item: item["index"])
    if not heater_captures:
        raise ValueError("experiment has no completed heater captures")
    acquisition = manifest["acquisition"]
    stimulus = manifest["stimulus"]
    detection = manifest["detection"]
    adc = int(acquisition["adc_channel"])
    # Periodic stimuli have no single programmed onset. Ignore only a short
    # capture guard, then detect spikes from the averaged output itself.
    step_sample = int(detection.get(
        "response_start_sample",
        stimulus.get("current_source", {}).get("step_sample", 64)))

    loaded = [_load_heater_capture(experiment_dir, descriptor, adc)
              for descriptor in heater_captures]
    metrics = []
    for _heater_dir, raw in loaded:
        volts = raw.astype(np.float64) * VOLTS_PER_COUNT
        baseline = np.median(volts[:, :max(4, step_sample - 10)], axis=1)
        average = (volts - baseline[:, None]).mean(axis=0)
        metrics.append(float(np.max(np.abs(average[step_sample:]))))
    reference_index = int(np.argmax(metrics))
    reference_raw = loaded[reference_index][1].astype(np.float64) * VOLTS_PER_COUNT
    reference = measure_triggered_spikes(
        reference_raw, step_sample,
        threshold_sigma=float(detection["threshold_sigma"]),
        boundary_sigma=float(detection["boundary_sigma"]),
        minimum_seed_samples=int(detection["minimum_seed_samples"]),
    )
    measurements = []
    for (heater_dir, raw), descriptor in zip(loaded, heater_captures):
        volts = raw.astype(np.float64) * VOLTS_PER_COUNT
        measurement = measure_spikes_at_indices(
            volts, step_sample,
            reference.peak_indices, start_indices=reference.start_indices,
            end_indices=reference.end_indices, polarities=reference.polarities)
        try:
            independently_detected = measure_triggered_spikes(
                volts, step_sample,
                threshold_sigma=float(detection["threshold_sigma"]),
                boundary_sigma=float(detection["boundary_sigma"]),
                minimum_seed_samples=int(detection["minimum_seed_samples"]),
            )
        except ValueError:
            independently_detected = None
        measurements.append(measurement)
        _write_heater_analysis(
            heater_dir, measurement, descriptor, independently_detected)

    signed = np.asarray([measurement.signed_height for measurement in measurements])
    absolute = np.abs(signed)
    minimum = float(np.min(absolute))
    maximum = float(np.max(absolute))
    span = maximum - minimum
    normalized = ((absolute - minimum) / span if span > np.finfo(float).eps
                  else np.zeros_like(absolute))
    voltages = np.asarray([capture["heater_voltage_v"] for capture in heater_captures])
    directions = np.asarray([
        1 if capture["direction"] == "reverse" else 0 for capture in heater_captures],
        dtype=np.int8)

    curve_path = experiment_dir / "optical_curve.csv"
    with curve_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "index", "heater_voltage_v", "direction", "signed_amplitude_mv",
            "absolute_amplitude_mv", "normalized_weight", "heater_directory",
        ])
        for index, capture in enumerate(heater_captures):
            writer.writerow([
                capture["index"], voltages[index], capture["direction"],
                signed[index] * 1e3, absolute[index] * 1e3, normalized[index],
                capture["directory"],
            ])
    plot_path = experiment_dir / "optical_curve.png"
    if make_plot:
        _write_curve_plot(plot_path, voltages, directions, normalized, absolute)

    extinction_db = float(10.0 * np.log10(
        max(maximum, np.finfo(float).tiny) /
        max(minimum, np.finfo(float).tiny)))
    summary = {
        "schema": "daq_optical_sweep_analysis",
        "schema_version": 1,
        "processed_utc": utc_now(),
        "experiment_directory": str(experiment_dir),
        "heater_capture_count": len(heater_captures),
        "reference_heater_index": int(heater_captures[reference_index]["index"]),
        "reference_heater_directory": heater_captures[reference_index]["directory"],
        "reference_spike_count": int(reference.peak_indices.size),
        "minimum_amplitude_v": minimum,
        "maximum_amplitude_v": maximum,
        "minimum_voltage_v": float(voltages[int(np.argmin(absolute))]),
        "extinction_db": extinction_db,
        "curve_csv": curve_path.name,
        "curve_plot": plot_path.name if make_plot else None,
    }
    write_json(experiment_dir / "analysis_summary.json", summary)
    update_manifest(experiment_dir, analysis=summary)
    return {
        "voltages": voltages, "directions": directions,
        "signed": signed, "absolute": absolute, "normalized": normalized,
        "min_height": minimum, "max_height": maximum,
        "min_voltage": summary["minimum_voltage_v"],
        "extinction_db": extinction_db,
        "reference_measurement": reference,
        "reference_heater_capture_index": reference_index,
        "path": str(experiment_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()
    result = process_experiment(args.experiment, make_plot=not args.no_plot)
    print(f"processed: {result['path']}")
    print(f"heater captures: {result['voltages'].size}")
    print(f"amplitude: {result['min_height'] * 1e3:.4f} to "
          f"{result['max_height'] * 1e3:.4f} mV")
    print(f"extinction: {result['extinction_db']:.3f} dB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
