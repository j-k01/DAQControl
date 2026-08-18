#!/usr/bin/env python3
"""Process a named optical-sweep experiment directory into a weight curve."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from mzi_calibration import (
    analyze_optical_peaks, measure_spikes_at_indices, measure_triggered_spikes,
)
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
    optical = analyze_optical_peaks(
        measurement, detected, polarity="auto",
        sigma_limit=2.5, filter_enabled=True)
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
        optical_peak_indices=optical.peak_indices,
        optical_peak_waveform_values_v=optical.waveform_values_v,
        optical_peak_amplitudes_v=optical.amplitudes_v,
        optical_peak_accepted=optical.accepted,
        optical_peak_polarity=optical.polarity,
        optical_raw_mean_peak_amplitude_v=np.float64(optical.raw_mean_v),
        optical_filtered_mean_peak_amplitude_v=np.float64(
            optical.filtered_mean_v),
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
        "optical_peak_polarity": optical.polarity,
        "optical_total_peak_count": int(optical.peak_indices.size),
        "optical_accepted_peak_count": int(np.count_nonzero(optical.accepted)),
        "optical_raw_mean_peak_amplitude_v": optical.raw_mean_v,
        "optical_filtered_mean_peak_amplitude_v": optical.filtered_mean_v,
        "optical_outlier_sigma": 2.5,
    }
    write_json(heater_meta_path, heater_meta)


def _write_curve_plot(
    path: Path,
    voltages: np.ndarray,
    directions: np.ndarray,
    normalized: np.ndarray,
    filtered_v: np.ndarray,
    raw_normalized: np.ndarray,
    raw_v: np.ndarray,
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(8.5, 7), sharex=True)
    constant_voltage = bool(
        voltages.size and np.ptp(voltages) <= np.finfo(np.float64).eps)
    if constant_voltage:
        capture_index = np.arange(filtered_v.size)
        raw_mv = raw_v * 1e3
        filtered_mv = filtered_v * 1e3
        mean_mv = float(np.mean(filtered_mv))
        axes[0].plot(
            capture_index, raw_mv, "o--", color="#90A4AE",
            linewidth=1.1, markersize=3, label="unfiltered")
        axes[0].plot(
            capture_index, filtered_mv, "o-", color="#20A4F3",
            linewidth=1.5, markersize=4, label="filtered")
        axes[0].axhline(
            mean_mv, color="#6B7785", linestyle=":", linewidth=1.2,
            label=f"filtered mean {mean_mv:.3f} mV")
        axes[1].plot(
            capture_index, filtered_mv - mean_mv, "o-", color="#F6AE2D",
            linewidth=1.5, markersize=4, label="filtered deviation")
        axes[1].axhline(0.0, color="#6B7785", linewidth=1.0)
        axes[0].set_ylabel("mean peak amplitude (mV)")
        axes[1].set_ylabel("amplitude deviation (mV)")
        axes[1].set_xlabel(
            f"capture index (heater held at {float(voltages[0]):.4f} V)")
    else:
        styles = ((0, "forward", "#20A4F3"), (1, "reverse", "#F6AE2D"))
        for code, label, color in styles:
            selected = directions == code
            if not np.any(selected):
                continue
            axes[0].plot(
                voltages[selected], raw_normalized[selected], "o--",
                color="#90A4AE", linewidth=1.0, markersize=3,
                label=f"unfiltered {label}")
            axes[0].plot(
                voltages[selected], normalized[selected], "o-",
                color=color, linewidth=1.5, markersize=4,
                label=f"filtered {label}")
            axes[1].plot(
                voltages[selected], raw_v[selected] * 1e3, "o--",
                color="#90A4AE", linewidth=1.0, markersize=3,
                label=f"unfiltered {label}")
            axes[1].plot(
                voltages[selected], filtered_v[selected] * 1e3, "o-",
                color=color, linewidth=1.5, markersize=4,
                label=f"filtered {label}")
        axes[0].set_ylabel("normalized optical weight")
        axes[0].set_ylim(-0.05, 1.05)
        axes[1].set_ylabel("mean peak amplitude (mV)")
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
    independent_measurements = []
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
        independent_measurements.append(independently_detected)
        _write_heater_analysis(
            heater_dir, measurement, descriptor, independently_detected)

    peak_analyses = [
        analyze_optical_peaks(
            measurement, detected, polarity="auto",
            sigma_limit=2.5, filter_enabled=True)
        for measurement, detected in zip(measurements, independent_measurements)
    ]
    if any(analysis.peak_indices.size == 0 for analysis in peak_analyses):
        raise ValueError(
            "one or more heater captures contain no detectable spike peaks")
    raw_peak_means = np.asarray([
        analysis.raw_mean_v for analysis in peak_analyses])
    filtered_peak_means = np.asarray([
        analysis.filtered_mean_v for analysis in peak_analyses])
    # Historical result names are retained for GUI/import compatibility.
    signed = filtered_peak_means.copy()
    absolute = filtered_peak_means.copy()
    minimum = float(np.min(absolute))
    maximum = float(np.max(absolute))
    span = maximum - minimum
    voltages = np.asarray([
        capture["heater_voltage_v"] for capture in heater_captures])
    constant_voltage = bool(
        voltages.size and np.ptp(voltages) <= np.finfo(np.float64).eps)
    normalized = (
        np.full_like(absolute, np.nan) if constant_voltage else
        ((absolute - minimum) / span if span > np.finfo(float).eps
         else np.zeros_like(absolute)))
    raw_minimum = float(np.min(raw_peak_means))
    raw_span = float(np.max(raw_peak_means) - raw_minimum)
    raw_normalized = (
        np.full_like(raw_peak_means, np.nan) if constant_voltage else
        ((raw_peak_means - raw_minimum) / raw_span
         if raw_span > np.finfo(float).eps
         else np.zeros_like(raw_peak_means)))
    directions = np.asarray([
        1 if capture["direction"] == "reverse" else 0 for capture in heater_captures],
        dtype=np.int8)

    curve_path = experiment_dir / "optical_curve.csv"
    with curve_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "index", "heater_voltage_v", "direction",
            "signed_amplitude_mv", "absolute_amplitude_mv",
            "normalized_weight",
            "raw_mean_peak_amplitude_mv", "filtered_mean_peak_amplitude_mv",
            "raw_normalized_weight", "filtered_normalized_weight",
            "selected_polarity", "accepted_peaks", "total_peaks",
            "heater_directory",
        ])
        for index, capture in enumerate(heater_captures):
            analysis = peak_analyses[index]
            writer.writerow([
                capture["index"], voltages[index], capture["direction"],
                filtered_peak_means[index] * 1e3,
                filtered_peak_means[index] * 1e3, normalized[index],
                raw_peak_means[index] * 1e3,
                filtered_peak_means[index] * 1e3,
                raw_normalized[index], normalized[index],
                analysis.polarity, int(np.count_nonzero(analysis.accepted)),
                int(analysis.peak_indices.size), capture["directory"],
            ])
    plot_path = experiment_dir / "optical_curve.png"
    if make_plot:
        _write_curve_plot(
            plot_path, voltages, directions, normalized, absolute,
            raw_normalized, raw_peak_means)

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
        "amplitude_metric": "sigma_clipped_mean_dominant_polarity_peak_amplitude",
        "default_peak_polarity": "auto",
        "default_outlier_sigma": 2.5,
        "minimum_amplitude_v": minimum,
        "maximum_amplitude_v": maximum,
        "minimum_voltage_v": float(voltages[int(np.argmin(absolute))]),
        "extinction_db": extinction_db,
        "constant_voltage_control": constant_voltage,
        "normalization_is_transfer_curve": not constant_voltage,
        "repeatability_standard_deviation_v": float(np.std(absolute)),
        "repeatability_peak_to_peak_v": float(np.ptp(absolute)),
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
        "constant_voltage_control": constant_voltage,
        "repeatability_std_v": summary["repeatability_standard_deviation_v"],
        "repeatability_peak_to_peak_v": summary["repeatability_peak_to_peak_v"],
        "reference_measurement": reference,
        "measurements": measurements,
        "independent_measurements": independent_measurements,
        "raw_peak_means_v": raw_peak_means,
        "filtered_peak_means_v": filtered_peak_means,
        "raw_normalized": raw_normalized,
        "peak_analyses": peak_analyses,
        "heater_captures": heater_captures,
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
