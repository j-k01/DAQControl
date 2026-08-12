#!/usr/bin/env python3
"""Pure-NumPy analysis for the isolated two-neuron loopback test."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np


@dataclass(frozen=True)
class SyntheticChannel:
    baseline_v: np.ndarray
    clean_v: np.ndarray
    synthetic_v: np.ndarray
    clean_average_v: np.ndarray
    average_v: np.ndarray
    scale_gain: float
    latency_samples: int


@dataclass(frozen=True)
class SpikeMeasurements:
    peak_indices: np.ndarray
    start_indices: np.ndarray
    end_indices: np.ndarray
    polarities: np.ndarray
    baselines_v: np.ndarray
    signed_amplitudes_v: np.ndarray
    absolute_amplitudes_v: np.ndarray
    widths_samples: np.ndarray
    fwhm_samples: np.ndarray
    areas_v_samples: np.ndarray
    boundary_thresholds_v: np.ndarray
    per_rep_amplitudes_v: np.ndarray


@dataclass(frozen=True)
class SpikeDetection:
    indices: np.ndarray
    detection_signal: np.ndarray
    threshold: float
    polarity: int
    noise_sigma: float
    measurements: SpikeMeasurements | None = None


def _delay_zero_padded(values: np.ndarray, samples: int) -> np.ndarray:
    delayed = np.zeros_like(values)
    if samples == 0:
        delayed[:] = values
    elif samples > 0 and samples < values.shape[1]:
        delayed[:, samples:] = values[:, :-samples]
    elif samples < 0 and -samples < values.shape[1]:
        delayed[:, :samples] = values[:, -samples:]
    return delayed


def source_peak_v(repetitions_v: np.ndarray, step_sample: int) -> float:
    """Return the baseline-subtracted peak of the clean fixed-index average."""

    reps = np.asarray(repetitions_v, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 2 or reps.shape[1] < 64:
        raise ValueError("repetitions_v must have shape [repetitions, samples]")
    step = int(step_sample)
    if step < 8 or step >= reps.shape[1] - 8:
        raise ValueError("step_sample must leave baseline and response regions")
    baseline = np.median(reps[:, :max(4, step - 10)], axis=1)
    average = (reps - baseline[:, None]).mean(axis=0)
    return float(np.max(np.abs(average[step:])))


def validate_source_channels(stacks_v: Mapping[int, np.ndarray], *,
                             step_sample: int,
                             minimum_peak_v: float) -> dict[int, float]:
    peaks = {channel: source_peak_v(stack, step_sample)
             for channel, stack in stacks_v.items()}
    weak = {channel: peak for channel, peak in peaks.items()
            if peak < float(minimum_peak_v)}
    if weak:
        detail = ", ".join(f"ADC{channel}={peak * 1e3:.3f} mV"
                           for channel, peak in weak.items())
        raise ValueError(f"source-signal preflight failed: {detail}")
    return peaks


def synthesize_integrated_channel(
    repetitions_v: np.ndarray,
    *,
    step_sample: int,
    target_peak_v: float = 0.015,
    noise_rms_v: float = 0.005,
    latency_samples: int = 0,
    rng: np.random.Generator | None = None,
) -> SyntheticChannel:
    """Scale one complete stack, delay it, and add independent Gaussian noise.

    Baselines are removed per repetition. One channel-wide gain is calculated
    from the clean 16-shot average, preserving all repetition-to-repetition
    amplitude variation. The input array is never modified.
    """

    reps = np.asarray(repetitions_v, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 2 or reps.shape[1] < 64:
        raise ValueError("repetitions_v must have shape [repetitions, samples]")
    step = int(step_sample)
    if step < 8 or step >= reps.shape[1] - 8:
        raise ValueError("step_sample must leave baseline and response regions")
    target = float(target_peak_v)
    noise = float(noise_rms_v)
    if not np.isfinite(target) or target <= 0.0:
        raise ValueError("target_peak_v must be positive and finite")
    if not np.isfinite(noise) or noise < 0.0:
        raise ValueError("noise_rms_v must be non-negative and finite")

    baseline_end = max(4, step - 10)
    baseline = np.median(reps[:, :baseline_end], axis=1)
    centered = reps - baseline[:, None]
    delayed = _delay_zero_padded(centered, int(latency_samples))
    response_start = max(0, min(reps.shape[1] - 1, step + int(latency_samples)))
    clean_average = delayed.mean(axis=0)
    peak = float(np.max(np.abs(clean_average[response_start:])))
    if peak <= np.finfo(np.float64).eps:
        raise ValueError("clean capture has no spike response to scale")
    gain = target / peak
    clean = delayed * gain
    clean_average = clean.mean(axis=0)
    generator = rng if rng is not None else np.random.default_rng()
    synthetic = clean + generator.normal(0.0, noise, clean.shape)
    return SyntheticChannel(
        baseline_v=baseline,
        clean_v=clean,
        synthetic_v=synthetic,
        clean_average_v=clean_average,
        average_v=synthetic.mean(axis=0),
        scale_gain=gain,
        latency_samples=int(latency_samples),
    )


def _robust_sigma(values: np.ndarray) -> float:
    median = float(np.median(values))
    return 1.4826 * float(np.median(np.abs(values - median)))


def _smooth_trace(values: np.ndarray, samples: int) -> np.ndarray:
    width = max(1, int(samples))
    if width == 1:
        return values.copy()
    kernel = np.full(width, 1.0 / width, dtype=np.float64)
    return np.convolve(values, kernel, mode="same")


def _event_extent(signal: np.ndarray, peak: int, threshold: float,
                  quiet_samples: int, lower: int, upper: int) -> tuple[int, int]:
    quiet_needed = max(1, int(quiet_samples))
    left = peak
    quiet = 0
    for index in range(peak, lower - 1, -1):
        if signal[index] >= threshold:
            left = index
            quiet = 0
        else:
            quiet += 1
            if quiet >= quiet_needed:
                break
    right = peak
    quiet = 0
    for index in range(peak, upper):
        if signal[index] >= threshold:
            right = index
            quiet = 0
        else:
            quiet += 1
            if quiet >= quiet_needed:
                break
    return left, right


def detect_spikes_output(
    average_v: np.ndarray,
    *,
    response_start: int,
    repetitions_v: np.ndarray | None = None,
    threshold_sigma: float = 5.0,
    boundary_sigma: float = 2.0,
    boundary_quiet_samples: int = 3,
    smooth_samples: int = 1,
    minimum_amplitude_v: float = 0.0,
    minimum_seed_samples: int = 2,
    minimum_width_samples: int = 1,
) -> SpikeDetection:
    """Detect and measure excursions using only the observed output waveform.

    No expected spike template, period, width, or polarity is used. A robust
    pre-response noise estimate sets a high detection threshold. Each detected
    excursion is then extended to a lower hysteresis threshold to infer its own
    boundaries. Events that never return below that threshold are necessarily
    reported as one event because the waveform contains no separable boundary.
    """

    trace = np.asarray(average_v, dtype=np.float64)
    if trace.ndim != 1 or trace.size < 64:
        raise ValueError("average_v must be a one-dimensional trace")
    start = int(response_start)
    if start < 8 or start >= trace.size - 8:
        raise ValueError("response_start must leave baseline and response regions")
    reps = (trace[None, :] if repetitions_v is None else
            np.asarray(repetitions_v, dtype=np.float64))
    if reps.ndim != 2 or reps.shape[1] != trace.size:
        raise ValueError("repetitions_v must have shape [repetitions, samples]")
    if float(threshold_sigma) <= 0.0 or float(boundary_sigma) < 0.0:
        raise ValueError("threshold_sigma must be positive and boundary_sigma non-negative")

    baseline_end = max(4, start - 8)
    smooth = _smooth_trace(trace, smooth_samples)
    baseline = float(np.median(smooth[:baseline_end]))
    centered = smooth - baseline
    noise_sigma = _robust_sigma(centered[:baseline_end])
    numerical_floor = np.finfo(np.float64).eps * max(1.0, float(np.max(np.abs(trace))))
    high_threshold = max(float(threshold_sigma) * noise_sigma,
                         float(minimum_amplitude_v), numerical_floor)
    low_threshold = max(float(boundary_sigma) * noise_sigma, numerical_floor)

    seed_mask = np.abs(centered) >= high_threshold
    seed_mask[:start] = False
    transitions = np.diff(np.pad(seed_mask.astype(np.int8), (1, 1)))
    run_starts = np.flatnonzero(transitions == 1)
    run_ends = np.flatnonzero(transitions == -1)
    candidates = [
        int(begin + np.argmax(np.abs(centered[begin:end])))
        for begin, end in zip(run_starts, run_ends)
        if end - begin >= max(1, int(minimum_seed_samples))
    ]

    raw_events: list[tuple[int, int, int, int]] = []
    for peak in candidates:
        polarity = 1 if centered[peak] >= 0.0 else -1
        signed = polarity * centered
        event_start, event_end = _event_extent(
            signed, peak, low_threshold, boundary_quiet_samples,
            start, trace.size)
        if event_end - event_start + 1 >= max(1, int(minimum_width_samples)):
            raw_events.append((event_start, event_end, peak, polarity))

    events: list[tuple[int, int, int, int]] = []
    for event_start, event_end, peak, polarity in raw_events:
        if events and event_start <= events[-1][1]:
            old_start, old_end, old_peak, old_polarity = events[-1]
            if abs(centered[peak]) > abs(centered[old_peak]):
                old_peak, old_polarity = peak, polarity
            events[-1] = (old_start, max(old_end, event_end),
                          old_peak, old_polarity)
        else:
            events.append((event_start, event_end, peak, polarity))

    count = len(events)
    peaks = np.empty(count, dtype=np.int32)
    starts = np.empty(count, dtype=np.int32)
    ends = np.empty(count, dtype=np.int32)
    polarities = np.empty(count, dtype=np.int8)
    baselines = np.full(count, baseline, dtype=np.float64)
    amplitudes = np.empty(count, dtype=np.float64)
    widths = np.empty(count, dtype=np.int32)
    fwhm = np.empty(count, dtype=np.int32)
    areas = np.empty(count, dtype=np.float64)
    thresholds = np.full(count, low_threshold, dtype=np.float64)
    per_rep = np.empty((reps.shape[0], count), dtype=np.float64)
    rep_baselines = np.median(reps[:, :baseline_end], axis=1)

    for event, (event_start, event_end, peak, polarity) in enumerate(events):
        signed = polarity * centered
        amplitude = float(trace[peak] - baseline)
        half_start, half_end = _event_extent(
            signed, peak, 0.5 * abs(float(centered[peak])), 1,
            event_start, event_end + 1)
        peaks[event] = peak
        starts[event] = event_start
        ends[event] = event_end
        polarities[event] = polarity
        amplitudes[event] = amplitude
        widths[event] = event_end - event_start + 1
        fwhm[event] = half_end - half_start + 1
        areas[event] = float(np.sum(
            polarity * (trace[event_start:event_end + 1] - baseline)))
        for repetition in range(reps.shape[0]):
            per_rep[repetition, event] = (
                reps[repetition, peak] - rep_baselines[repetition])

    measurements = SpikeMeasurements(
        peak_indices=peaks, start_indices=starts, end_indices=ends,
        polarities=polarities, baselines_v=baselines,
        signed_amplitudes_v=amplitudes,
        absolute_amplitudes_v=np.abs(amplitudes), widths_samples=widths,
        fwhm_samples=fwhm, areas_v_samples=areas,
        boundary_thresholds_v=thresholds,
        per_rep_amplitudes_v=per_rep)
    common_polarity = (int(polarities[0]) if count and
                       np.all(polarities == polarities[0]) else 0)
    return SpikeDetection(
        indices=peaks.copy(), detection_signal=centered,
        threshold=high_threshold, polarity=common_polarity,
        noise_sigma=noise_sigma, measurements=measurements)


def analyze_stacks(
    stacks_v: Mapping[int, np.ndarray],
    *,
    step_sample: int,
    target_peak_v: float,
    noise_rms_v: float,
    latency_samples: Mapping[int, int] | None = None,
    seed: int = 1,
    threshold_sigma: float = 5.0,
    boundary_sigma: float = 2.0,
    boundary_quiet_samples: int = 3,
    smooth_samples: int = 1,
    minimum_seed_samples: int = 2,
    minimum_width_samples: int = 1,
) -> tuple[dict[int, SyntheticChannel], dict[int, SpikeDetection]]:
    """Synthesize and detect every selected ADC channel reproducibly."""

    latencies = dict(latency_samples or {})
    root_rng = np.random.default_rng(int(seed))
    synthetic: dict[int, SyntheticChannel] = {}
    detections: dict[int, SpikeDetection] = {}
    for channel in sorted(stacks_v):
        child_seed = int(root_rng.integers(0, np.iinfo(np.uint32).max))
        latency = int(latencies.get(channel, 0))
        item = synthesize_integrated_channel(
            stacks_v[channel], step_sample=step_sample,
            target_peak_v=target_peak_v, noise_rms_v=noise_rms_v,
            latency_samples=latency, rng=np.random.default_rng(child_seed))
        synthetic[channel] = item
        detections[channel] = detect_spikes_output(
            item.average_v, response_start=step_sample + latency,
            repetitions_v=item.synthetic_v,
            threshold_sigma=threshold_sigma, boundary_sigma=boundary_sigma,
            boundary_quiet_samples=boundary_quiet_samples,
            smooth_samples=smooth_samples,
            minimum_seed_samples=minimum_seed_samples,
            minimum_width_samples=minimum_width_samples)
    return synthetic, detections
