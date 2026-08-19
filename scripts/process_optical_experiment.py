#!/usr/bin/env python3
"""Process a named optical-sweep experiment directory into a weight curve."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from mzi_calibration import (
    analyze_optical_peaks,
    dominant_spike_polarity,
    estimate_main_lobe_lag,
    measure_spikes_at_indices,
    measure_spikes_in_windows,
    measure_triggered_spikes,
    optical_schedule_from_loopback,
)
from optical_experiment import load_manifest, update_manifest, utc_now, write_json


VOLTS_PER_COUNT = 1.9 / 65536.0


def _load_heater_capture(experiment_dir: Path, descriptor: dict, adc_channel: int):
    heater_dir = experiment_dir / descriptor["directory"]
    with np.load(heater_dir / "raw_captures.npz", allow_pickle=False) as data:
        raw = np.asarray(data[f"raw_ch{adc_channel}"], dtype=np.int16)
    return heater_dir, raw


def _load_heater_channels(
    experiment_dir: Path,
    descriptor: dict,
    channels: tuple[int, ...],
):
    heater_dir = experiment_dir / descriptor["directory"]
    with np.load(heater_dir / "raw_captures.npz", allow_pickle=False) as data:
        raw = {
            channel: np.asarray(data[f"raw_ch{channel}"], dtype=np.int16)
            for channel in channels
        }
    return heater_dir, raw


def _write_heater_analysis(
    heater_dir: Path,
    measurement,
    descriptor: dict,
    detected=None,
    *,
    loopback_alignment=None,
    loopback_reference_average=None,
    reference_adc: int | None = None,
    optical_latency_samples: int | None = None,
    optical_correlation_score: float | None = None,
    adc_channel: int | None = None,
    primary_channel: bool = True,
) -> None:
    amplitudes = measurement.per_peak_height.mean(axis=0)
    detected_count = 0 if detected is None else int(detected.peak_indices.size)
    optical = analyze_optical_peaks(
        measurement, detected, polarity="auto",
        sigma_limit=2.5, filter_enabled=True)
    suffix = "" if primary_channel else f"_ch{int(adc_channel)}"
    processed_name = f"processed{suffix}.npz"
    measurements_name = f"spike_measurements{suffix}.csv"
    independent_name = f"independently_detected_spikes{suffix}.csv"
    np.savez_compressed(
        heater_dir / processed_name,
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
        loopback_reference_adc=np.int16(
            -1 if reference_adc is None else reference_adc),
        loopback_reference_template_v=(
            np.asarray(loopback_reference_average, dtype=np.float64)
            if loopback_reference_average is not None else
            (np.empty(0, dtype=np.float64) if loopback_alignment is None else
             loopback_alignment.template)),
        loopback_lag_samples=(
            np.empty(0, dtype=np.int32) if loopback_alignment is None else
            loopback_alignment.lag_samples),
        loopback_correlation_scores=(
            np.empty(0, dtype=np.float64) if loopback_alignment is None else
            loopback_alignment.correlation_scores),
        optical_latency_from_loopback_samples=np.int32(
            0 if optical_latency_samples is None else optical_latency_samples),
        optical_loopback_correlation_score=np.float64(
            np.nan if optical_correlation_score is None else
            optical_correlation_score),
    )
    with (heater_dir / measurements_name).open("w", newline="") as handle:
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
    with (heater_dir / independent_name).open(
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
    analysis_payload = {
        "processed_utc": utc_now(),
        "processed_file": processed_name,
        "adc_channel": adc_channel,
        "measurements_file": measurements_name,
        "spike_count": int(measurement.peak_indices.size),
        "signed_mean_amplitude_v": float(measurement.signed_height),
        "absolute_mean_amplitude_v": float(measurement.absolute_height),
        "reference_boundaries": True,
        "independently_detected_spike_count": detected_count,
        "independent_detection_file": independent_name,
        "optical_peak_polarity": optical.polarity,
        "optical_total_peak_count": int(optical.peak_indices.size),
        "optical_accepted_peak_count": int(np.count_nonzero(optical.accepted)),
        "optical_raw_mean_peak_amplitude_v": optical.raw_mean_v,
        "optical_filtered_mean_peak_amplitude_v": optical.filtered_mean_v,
        "optical_outlier_sigma": 2.5,
        "loopback_reference_adc": reference_adc,
        "loopback_lag_samples": (
            [] if loopback_alignment is None else
            loopback_alignment.lag_samples.tolist()),
        "loopback_correlation_scores": (
            [] if loopback_alignment is None else
            loopback_alignment.correlation_scores.tolist()),
        "optical_latency_from_loopback_samples": optical_latency_samples,
        "optical_loopback_correlation_score": optical_correlation_score,
    }
    if adc_channel is not None:
        heater_meta.setdefault("analysis_by_adc", {})[
            f"ADC{int(adc_channel)}"] = analysis_payload
    if primary_channel:
        heater_meta["analysis"] = analysis_payload
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

def _analyze_channel_measurements(
    measurements,
    independent_measurements,
    *,
    constant_voltage: bool,
):
    peak_analyses = [
        analyze_optical_peaks(
            measurement, detected, polarity="auto",
            sigma_limit=2.5, filter_enabled=True)
        for measurement, detected in zip(measurements, independent_measurements)
    ]
    if any(analysis.peak_indices.size == 0 for analysis in peak_analyses):
        raise ValueError("one or more heater captures contain no spike windows")
    raw = np.asarray([analysis.raw_mean_v for analysis in peak_analyses])
    filtered = np.asarray([
        analysis.filtered_mean_v for analysis in peak_analyses])

    def normalize(values):
        if constant_voltage:
            return np.full_like(values, np.nan)
        minimum = float(np.min(values))
        span = float(np.max(values) - minimum)
        return ((values - minimum) / span
                if span > np.finfo(float).eps else np.zeros_like(values))

    return {
        "measurements": measurements,
        "independent_measurements": independent_measurements,
        "peak_analyses": peak_analyses,
        "raw_peak_means_v": raw,
        "filtered_peak_means_v": filtered,
        "raw_normalized": normalize(raw),
        "normalized": normalize(filtered),
        "min_height": float(np.min(filtered)),
        "max_height": float(np.max(filtered)),
    }


def _write_channel_curve_csv(
    path: Path,
    heater_captures: list[dict],
    voltages: np.ndarray,
    channel_result: dict,
) -> None:
    raw = channel_result["raw_peak_means_v"]
    filtered = channel_result["filtered_peak_means_v"]
    analyses = channel_result["peak_analyses"]
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "index", "heater_voltage_v", "direction",
            "raw_mean_peak_amplitude_mv", "filtered_mean_peak_amplitude_mv",
            "raw_normalized_weight", "filtered_normalized_weight",
            "selected_polarity", "accepted_peaks", "total_peaks",
            "heater_directory",
        ])
        for index, capture in enumerate(heater_captures):
            analysis = analyses[index]
            writer.writerow([
                capture["index"], voltages[index], capture["direction"],
                raw[index] * 1e3, filtered[index] * 1e3,
                channel_result["raw_normalized"][index],
                channel_result["normalized"][index], analysis.polarity,
                int(np.count_nonzero(analysis.accepted)),
                int(analysis.peak_indices.size), capture["directory"],
            ])


def process_experiment(experiment_dir: Path, *, make_plot: bool = True) -> dict:
    experiment_dir = Path(experiment_dir).expanduser().resolve()
    manifest = load_manifest(experiment_dir)
    heater_captures = sorted(
        manifest.get("heater_captures", []), key=lambda item: item["index"])
    if not heater_captures:
        raise ValueError("experiment has no completed heater captures")

    acquisition = manifest["acquisition"]
    stimulus = manifest["stimulus"]
    detection = manifest["detection"]
    primary_adc = int(acquisition.get("adc_channel", 0))
    optical_adcs = tuple(int(channel) for channel in acquisition.get(
        "optical_adc_channels", [primary_adc]))
    step_sample = int(detection.get(
        "response_start_sample",
        stimulus.get("current_source", {}).get("step_sample", 64)))
    voltages = np.asarray([
        capture["heater_voltage_v"] for capture in heater_captures])
    directions = np.asarray([
        1 if capture["direction"] == "reverse" else 0
        for capture in heater_captures], dtype=np.int8)
    constant_voltage = bool(
        voltages.size and np.ptp(voltages) <= np.finfo(np.float64).eps)

    reference_value = acquisition.get("reference_adc_channel")
    reference_adc = None if reference_value is None else int(reference_value)
    loopback_enabled = reference_adc is not None
    alignments = []
    reference = None
    reference_averages = []
    reference_first_peak_latency_samples = None
    channel_results = {}

    if loopback_enabled:
        optical_adcs = tuple(
            channel for channel in optical_adcs if channel != reference_adc)
        if not optical_adcs:
            raise ValueError("no optical ADC channels are configured")
        max_optical_lag = int(acquisition.get(
            "loopback_max_optical_lag_samples", 1024))
        window_padding = int(acquisition.get(
            "loopback_window_padding_samples", 8))
        channels = tuple(dict.fromkeys((*optical_adcs, reference_adc)))
        loaded = [
            _load_heater_channels(experiment_dir, descriptor, channels)
            for descriptor in heater_captures
        ]
        stacks_by_adc = {channel: [] for channel in channels}
        baseline_end = max(4, step_sample - 10)
        for _heater_dir, raw_channels in loaded:
            for channel, values in raw_channels.items():
                repetitions = values.astype(np.float64) * VOLTS_PER_COUNT
                stacks_by_adc[channel].append(repetitions)
            reference_reps = stacks_by_adc[reference_adc][-1]
            baseline = np.median(
                reference_reps[:, :baseline_end], axis=1)
            reference_averages.append(
                (reference_reps - baseline[:, None]).mean(axis=0))

        # BCPT captures are already aligned by the hardware trigger. Pooling
        # the direct ADC3 repetitions only improves the clean reference SNR;
        # no repetition is shifted in software.
        reference_reps = np.concatenate(
            stacks_by_adc[reference_adc], axis=0)
        reference = measure_triggered_spikes(
            reference_reps, step_sample,
            threshold_sigma=float(detection["threshold_sigma"]),
            boundary_sigma=float(detection["boundary_sigma"]),
            minimum_seed_samples=int(detection["minimum_seed_samples"]),
        )
        reference_first_peak_latency_samples = int(
            reference.peak_indices[0] - step_sample)

        xbar = manifest.get("xbar", {})
        xbar_sources = xbar.get("sources_by_dac", {})
        invert_by_dac = xbar.get("invert_by_dac", {})
        reference_polarity = dominant_spike_polarity(reference)
        selected_reference_peaks = reference.peak_indices[
            reference.polarities == reference_polarity]
        channel_work = {}
        all_valid_lags = []
        for channel in optical_adcs:
            averages = []
            candidates = []
            repetitions_by_point = stacks_by_adc[channel]
            relative_polarity = (
                -1 if bool(invert_by_dac.get(f"DAC{channel}", False)) !=
                bool(invert_by_dac.get(f"DAC{reference_adc}", False)) else 1)
            for repetitions, reference_average in zip(
                    repetitions_by_point, reference_averages):
                baseline = np.median(
                    repetitions[:, :baseline_end], axis=1)
                average = (repetitions - baseline[:, None]).mean(axis=0)
                averages.append(average)
                try:
                    candidate = estimate_main_lobe_lag(
                        average, reference_average, max_optical_lag,
                        observed_polarity=reference_polarity * relative_polarity,
                        template_polarity=reference_polarity,
                        template_peak_indices=selected_reference_peaks)
                except ValueError:
                    candidate = None
                candidates.append(candidate)

            source = str(xbar_sources.get(f"DAC{channel}", "Spike 0"))
            stimulus_enabled = source != "Off"
            valid = [
                candidate for candidate in candidates
                if (stimulus_enabled and candidate is not None and
                    float(candidate.score) >= 0.10)
            ]
            valid_lags = [int(candidate.lag_samples) for candidate in valid]
            all_valid_lags.extend(valid_lags)
            channel_work[channel] = {
                "averages": averages,
                "candidates": candidates,
                "valid": valid,
                "valid_lags": valid_lags,
                "repetitions": repetitions_by_point,
                "source": source,
                "stimulus_enabled": stimulus_enabled,
                "relative_polarity": relative_polarity,
            }

        fallback_lag = (
            int(round(float(np.median(all_valid_lags))))
            if all_valid_lags else 0)
        for channel in optical_adcs:
            work = channel_work[channel]
            valid = work["valid"]
            latency = (
                int(round(float(np.median(work["valid_lags"]))))
                if work["valid_lags"] else fallback_lag)
            valid_scores = [float(candidate.score) for candidate in valid]
            correlation_score = (
                float(np.median(valid_scores)) if valid_scores else 0.0)
            nominal, starts, ends, signs = optical_schedule_from_loopback(
                reference, work["averages"][0], latency,
                padding_samples=window_padding,
                response_polarity=work["relative_polarity"])
            measurements = []
            independent = [None] * len(heater_captures)
            for point_index, (
                    (heater_dir, _raw), descriptor, repetitions
            ) in enumerate(zip(
                    loaded, heater_captures, work["repetitions"])):
                measurement = measure_spikes_in_windows(
                    repetitions, step_sample, nominal,
                    start_indices=starts, end_indices=ends,
                    polarities=signs)
                measurements.append(measurement)
                candidate = work["candidates"][point_index]
                point_score = (
                    None if candidate is None else float(candidate.score))
                _write_heater_analysis(
                    heater_dir, measurement, descriptor, None,
                    loopback_reference_average=
                        reference_averages[point_index],
                    reference_adc=reference_adc,
                    optical_latency_samples=latency,
                    optical_correlation_score=point_score,
                    adc_channel=channel,
                    primary_channel=(channel == primary_adc))
            result = _analyze_channel_measurements(
                measurements, independent,
                constant_voltage=constant_voltage)
            result.update({
                "adc_channel": channel,
                "stimulus_enabled": bool(work["stimulus_enabled"]),
                "source": work["source"],
                "reference_heater_capture_index": None,
                "optical_latency_samples": latency,
                "optical_correlation_score": correlation_score,
                "optical_correlation_valid": bool(valid),
                "point_correlation_scores": [
                    None if candidate is None else float(candidate.score)
                    for candidate in work["candidates"]
                ],
            })
            channel_results[channel] = result
    else:
        optical_adcs = (primary_adc,)
        loaded = [
            _load_heater_capture(experiment_dir, descriptor, primary_adc)
            for descriptor in heater_captures
        ]
        metrics = []
        for _heater_dir, raw in loaded:
            volts = raw.astype(np.float64) * VOLTS_PER_COUNT
            baseline = np.median(
                volts[:, :max(4, step_sample - 10)], axis=1)
            average = (volts - baseline[:, None]).mean(axis=0)
            metrics.append(float(np.max(np.abs(average[step_sample:]))))
        reference_index = int(np.argmax(metrics))
        reference_raw = (
            loaded[reference_index][1].astype(np.float64) * VOLTS_PER_COUNT)
        reference = measure_triggered_spikes(
            reference_raw, step_sample,
            threshold_sigma=float(detection["threshold_sigma"]),
            boundary_sigma=float(detection["boundary_sigma"]),
            minimum_seed_samples=int(detection["minimum_seed_samples"]),
        )
        measurements = []
        independent = []
        for (heater_dir, raw), descriptor in zip(loaded, heater_captures):
            volts = raw.astype(np.float64) * VOLTS_PER_COUNT
            measurement = measure_spikes_at_indices(
                volts, step_sample, reference.peak_indices,
                start_indices=reference.start_indices,
                end_indices=reference.end_indices,
                polarities=reference.polarities)
            try:
                detected = measure_triggered_spikes(
                    volts, step_sample,
                    threshold_sigma=float(detection["threshold_sigma"]),
                    boundary_sigma=float(detection["boundary_sigma"]),
                    minimum_seed_samples=int(detection["minimum_seed_samples"]),
                )
            except ValueError:
                detected = None
            measurements.append(measurement)
            independent.append(detected)
            _write_heater_analysis(
                heater_dir, measurement, descriptor, detected,
                adc_channel=primary_adc, primary_channel=True)
        result = _analyze_channel_measurements(
            measurements, independent, constant_voltage=constant_voltage)
        result.update({
            "adc_channel": primary_adc,
            "stimulus_enabled": True,
            "source": "legacy",
            "reference_heater_capture_index": reference_index,
            "optical_latency_samples": 0,
            "optical_correlation_score": None,
            "optical_correlation_valid": False,
        })
        channel_results[primary_adc] = result

    if primary_adc not in channel_results:
        primary_adc = optical_adcs[0]
    primary = channel_results[primary_adc]

    channel_summaries = {}
    for channel, result in channel_results.items():
        channel_path = experiment_dir / f"optical_curve_ch{channel}.csv"
        _write_channel_curve_csv(
            channel_path, heater_captures, voltages, result)
        plot_name = f"optical_curve_ch{channel}.png"
        if make_plot:
            _write_curve_plot(
                experiment_dir / plot_name, voltages, directions,
                result["normalized"], result["filtered_peak_means_v"],
                result["raw_normalized"], result["raw_peak_means_v"])
        minimum = result["min_height"]
        maximum = result["max_height"]
        extinction_db = float(10.0 * np.log10(
            max(maximum, np.finfo(float).tiny) /
            max(minimum, np.finfo(float).tiny)))
        result["extinction_db"] = extinction_db
        result["min_voltage"] = float(
            voltages[int(np.argmin(result["filtered_peak_means_v"]))])
        channel_summaries[f"ADC{channel}"] = {
            "adc_channel": channel,
            "dac_source": result["source"],
            "stimulus_enabled": result["stimulus_enabled"],
            "optical_latency_from_loopback_samples":
                result["optical_latency_samples"],
            "optical_loopback_correlation_score":
                result["optical_correlation_score"],
            "optical_correlation_valid": result["optical_correlation_valid"],
            "minimum_amplitude_v": minimum,
            "maximum_amplitude_v": maximum,
            "extinction_db": extinction_db,
            "curve_csv": channel_path.name,
            "curve_plot": plot_name if make_plot else None,
        }

    primary_curve = experiment_dir / "optical_curve.csv"
    _write_channel_curve_csv(
        primary_curve, heater_captures, voltages, primary)
    primary_plot = experiment_dir / "optical_curve.png"
    if make_plot:
        _write_curve_plot(
            primary_plot, voltages, directions, primary["normalized"],
            primary["filtered_peak_means_v"], primary["raw_normalized"],
            primary["raw_peak_means_v"])

    summary = {
        "schema": "daq_optical_sweep_analysis",
        "schema_version": 2,
        "processed_utc": utc_now(),
        "experiment_directory": str(experiment_dir),
        "heater_capture_count": len(heater_captures),
        "primary_optical_adc": primary_adc,
        "optical_adc_channels": list(channel_results),
        "channels_averaged_independently": True,
        "channels": channel_summaries,
        "reference_spike_count": int(reference.peak_indices.size),
        "timing_source": (
            f"ADC{reference_adc} and optical triggered-average cross-correlation"
            if loopback_enabled else "strongest optical heater capture"),
        "loopback_reference_adc": reference_adc,
        "reference_first_peak_latency_samples":
            reference_first_peak_latency_samples,
        "loopback_alignment_applied": False,
        "trigger_aligned_repetitions_averaged_without_shifting": loopback_enabled,
        "constant_voltage_control": constant_voltage,
        "normalization_is_transfer_curve": not constant_voltage,
        "curve_csv": primary_curve.name,
        "curve_plot": primary_plot.name if make_plot else None,
    }
    write_json(experiment_dir / "analysis_summary.json", summary)
    update_manifest(experiment_dir, analysis=summary)

    return {
        "voltages": voltages,
        "directions": directions,
        "signed": primary["filtered_peak_means_v"].copy(),
        "absolute": primary["filtered_peak_means_v"].copy(),
        "normalized": primary["normalized"],
        "min_height": primary["min_height"],
        "max_height": primary["max_height"],
        "min_voltage": primary["min_voltage"],
        "extinction_db": primary["extinction_db"],
        "constant_voltage_control": constant_voltage,
        "repeatability_std_v": float(np.std(
            primary["filtered_peak_means_v"])),
        "repeatability_peak_to_peak_v": float(np.ptp(
            primary["filtered_peak_means_v"])),
        "reference_measurement": reference,
        "reference_averages": reference_averages,
        "measurements": primary["measurements"],
        "independent_measurements": primary["independent_measurements"],
        "raw_peak_means_v": primary["raw_peak_means_v"],
        "filtered_peak_means_v": primary["filtered_peak_means_v"],
        "raw_normalized": primary["raw_normalized"],
        "peak_analyses": primary["peak_analyses"],
        "channel_results": channel_results,
        "primary_optical_adc": primary_adc,
        "heater_captures": heater_captures,
        "reference_heater_capture_index":
            primary["reference_heater_capture_index"],
        "loopback_reference_adc": reference_adc,
        "loopback_alignments": alignments,
        "reference_first_peak_latency_samples":
            reference_first_peak_latency_samples,
        "optical_latency_samples": primary["optical_latency_samples"],
        "optical_correlation_score": primary["optical_correlation_score"],
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
