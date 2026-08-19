"""MZI weight-curve calibration helpers shared by the Qt GUI and tests."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import re
import sys
import time

import numpy as np
from mzi_heater_map import (
    HEATER_MAX_V, HEATER_MIN_V, validate_heater_voltage,
    validate_requested_heater_voltages,
)
from spike_test_analysis import detect_spikes_output


@dataclass(frozen=True)
class PulseTrainMeasurement:
    signed_height: float
    absolute_height: float
    per_rep_height: np.ndarray
    phase_offsets: np.ndarray
    aligned_average: np.ndarray


@dataclass(frozen=True)
class TriggeredSpikeMeasurement:
    signed_height: float
    absolute_height: float
    per_rep_height: np.ndarray
    per_peak_height: np.ndarray
    peak_indices: np.ndarray
    baseline_levels: np.ndarray
    averaged_waveform: np.ndarray
    start_indices: np.ndarray
    end_indices: np.ndarray
    polarities: np.ndarray
    widths_samples: np.ndarray
    fwhm_samples: np.ndarray
    areas_v_samples: np.ndarray
    detection_threshold_v: float
    boundary_thresholds_v: np.ndarray
    noise_sigma_v: float


@dataclass(frozen=True)
class CorrelationLag:
    lag_samples: int
    score: float


@dataclass(frozen=True)
class LoopbackAlignment:
    reference_channel: int
    template: np.ndarray
    lag_samples: np.ndarray
    correlation_scores: np.ndarray
    aligned_stacks: dict[int, np.ndarray]


@dataclass(frozen=True)
class LoopbackReferencedMeasurement:
    reference: TriggeredSpikeMeasurement
    optical: TriggeredSpikeMeasurement
    alignment: LoopbackAlignment
    optical_latency_samples: int
    optical_correlation_score: float


@dataclass(frozen=True)
class OpticalPeakAnalysis:
    peak_indices: np.ndarray
    waveform_values_v: np.ndarray
    amplitudes_v: np.ndarray
    accepted: np.ndarray
    polarity: str
    source: str
    raw_mean_v: float
    filtered_mean_v: float
    standard_deviation_v: float


def _sigma_clip_peak_amplitudes(
    amplitudes: np.ndarray,
    *,
    sigma_limit: float,
    enabled: bool,
) -> np.ndarray:
    accepted = np.ones(amplitudes.size, dtype=bool)
    if not enabled or amplitudes.size < 3:
        return accepted
    limit = max(0.1, float(sigma_limit))
    for _iteration in range(8):
        selected = amplitudes[accepted]
        if selected.size < 3:
            break
        center = float(np.mean(selected))
        deviation = float(np.std(selected))
        if deviation <= np.finfo(np.float64).eps:
            break
        updated = np.abs(amplitudes - center) <= limit * deviation
        if np.count_nonzero(updated) < 2 or np.array_equal(updated, accepted):
            break
        accepted = updated
    return accepted


def analyze_optical_peaks(
    measurement: TriggeredSpikeMeasurement,
    detected: TriggeredSpikeMeasurement | None = None,
    *,
    polarity: str = "auto",
    sigma_limit: float = 2.5,
    filter_enabled: bool = True,
) -> OpticalPeakAnalysis:
    """Select the optical pulse polarity and optionally reject height outliers."""

    requested = str(polarity).strip().lower()
    if requested not in {"auto", "positive", "negative"}:
        raise ValueError("polarity must be auto, positive, or negative")

    candidates = []
    if detected is not None:
        candidates.append((detected, "detected"))
    candidates.append((measurement, "reference fallback"))

    selected = None
    for source_measurement, source_name in candidates:
        waveform = np.asarray(
            source_measurement.averaged_waveform, dtype=np.float64)
        peaks = np.asarray(source_measurement.peak_indices, dtype=np.int32)
        signs = np.asarray(source_measurement.polarities, dtype=np.int8)
        in_bounds = (peaks >= 0) & (peaks < waveform.size)
        values = np.zeros(peaks.size, dtype=np.float64)
        values[in_bounds] = waveform[peaks[in_bounds]]
        finite = in_bounds & np.isfinite(values)
        nonzero = finite & (values != 0.0)
        if requested == "auto" and np.any(finite) and not np.any(nonzero):
            selected = (
                peaks[finite], values[finite], np.abs(values[finite]),
                "positive", f"{source_name} (zero control)")
            break
        finite = nonzero

        chosen = requested
        if chosen == "auto":
            scores = {}
            for label, sign in (("positive", 1), ("negative", -1)):
                subset = np.abs(values[finite & (signs == sign)])
                scores[label] = (
                    float(np.median(subset)) if subset.size else -1.0)
            chosen = ("negative" if scores["negative"] >= scores["positive"]
                      else "positive")
        sign = 1 if chosen == "positive" else -1
        keep = finite & (signs == sign)
        if np.any(keep):
            selected = (
                peaks[keep], values[keep], np.abs(values[keep]),
                chosen, source_name)
            break

    if selected is None:
        return OpticalPeakAnalysis(
            peak_indices=np.empty(0, dtype=np.int32),
            waveform_values_v=np.empty(0, dtype=np.float64),
            amplitudes_v=np.empty(0, dtype=np.float64),
            accepted=np.empty(0, dtype=bool),
            polarity=requested,
            source="none",
            raw_mean_v=float("nan"),
            filtered_mean_v=float("nan"),
            standard_deviation_v=float("nan"),
        )

    peaks, values, amplitudes, chosen, source_name = selected
    accepted = _sigma_clip_peak_amplitudes(
        amplitudes, sigma_limit=sigma_limit, enabled=filter_enabled)
    raw_mean = float(np.mean(amplitudes))
    filtered_mean = float(np.mean(amplitudes[accepted]))
    return OpticalPeakAnalysis(
        peak_indices=peaks,
        waveform_values_v=values,
        amplitudes_v=amplitudes,
        accepted=accepted,
        polarity=chosen,
        source=source_name,
        raw_mean_v=raw_mean,
        filtered_mean_v=filtered_mean,
        standard_deviation_v=float(np.std(amplitudes)),
    )


def positive_average_peaks(
    measurement: TriggeredSpikeMeasurement,
) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility wrapper returning positive averaged-waveform peaks."""

    analysis = analyze_optical_peaks(
        measurement, polarity="positive", filter_enabled=False)
    return analysis.peak_indices, analysis.amplitudes_v


