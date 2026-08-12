from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from spike_test_analysis import (
    analyze_stacks,
    detect_spikes_output,
    synthesize_integrated_channel,
    validate_source_channels,
)


def spike_stack(indices, *, repetitions=16, samples=4096, amplitude=-0.8):
    stack = np.zeros((repetitions, samples), dtype=np.float64)
    ramp = 5
    length = 40
    denom = ramp + 2
    shape = [-(index + 1) / denom for index in range(ramp)]
    shape.extend([-1.0] * (length - 2 * ramp))
    shape.extend([-(ramp - index) / denom for index in range(ramp)])
    shape = np.asarray(shape) * abs(amplitude)
    for index in indices:
        stack[:, index:index + shape.size] += shape
    stack += np.linspace(-0.01, 0.01, repetitions)[:, None]
    return stack


class SpikeTestAnalysisTests(unittest.TestCase):
    def test_scaling_is_global_and_input_is_unchanged(self):
        original = spike_stack([800, 1500, 2500])
        before = original.copy()
        result = synthesize_integrated_channel(
            original, step_sample=500, target_peak_v=0.015,
            noise_rms_v=0.0, latency_samples=37,
            rng=np.random.default_rng(4))

        np.testing.assert_array_equal(original, before)
        self.assertAlmostEqual(
            np.max(np.abs(result.clean_average_v[537:])), 0.015, places=12)
        self.assertEqual(result.latency_samples, 37)

    def test_average_recovers_regular_and_chattering_under_noise(self):
        regular = [900, 1600, 2400, 3300]
        chattering = [850, 950, 1050, 2100, 2200, 2300, 3400]
        stacks = {0: spike_stack(regular), 1: spike_stack(chattering)}

        synthetic, detections = analyze_stacks(
            stacks, step_sample=500, target_peak_v=0.015,
            noise_rms_v=0.005, latency_samples={0: 31, 1: 73}, seed=9,
            threshold_sigma=4.5)

        self.assertEqual(synthetic[0].synthetic_v.shape, (16, 4096))
        self.assertEqual(detections[0].indices.size, len(regular))
        self.assertEqual(detections[1].indices.size, len(chattering))
        for channel, expected, latency in (
                (0, regular, 31), (1, chattering, 73)):
            measured = detections[channel].measurements
            for spike, pulse_start in enumerate(expected):
                true_start = pulse_start + latency
                true_end = true_start + 39
                self.assertLessEqual(abs(int(measured.start_indices[spike]) - true_start), 4)
                self.assertLessEqual(abs(int(measured.end_indices[spike]) - true_end), 4)
                self.assertGreaterEqual(measured.peak_indices[spike], measured.start_indices[spike])
                self.assertLessEqual(measured.peak_indices[spike], measured.end_indices[spike])
                self.assertGreater(measured.absolute_amplitudes_v[spike], 0.013)
            self.assertEqual(measured.per_rep_amplitudes_v.shape,
                             (16, len(expected)))
        self.assertGreater(np.std(synthetic[0].synthetic_v),
                           np.std(synthetic[0].average_v))

    def test_output_only_detector_finds_unrelated_shapes_and_polarities(self):
        samples = 2400
        clean = np.zeros(samples)
        clean[500:517] = np.r_[np.linspace(0.0, 0.012, 9),
                               np.linspace(0.0105, 0.0, 8)]
        clean[900:970] = -0.009 * np.sin(np.linspace(0.0, np.pi, 70))
        clean[1500:1620] = 0.007
        rng = np.random.default_rng(72)
        repetitions = clean[None, :] + rng.normal(0.0, 0.001, (16, samples))
        result = detect_spikes_output(
            repetitions.mean(axis=0), response_start=300,
            repetitions_v=repetitions, threshold_sigma=5.0,
            boundary_sigma=2.0)

        measured = result.measurements
        self.assertEqual(measured.peak_indices.size, 3)
        np.testing.assert_array_equal(measured.polarities, [1, -1, 1])
        for spike, (start, end) in enumerate(((500, 516), (900, 969), (1500, 1619))):
            self.assertLessEqual(abs(int(measured.start_indices[spike]) - start), 5)
            self.assertLessEqual(abs(int(measured.end_indices[spike]) - end), 5)
        np.testing.assert_allclose(
            measured.absolute_amplitudes_v, [0.012, 0.009, 0.007], atol=0.001)

    def test_boundary_level_is_configurable_without_expected_width(self):
        samples = 1024
        x = np.arange(samples)
        trace = 0.010 * np.exp(-0.5 * ((x - 600) / 24.0) ** 2)
        rng = np.random.default_rng(19)
        repetitions = trace[None, :] + rng.normal(0.0, 0.0004, (16, samples))
        average = repetitions.mean(axis=0)
        loose = detect_spikes_output(
            average, response_start=200, repetitions_v=repetitions,
            threshold_sigma=5.0, boundary_sigma=1.5)
        tight = detect_spikes_output(
            average, response_start=200, repetitions_v=repetitions,
            threshold_sigma=5.0, boundary_sigma=4.0)
        self.assertEqual(loose.indices.size, 1)
        self.assertEqual(tight.indices.size, 1)
        self.assertGreater(loose.measurements.widths_samples[0],
                           tight.measurements.widths_samples[0])

    def test_isolated_sample_rejection_is_explicitly_configurable(self):
        trace = np.zeros(1024)
        trace[500] = 0.010
        trace[700:703] = 0.008
        default = detect_spikes_output(trace, response_start=200)
        literal = detect_spikes_output(
            trace, response_start=200, minimum_seed_samples=1)

        np.testing.assert_array_equal(default.indices, [700])
        np.testing.assert_array_equal(literal.indices, [500, 700])

    def test_noise_is_independent_between_repetitions(self):
        result = synthesize_integrated_channel(
            spike_stack([900]), step_sample=500, target_peak_v=0.015,
            noise_rms_v=0.004, rng=np.random.default_rng(2))
        residual = result.synthetic_v - result.clean_v
        self.assertFalse(np.array_equal(residual[0], residual[1]))
        self.assertAlmostEqual(float(residual.std()), 0.004, delta=0.00015)

    def test_source_preflight_rejects_quiet_channel(self):
        stacks = {0: spike_stack([900]), 1: np.zeros((16, 4096))}
        with self.assertRaisesRegex(ValueError, "ADC1=0.000 mV"):
            validate_source_channels(
                stacks, step_sample=500, minimum_peak_v=0.005)
        peaks = validate_source_channels(
            {0: stacks[0]}, step_sample=500, minimum_peak_v=0.005)
        self.assertGreater(peaks[0], 0.7)


if __name__ == "__main__":
    unittest.main()
