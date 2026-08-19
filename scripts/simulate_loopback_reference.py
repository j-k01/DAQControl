#!/usr/bin/env python3
"""Verify DAC3/ADC3 loopback alignment with a delayed noisy spike train."""

from __future__ import annotations

import argparse
import json

import numpy as np

from mzi_calibration import measure_spikes_with_loopback


def _delay(trace: np.ndarray, samples: int) -> np.ndarray:
    output = np.zeros_like(trace)
    if samples >= 0:
        output[samples:] = trace[:trace.size - samples]
    else:
        output[:samples] = trace[-samples:]
    return output


def run_simulation(seed: int = 83) -> dict:
    rng = np.random.default_rng(seed)
    repetitions = 16
    length = 8192
    step_sample = 200
    reference_latency = 83
    optical_latency = 47
    event_samples = np.asarray(
        [620, 1310, 2170, 2940, 3860, 4750, 5930, 7010],
        dtype=np.int32)
    pulse = np.asarray(
        [-0.025, -0.075, -0.150, -0.200, -0.150, -0.075, -0.025])
    known_pattern = np.zeros(length, dtype=np.float64)
    for event in event_samples:
        known_pattern[event - 3:event + 4] += pulse

    def recovery_train(amplitude: float) -> np.ndarray:
        recovery = np.zeros(length, dtype=np.float64)
        tail = amplitude * np.exp(-np.arange(32) / 9.0)
        for event in event_samples:
            start = event + 15
            recovery[start:start + tail.size] += tail
        return recovery

    # The electrical reference is 500 mV while the optical response is only
    # 15 mV. Both also carry an opposite-polarity AC-coupling recovery lobe;
    # the optical recovery is intentionally larger than its true spike.
    reference_pattern = known_pattern * 2.5 + recovery_train(0.220)
    optical_gain = 0.075
    optical_pattern = known_pattern * optical_gain + recovery_train(0.030)

    # BCPT captures are hardware-triggered, so every repetition has the same
    # sample origin. Independent noise is averaged; no synthetic timing jitter
    # is introduced or corrected in software.
    loopback = np.stack([
        _delay(reference_pattern, reference_latency) +
        rng.normal(0.0, 0.0010, length)
        for _repetition in range(repetitions)
    ])
    optical = np.stack([
        _delay(optical_pattern, reference_latency + optical_latency) +
        rng.normal(0.0, 0.0015, length)
        for _repetition in range(repetitions)
    ])

    result = measure_spikes_with_loopback(
        optical, loopback, step_sample, template=known_pattern,
        max_repetition_lag=128, max_optical_lag=128,
        window_padding_samples=5, threshold_sigma=5.0,
        boundary_sigma=2.0, minimum_seed_samples=2)

    expected_lags = np.zeros(repetitions, dtype=np.int32)
    recovered_lags = result.alignment.lag_samples
    expected_amplitude = abs(float(np.min(pulse))) * optical_gain
    measured_amplitude = result.optical.absolute_height
    report = {
        "reference_latency_samples": reference_latency,
        "optical_latency_samples": optical_latency,
        "recovered_reference_lags": recovered_lags.tolist(),
        "expected_reference_lags": expected_lags.tolist(),
        "maximum_reference_lag_error_samples": int(
            np.max(np.abs(recovered_lags - expected_lags))),
        "recovered_optical_latency_samples": int(
            result.optical_latency_samples),
        "optical_latency_error_samples": int(
            result.optical_latency_samples - optical_latency),
        "software_repetition_alignment_applied": False,
        "loopback_correlation_min": None,
        "optical_correlation": float(result.optical_correlation_score),
        "reference_spike_amplitude_v": 0.500,
        "optical_ac_recovery_peak_v": 0.030,
        "expected_spike_amplitude_v": expected_amplitude,
        "measured_spike_amplitude_v": float(measured_amplitude),
        "spike_amplitude_error_v": float(
            measured_amplitude - expected_amplitude),
        "recovered_spike_count": int(result.optical.peak_indices.size),
        "expected_spike_count": int(event_samples.size),
    }
    if report["maximum_reference_lag_error_samples"] > 0:
        raise AssertionError(report)
    if abs(report["optical_latency_error_samples"]) > 1:
        raise AssertionError(report)
    if report["recovered_spike_count"] != report["expected_spike_count"]:
        raise AssertionError(report)
    if abs(report["spike_amplitude_error_v"]) > 0.001:
        raise AssertionError(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=83)
    args = parser.parse_args()
    print(json.dumps(run_simulation(args.seed), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())