def select_positive_average_peaks(
    measurement: TriggeredSpikeMeasurement,
    detected: TriggeredSpikeMeasurement | None = None,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Compatibility wrapper for the historical positive-only selection."""

    analysis = analyze_optical_peaks(
        measurement, detected, polarity="positive", filter_enabled=False)
    return analysis.peak_indices, analysis.amplitudes_v, analysis.source


def _phase_average(trace: np.ndarray, period: int) -> np.ndarray:
    phase = np.arange(trace.size) % period
    sums = np.bincount(phase, weights=trace, minlength=period)
    counts = np.bincount(phase, minlength=period)
    return sums / np.maximum(counts, 1)


def measure_periodic_pulses(
    repetitions: np.ndarray,
    period_samples: int,
    high_samples: int,
) -> PulseTrainMeasurement:
    """Align periodic pulse captures and measure high-minus-baseline height."""

    reps = np.asarray(repetitions, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 1 or reps.shape[1] < 4:
        raise ValueError("repetitions must have shape [N, samples]")
    period = int(period_samples)
    high = int(high_samples)
    if period < 4 or period > reps.shape[1]:
        raise ValueError("period_samples must be between 4 and capture length")
    if high < 1 or high >= period:
        raise ValueError("high_samples must be between 1 and period-1")

    template = np.zeros(period, dtype=np.float64)
    template[:high] = 1.0
    template -= template.mean()
    template_fft = np.fft.rfft(template)

    offsets = np.empty(reps.shape[0], dtype=np.int32)
    heights = np.empty(reps.shape[0], dtype=np.float64)
    aligned = np.empty_like(reps)
    guard = min(max(1, high // 8), max(1, (period - high) // 4))
    low_phase = np.arange(period) >= min(period - 1, high + guard)
    low_phase &= np.arange(period) < max(high + 1, period - guard)
    if not np.any(low_phase):
        low_phase = np.arange(period) >= high

    for index, trace in enumerate(reps):
        folded = _phase_average(trace, period)
        folded -= np.median(folded)
        correlation = np.fft.irfft(
            np.fft.rfft(folded) * np.conj(template_fft), n=period
        )
        offset = int(np.argmax(np.abs(correlation)))
        offsets[index] = offset
        aligned[index] = np.roll(trace, -offset)

        phase_trace = _phase_average(aligned[index], period)
        high_level = np.median(phase_trace[:high])
        low_level = np.median(phase_trace[low_phase])
        heights[index] = high_level - low_level

    signed = float(np.mean(heights))
    return PulseTrainMeasurement(
        signed_height=signed,
        absolute_height=abs(signed),
        per_rep_height=heights,
        phase_offsets=offsets,
        aligned_average=aligned.mean(axis=0),
    )


def parse_heater_voltages(text: str) -> np.ndarray:
    """Parse a separated list within the photonic-heater safety range."""

    tokens = [token for token in re.split(r"[,;\s]+", str(text).strip()) if token]
    if len(tokens) < 2:
        raise ValueError("enter at least two heater voltages")
    try:
        values = np.asarray([float(token) for token in tokens], dtype=np.float64)
    except ValueError as exc:
        raise ValueError("heater voltage list contains a non-number") from exc
    if not np.all(np.isfinite(values)):
        raise ValueError("heater voltages must be finite")
    if np.any(values < HEATER_MIN_V) or np.any(values > HEATER_MAX_V):
        raise ValueError(
            f"heater voltages must be between {HEATER_MIN_V:g} and "
            f"{HEATER_MAX_V:g} V")
    return values


def estimate_correlation_lag(
    observed: np.ndarray,
    template: np.ndarray,
    max_lag: int,
    *,
    allow_inversion: bool = False,
) -> CorrelationLag:
    """Return the delay of observed relative to template.

    Positive lag means that the observed signal arrives later. Correlation is
    linear (zero padded), so samples never wrap across capture boundaries.
    """

    signal = np.asarray(observed, dtype=np.float64)
    reference = np.asarray(template, dtype=np.float64)
    if signal.ndim != 1 or reference.ndim != 1:
        raise ValueError("observed and template must be one-dimensional")
    if signal.size != reference.size or signal.size < 16:
        raise ValueError("observed and template must have the same length >= 16")
    limit = int(max_lag)
    if limit < 0:
        raise ValueError("max_lag must be non-negative")
    limit = min(limit, signal.size - 2)
    signal = signal - np.median(signal)
    reference = reference - np.median(reference)
    signal_norm = float(np.linalg.norm(signal))
    reference_norm = float(np.linalg.norm(reference))
    if signal_norm <= np.finfo(np.float64).eps:
        raise ValueError("observed signal has no correlation energy")
    if reference_norm <= np.finfo(np.float64).eps:
        raise ValueError("template has no correlation energy")

    linear_length = signal.size + reference.size - 1
    fft_length = 1 << (linear_length - 1).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(signal, fft_length) *
        np.conj(np.fft.rfft(reference, fft_length)),
        fft_length)
    lags = np.arange(-limit, limit + 1, dtype=np.int32)
    indices = np.where(lags >= 0, lags, fft_length + lags)
    scores = correlation[indices] / (signal_norm * reference_norm)
    selected = int(np.argmax(np.abs(scores) if allow_inversion else scores))
    return CorrelationLag(
        lag_samples=int(lags[selected]), score=float(scores[selected]))


def _shift_without_wrap(trace: np.ndarray, lag_samples: int) -> np.ndarray:
    """Remove a measured delay without wrapping capture-edge samples."""

    values = np.asarray(trace)
    lag = int(lag_samples)
    if values.ndim != 1:
        raise ValueError("trace must be one-dimensional")
    if abs(lag) >= values.size:
        raise ValueError("lag must be shorter than the trace")
    output = np.empty_like(values)
    guard = max(1, min(values.size, 64))
    fill = np.median(values[:guard])
    if lag > 0:
        output[:-lag] = values[lag:]
        output[-lag:] = fill
    elif lag < 0:
        advance = -lag
        output[advance:] = values[:-advance]
        output[:advance] = fill
    else:
        output[:] = values
    return output


def align_stacks_to_loopback(
    stacks: dict[int, np.ndarray],
    reference_channel: int,
    *,
    template: np.ndarray | None = None,
    max_lag: int = 256,
    minimum_median_score: float = 0.50,
) -> LoopbackAlignment:
    """Align every ADC stack using only the simultaneous loopback channel."""

    channels = {
        int(channel): np.asarray(values)
        for channel, values in stacks.items()
    }
    reference_channel = int(reference_channel)
    if reference_channel not in channels:
        raise ValueError(f"reference ADC{reference_channel} is missing")
    reference_reps = channels[reference_channel]
    if reference_reps.ndim != 2 or reference_reps.shape[0] < 1:
        raise ValueError("loopback reference must have shape [N, samples]")
    if any(values.shape != reference_reps.shape for values in channels.values()):
        raise ValueError("all ADC stacks must have the same shape")

    if template is None:
        # The clean loopback's first repetition cannot be blurred away by a
        # large initial jitter spread. A second pass refines this seed from the
        # aligned mean of every repetition.
        working_template = reference_reps[0].astype(np.float64)
    else:
        working_template = np.asarray(template, dtype=np.float64)
        if working_template.shape != (reference_reps.shape[1],):
            raise ValueError("loopback template length does not match captures")

    def estimate(reference_template):
        results = [
            estimate_correlation_lag(rep, reference_template, max_lag)
            for rep in reference_reps
        ]
        return (
            np.asarray([item.lag_samples for item in results], dtype=np.int32),
            np.asarray([item.score for item in results], dtype=np.float64),
        )

    lags, scores = estimate(working_template)
    if template is None:
        rough = np.stack([
            _shift_without_wrap(rep, lag)
            for rep, lag in zip(reference_reps, lags)
        ]).astype(np.float64)
        working_template = np.mean(rough, axis=0)
        lags, scores = estimate(working_template)

    median_score = float(np.median(np.abs(scores)))
    if median_score < float(minimum_median_score):
        raise ValueError(
            "ADC3 loopback correlation is too weak "
            f"(median |corr|={median_score:.3f}); check the DAC3-to-ADC3 cable "
            "and confirm Spike 0 is visible on ADC3")

    aligned = {
        channel: np.stack([
            _shift_without_wrap(rep, lag)
            for rep, lag in zip(values, lags)
        ])
        for channel, values in channels.items()
    }
    return LoopbackAlignment(
        reference_channel=reference_channel,
        template=working_template.copy(),
        lag_samples=lags,
        correlation_scores=scores,
        aligned_stacks=aligned,
    )


def optical_schedule_from_loopback(
    reference: TriggeredSpikeMeasurement,
    optical_average: np.ndarray,
    optical_latency_samples: int,
    *,
    padding_samples: int = 8,
    response_polarity: int = 1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Translate the ADC3 event schedule to fixed optical windows.

    The optical waveform is used only to clip windows to the capture length.
    It is deliberately not searched for events or local extrema here.
    """

    waveform = np.asarray(optical_average, dtype=np.float64)
    lag = int(optical_latency_samples)
    padding = max(0, int(padding_samples))
    starts = reference.start_indices.astype(np.int64) + lag - padding
    ends = reference.end_indices.astype(np.int64) + lag + padding
    nominal_peaks = reference.peak_indices.astype(np.int64) + lag
    valid = ((ends >= 0) & (starts < waveform.size) &
             (nominal_peaks >= 0) & (nominal_peaks < waveform.size))
    starts = np.clip(starts[valid], 0, waveform.size - 1).astype(np.int32)
    ends = np.clip(ends[valid], 0, waveform.size - 1).astype(np.int32)
    peaks = nominal_peaks[valid].astype(np.int32)
    direction = -1 if int(response_polarity) < 0 else 1
    signs = reference.polarities[valid].astype(np.int8) * direction
    return peaks, starts, ends, signs


def measure_spikes_with_loopback(
    optical_repetitions: np.ndarray,
    loopback_repetitions: np.ndarray,
    step_sample: int,
    *,
    reference_channel: int = 3,
    template: np.ndarray | None = None,
    max_repetition_lag: int = 256,
    max_optical_lag: int = 1024,
    window_padding_samples: int = 8,
    threshold_sigma: float = 5.0,
    boundary_sigma: float = 2.0,
    minimum_seed_samples: int = 2,
) -> LoopbackReferencedMeasurement:
    """Measure one channel from trigger-aligned optical and ADC3 captures.

    Hardware triggering defines the repetition alignment. Software therefore
    averages each stack at its original sample indices and uses correlation
    only between the two averages to measure optical path latency.
    """

    del template, max_repetition_lag
    reference_reps = np.asarray(loopback_repetitions, dtype=np.float64)
    optical_reps = np.asarray(optical_repetitions, dtype=np.float64)
    if reference_reps.shape != optical_reps.shape:
        raise ValueError("optical and loopback repetitions must have equal shape")
    reference = measure_triggered_spikes(
        reference_reps, step_sample, threshold_sigma=threshold_sigma,
        boundary_sigma=boundary_sigma,
        minimum_seed_samples=minimum_seed_samples)
    baseline_end = max(4, int(step_sample) - 10)
    optical_baselines = np.median(optical_reps[:, :baseline_end], axis=1)
    optical_average = (
        optical_reps - optical_baselines[:, None]).mean(axis=0)
    latency = estimate_correlation_lag(
        optical_average, reference.averaged_waveform, max_optical_lag,
        allow_inversion=True)
    peaks, starts, ends, signs = optical_schedule_from_loopback(
        reference, optical_average, latency.lag_samples,
        padding_samples=window_padding_samples,
        response_polarity=(-1 if latency.score < 0 else 1))
    if peaks.size == 0:
        raise ValueError("loopback spike schedule does not overlap optical capture")
    optical = measure_spikes_in_windows(
        optical_reps, step_sample, peaks, start_indices=starts,
        end_indices=ends, polarities=signs)
    alignment = LoopbackAlignment(
        reference_channel=int(reference_channel),
        template=reference.averaged_waveform.copy(),
        lag_samples=np.zeros(reference_reps.shape[0], dtype=np.int32),
        correlation_scores=np.full(reference_reps.shape[0], np.nan),
        aligned_stacks={
            0: optical_reps.copy(),
            int(reference_channel): reference_reps.copy(),
        },
    )
    return LoopbackReferencedMeasurement(
        reference=reference, optical=optical, alignment=alignment,
        optical_latency_samples=latency.lag_samples,
        optical_correlation_score=latency.score,
    )

def measure_triggered_spikes(
    repetitions: np.ndarray,
    step_sample: int,
    *,
    threshold_sigma: float = 5.0,
    boundary_sigma: float = 2.0,
    boundary_quiet_samples: int = 3,
    minimum_seed_samples: int = 2,
    minimum_width_samples: int = 1,
    smooth_samples: int = 1,
) -> TriggeredSpikeMeasurement:
    """Measure repeatable neuron-spike pulses from hardware-aligned captures.

    Each repetition is baseline-subtracted independently. Peaks are detected
    once on the fixed-index average, then every repetition is measured at the
    same indices; no software time shifting is applied.
    """

    reps = np.asarray(repetitions, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 1 or reps.shape[1] < 16:
        raise ValueError("repetitions must have shape [N, samples]")
    step = int(step_sample)
    if step < 8 or step >= reps.shape[1] - 4:
        raise ValueError("step_sample must leave baseline and response regions")
    baseline_end = max(4, step - 10)
    baseline_levels = np.median(reps[:, :baseline_end], axis=1)
    centered = reps - baseline_levels[:, None]
    averaged = centered.mean(axis=0)
    detection = detect_spikes_output(
        averaged, response_start=step, repetitions_v=centered,
        threshold_sigma=threshold_sigma, boundary_sigma=boundary_sigma,
        boundary_quiet_samples=boundary_quiet_samples,
        minimum_seed_samples=minimum_seed_samples,
        minimum_width_samples=minimum_width_samples,
        smooth_samples=smooth_samples)
    measured = detection.measurements
    if measured.peak_indices.size == 0:
        raise ValueError("no triggered spikes exceeded the detection threshold")
    peak_indices = measured.peak_indices
    per_peak = measured.per_rep_amplitudes_v
    per_rep = per_peak.mean(axis=1)
    signed = float(np.mean(per_rep))
    return TriggeredSpikeMeasurement(
        signed_height=signed,
        absolute_height=abs(signed),
        per_rep_height=per_rep,
        per_peak_height=per_peak,
        peak_indices=peak_indices,
        baseline_levels=baseline_levels,
        averaged_waveform=averaged,
        start_indices=measured.start_indices,
        end_indices=measured.end_indices,
        polarities=measured.polarities,
        widths_samples=measured.widths_samples,
        fwhm_samples=measured.fwhm_samples,
        areas_v_samples=measured.areas_v_samples,
        detection_threshold_v=detection.threshold,
        boundary_thresholds_v=measured.boundary_thresholds_v,
        noise_sigma_v=detection.noise_sigma,
    )

def measure_spikes_at_indices(
    repetitions: np.ndarray,
    step_sample: int,
    peak_indices: np.ndarray,
    *,
    start_indices: np.ndarray | None = None,
    end_indices: np.ndarray | None = None,
    polarities: np.ndarray | None = None,
) -> TriggeredSpikeMeasurement:
    """Measure every capture at one shared set of hardware spike indices.

    This is used after detecting the spike schedule at the strongest optical
    sweep point. It intentionally accepts zero-response traces at extinction.
    """

    reps = np.asarray(repetitions, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 1 or reps.shape[1] < 16:
        raise ValueError("repetitions must have shape [N, samples]")
    step = int(step_sample)
    if step < 8 or step >= reps.shape[1] - 4:
        raise ValueError("step_sample must leave baseline and response regions")
    peaks = np.asarray(peak_indices, dtype=np.int32)
    if peaks.ndim != 1 or peaks.size < 1:
        raise ValueError("peak_indices must contain at least one sample")
    if np.any(peaks < step) or np.any(peaks >= reps.shape[1]):
        raise ValueError("peak_indices must lie in the response region")

    baseline_end = max(4, step - 10)
    baseline_levels = np.median(reps[:, :baseline_end], axis=1)
    centered = reps - baseline_levels[:, None]
    averaged = centered.mean(axis=0)
    starts = peaks.copy() if start_indices is None else np.asarray(start_indices, dtype=np.int32)
    ends = peaks.copy() if end_indices is None else np.asarray(end_indices, dtype=np.int32)
    signs = (np.sign(averaged[peaks]).astype(np.int8) if polarities is None else
             np.asarray(polarities, dtype=np.int8))
    if starts.shape != peaks.shape or ends.shape != peaks.shape or signs.shape != peaks.shape:
        raise ValueError("boundary and polarity arrays must match peak_indices")
    if np.any(starts > peaks) or np.any(ends < peaks):
        raise ValueError("each fixed peak must lie inside its boundary")
    # Measure each event inside its reference boundary rather than at one exact
    # sample. This preserves the hardware-aligned data while avoiding a large
    # amplitude error when a narrow pulse peak moves by a sample inside the
    # same detected event window.
    per_peak = np.empty((centered.shape[0], peaks.size), dtype=np.float64)
    for index, (start, end, polarity) in enumerate(zip(starts, ends, signs)):
        window = centered[:, int(start):int(end) + 1]
        if int(polarity) < 0:
            per_peak[:, index] = np.min(window, axis=1)
        else:
            per_peak[:, index] = np.max(window, axis=1)
    per_rep = per_peak.mean(axis=1)
    signed = float(np.mean(per_rep))
    widths = ends - starts + 1
    areas = np.asarray([
        np.sum(int(signs[index]) * averaged[starts[index]:ends[index] + 1])
        for index in range(peaks.size)], dtype=np.float64)
    baseline_average = averaged[:baseline_end]
    median = float(np.median(baseline_average))
    noise = 1.4826 * float(np.median(np.abs(baseline_average - median)))
    return TriggeredSpikeMeasurement(
        signed_height=signed,
        absolute_height=abs(signed),
        per_rep_height=per_rep,
        per_peak_height=per_peak,
        peak_indices=peaks.copy(),
        baseline_levels=baseline_levels,
        averaged_waveform=averaged,
        start_indices=starts.copy(),
        end_indices=ends.copy(),
        polarities=signs.copy(),
        widths_samples=widths,
        fwhm_samples=np.zeros(peaks.size, dtype=np.int32),
        areas_v_samples=areas,
        detection_threshold_v=float("nan"),
        boundary_thresholds_v=np.full(peaks.size, np.nan),
        noise_sigma_v=noise,
    )

def measure_spikes_in_windows(
    repetitions: np.ndarray,
    step_sample: int,
    peak_indices: np.ndarray,
    *,
    start_indices: np.ndarray,
    end_indices: np.ndarray,
    polarities: np.ndarray,
) -> TriggeredSpikeMeasurement:
    """Take one expected-polarity extremum per known event after averaging.

    Repetitions are never shifted. The arithmetic average is formed first,
    then ADC3-derived event windows are searched only for their positive or
    negative extremum. This avoids both independent optical spike detection
    and the noise-maximum bias caused by maximizing every raw repetition.
    """

    reps = np.asarray(repetitions, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 1 or reps.shape[1] < 16:
        raise ValueError("repetitions must have shape [N, samples]")
    step = int(step_sample)
    if step < 8 or step >= reps.shape[1] - 4:
        raise ValueError("step_sample must leave baseline and response regions")
    nominal = np.asarray(peak_indices, dtype=np.int32)
    starts = np.asarray(start_indices, dtype=np.int32)
    ends = np.asarray(end_indices, dtype=np.int32)
    signs = np.asarray(polarities, dtype=np.int8)
    if nominal.ndim != 1 or nominal.size < 1:
        raise ValueError("peak_indices must contain at least one sample")
    if (starts.shape != nominal.shape or ends.shape != nominal.shape or
            signs.shape != nominal.shape):
        raise ValueError("boundary and polarity arrays must match peak_indices")
    if (np.any(starts < step) or np.any(ends >= reps.shape[1]) or
            np.any(starts > ends)):
        raise ValueError("event windows must lie in the response region")
    if np.any(signs == 0):
        raise ValueError("event polarities must be non-zero")

    baseline_end = max(4, step - 10)
    baseline_levels = np.median(reps[:, :baseline_end], axis=1)
    centered = reps - baseline_levels[:, None]
    averaged = centered.mean(axis=0)
    peaks = np.empty(nominal.size, dtype=np.int32)
    for index, (start, end, sign) in enumerate(zip(starts, ends, signs)):
        window = averaged[int(start):int(end) + 1]
        local = int(np.argmin(window) if int(sign) < 0 else np.argmax(window))
        peaks[index] = int(start) + local

    # Sample every raw repetition at the peak selected from the average. Its
    # column mean is exactly the corresponding averaged-trace peak.
    per_peak = centered[:, peaks]
    per_rep = per_peak.mean(axis=1)
    signed = float(np.mean(averaged[peaks]))
    widths = ends - starts + 1
    areas = np.asarray([
        np.sum(int(signs[index]) * averaged[starts[index]:ends[index] + 1])
        for index in range(peaks.size)], dtype=np.float64)
    baseline_average = averaged[:baseline_end]
    median = float(np.median(baseline_average))
    noise = 1.4826 * float(np.median(np.abs(baseline_average - median)))
    return TriggeredSpikeMeasurement(
        signed_height=signed,
        absolute_height=float(np.mean(np.abs(averaged[peaks]))),
        per_rep_height=per_rep,
        per_peak_height=per_peak,
        peak_indices=peaks,
        baseline_levels=baseline_levels,
        averaged_waveform=averaged,
        start_indices=starts.copy(),
        end_indices=ends.copy(),
        polarities=signs.copy(),
        widths_samples=widths,
        fwhm_samples=np.zeros(peaks.size, dtype=np.int32),
        areas_v_samples=areas,
        detection_threshold_v=float("nan"),
        boundary_thresholds_v=np.full(peaks.size, np.nan),
        noise_sigma_v=noise,
    )

def probe_fpga_pico_bridge(
    board_ip: str = "192.168.2.10",
    local_ip: str = "192.168.2.1",
    *,
    timeout: float = 0.5,
    serial_factory=None,
) -> None:
    """Verify that the Linux Pico bridge answers without touching the Pico."""

    if serial_factory is None:
        from fpga_pico_serial import Serial as serial_factory
    try:
        with serial_factory(
            transport="ethernet",
            board_ip=board_ip,
            local_ip=local_ip,
            timeout=timeout,
            write_timeout=timeout,
        ) as bridge:
            bridge.reset_input_buffer()
    except OSError as exc:
        raise RuntimeError(
            f"FPGA Pico bridge is not responding at {board_ip}:5007. "
            f"ADC capture on {board_ip}:5006 is a separate service and can "
            "still work. The ordinary program_board.ps1/program_board.tcl "
            "runtime does not provide the USB host or Pico bridge. Load the "
            "unified runtime with uv run python pico_usb\\load_and_test.py "
            "--local-jtag --port COM9, then retry."
        ) from exc


class PydaqMziController:
    """Lazy connection to the FPGA-attached PICO-002 through unmodified PyDAQ."""

    def __init__(self) -> None:
        self._installation = None
        self._config = None

    @property
    def connected(self) -> bool:
        return self._config is not None

    def connect(
        self,
        *,
        board_ip: str = "192.168.2.10",
        local_ip: str = "192.168.2.1",
    ) -> None:
        if self.connected:
            return
        module_path = Path(__file__).resolve()
        for search_path in (str(module_path.parent), str(module_path.parents[1])):
            if search_path not in sys.path:
                sys.path.insert(0, search_path)
        from pydaq_fpga_transport import install

        probe_fpga_pico_bridge(board_ip, local_ip)
        installation = install(
            board_ip=board_ip,
            local_ip=local_ip,
            transport="ethernet",
        )
        try:
            module_name = "mzi_pydaq_config"
            if module_name in sys.modules:
                config = importlib.reload(sys.modules[module_name])
            else:
                config = importlib.import_module(module_name)
        except Exception as exc:
            installation.uninstall(close_connections=True)
            message = str(exc)
            endpoint = f"{board_ip}:{installation.backend.config.udp_port}"
            if "No serial DAQ boards found" in message:
                raise RuntimeError(
                    f"The FPGA Pico bridge answered at {endpoint}, but "
                    "PICO-002 did not answer the PyDAQ HANDSHAKE. The bridge "
                    "service is running; check the Pico USB cable and confirm "
                    "the directly connected Pico still returns UID:PICO-002."
                ) from exc
            if "Board PICO-002 not found" in message:
                raise RuntimeError(
                    f"The FPGA bridge at {endpoint} returned a Pico UID other "
                    f"than PICO-002. Program the expected PICO-002 firmware."
                ) from exc
            raise
        self._installation = installation
        self._config = config

    def available_nets(self) -> tuple[str, ...]:
        if not self.connected:
            raise RuntimeError("PyDAQ MZI controller is not connected")
        return tuple(self._config.MZI_NET_NAMES)

    def test_connection(self, probes: int = 5) -> dict[str, object]:
        """Exercise the live Pico CDC path without changing DAC outputs."""

        if not self.connected:
            raise RuntimeError("PyDAQ MZI controller is not connected")
        serial_port = self._config.pico.serial
        durations_ms = []
        for _ in range(max(1, int(probes))):
            started = time.monotonic()
            serial_port.reset_input_buffer()
            serial_port.write(b"HANDSHAKE\n")
            uid = serial_port.readline().decode(
                "ascii", errors="replace").strip()
            # Always leave handshake mode, including after a bad UID response.
            serial_port.write(b"ENDHS\n")
            response = serial_port.readline().decode(
                "ascii", errors="replace").strip()
            if uid != "UID:PICO-002":
                raise RuntimeError(
                    f"Pico handshake returned {uid!r}, expected 'UID:PICO-002'")
            if response != "HSOK":
                raise RuntimeError(
                    f"Pico handshake termination returned {response!r}")
            durations_ms.append((time.monotonic() - started) * 1000.0)
        return {
            "probes": len(durations_ms),
            "mean_ms": float(np.mean(durations_ms)),
            "max_ms": float(np.max(durations_ms)),
        }

    def set_voltage(self, net_name: str, voltage: float) -> None:
        if not self.connected:
            raise RuntimeError("PyDAQ MZI controller is not connected")
        requested = validate_requested_heater_voltages({net_name: voltage})
        self._config.set_mzi_voltage(net_name, requested[net_name])
    def set_voltages(self, voltages, *, on_sent=None) -> None:
        if not self.connected:
            raise RuntimeError("PyDAQ MZI controller is not connected")
        requested = validate_requested_heater_voltages(
            {str(net): voltage for net, voltage in voltages.items()})
        if hasattr(self._config, "set_mzi_voltages"):
            self._config.set_mzi_voltages(requested, on_sent=on_sent)
        else:
            for net, voltage in requested.items():
                self._config.set_mzi_voltage(net, voltage)
                if on_sent is not None:
                    on_sent(net, voltage)


    def close(self) -> None:
        if self._installation is not None:
            self._installation.uninstall(close_connections=True)
        self._installation = None
        self._config = None


def calibration_voltage_sequence(
    start: float,
    stop: float,
    points: int,
    reverse: bool,
    *,
    spacing: str = "voltage",
    explicit: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    if explicit is not None:
        forward = np.asarray(explicit, dtype=np.float64)
        if forward.ndim != 1 or forward.size < 2:
            raise ValueError("explicit heater sweep requires at least two values")
    else:
        if points < 2:
            raise ValueError("points must be at least 2")
        start = float(start)
        stop = float(stop)
        if spacing == "voltage":
            forward = np.linspace(start, stop, int(points))
        elif spacing == "power":
            if start < 0.0 or stop < 0.0:
                raise ValueError("power-spaced heater voltages must be non-negative")
            forward = np.sqrt(np.linspace(start * start, stop * stop, int(points)))
        else:
            raise ValueError("spacing must be voltage or power")
    for voltage in forward:
        validate_heater_voltage(voltage)
    if not reverse:
        return forward, np.zeros(forward.size, dtype=np.int8)
    backward = forward[::-1]
    return (
        np.concatenate((forward, backward)),
        np.concatenate((
            np.zeros(forward.size, dtype=np.int8),
            np.ones(backward.size, dtype=np.int8),
        )),
    )
