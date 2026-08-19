from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mzi_calibration import (
    TriggeredSpikeMeasurement,
    align_stacks_to_loopback,
    analyze_optical_peaks,
    calibration_voltage_sequence,
    estimate_main_lobe_lag,
    estimate_main_lobe_lag_auto_polarity,
    measure_periodic_pulses,
    measure_reference_spikes,
    measure_spikes_at_indices,
    measure_spikes_in_windows,
    measure_triggered_spikes,
    optical_schedule_from_loopback,
    parse_heater_voltages,
    probe_fpga_pico_bridge,
    PydaqMziController,
)
from simulate_loopback_reference import run_simulation


class FakePicoSerial:
    def __init__(self):
        self.commands = []
        self.responses = []

    def reset_input_buffer(self):
        self.responses.clear()

    def write(self, payload):
        self.commands.append(payload)
        if payload == b"HANDSHAKE\n":
            self.responses.append(b"UID:PICO-002\n")
        elif payload == b"ENDHS\n":
            self.responses.append(b"HSOK\n")

    def readline(self):
        return self.responses.pop(0)


class MziCalibrationTests(unittest.TestCase):
    def test_bridge_preflight_explains_independent_udp_services(self):
        class MissingBridge:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def reset_input_buffer(self):
                raise OSError("timed out")

        with self.assertRaisesRegex(
                RuntimeError, r"5007.*5006 is a separate service"):
            probe_fpga_pico_bridge(serial_factory=MissingBridge)

    def test_bridge_preflight_does_not_send_pico_commands(self):
        class LiveBridge:
            instance = None

            def __init__(self, **_kwargs):
                self.flushed = False
                self.writes = []
                LiveBridge.instance = self

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def reset_input_buffer(self):
                self.flushed = True

        probe_fpga_pico_bridge(serial_factory=LiveBridge)

        self.assertTrue(LiveBridge.instance.flushed)
        self.assertEqual(LiveBridge.instance.writes, [])

    def test_pico_connection_probe_does_not_write_heater_outputs(self):
        serial_port = FakePicoSerial()
        controller = PydaqMziController()
        controller._config = SimpleNamespace(
            pico=SimpleNamespace(serial=serial_port), MZI_NET_NAMES=())

        result = controller.test_connection(probes=5)

        self.assertEqual(result["probes"], 5)
        self.assertEqual(serial_port.commands,
                         [b"HANDSHAKE\n", b"ENDHS\n"] * 5)
        self.assertTrue(all(not command.startswith(b"W")
                            for command in serial_port.commands))

    def test_aligns_shifted_positive_pulses(self):
        period, high, length = 128, 20, 4096
        base = np.zeros(length)
        base[(np.arange(length) % period) < high] = 0.275
        shifts = np.array([0, 3, 17, 51, 97])
        reps = np.stack([np.roll(base, shift) for shift in shifts])

        result = measure_periodic_pulses(reps, period, high)

        np.testing.assert_array_equal(result.phase_offsets, shifts)
        self.assertAlmostEqual(result.signed_height, 0.275, places=10)
        self.assertAlmostEqual(result.absolute_height, 0.275, places=10)
        np.testing.assert_allclose(result.aligned_average, base)

    def test_preserves_inverted_pulse_sign(self):
        period, high, length = 64, 8, 2048
        base = np.zeros(length)
        base[(np.arange(length) % period) < high] = -0.12
        reps = np.stack([np.roll(base, shift) for shift in (5, 19, 31)])

        result = measure_periodic_pulses(reps, period, high)

        self.assertAlmostEqual(result.signed_height, -0.12, places=10)
        self.assertAlmostEqual(result.absolute_height, 0.12, places=10)

    def test_forward_reverse_voltage_sequence(self):
        voltage, direction = calibration_voltage_sequence(0.0, 1.0, 3, True)
        np.testing.assert_allclose(voltage, [0.0, 0.5, 1.0, 1.0, 0.5, 0.0])
        np.testing.assert_array_equal(direction, [0, 0, 0, 1, 1, 1])

    def test_constant_voltage_sequence_is_valid_repeatability_control(self):
        voltage, direction = calibration_voltage_sequence(
            0.0, 0.0, 4, False)

        np.testing.assert_allclose(voltage, [0.0, 0.0, 0.0, 0.0])
        np.testing.assert_array_equal(direction, [0, 0, 0, 0])

    def test_power_spaced_voltage_sequence(self):
        voltage, direction = calibration_voltage_sequence(
            0.0, 1.0, 3, False, spacing="power")
        np.testing.assert_allclose(voltage, [0.0, np.sqrt(0.5), 1.0])
        np.testing.assert_array_equal(direction, [0, 0, 0])

    def test_explicit_heater_voltage_sequence(self):
        explicit = parse_heater_voltages("0, 0.12; 0.41  0.9")
        voltage, direction = calibration_voltage_sequence(
            0.0, 1.0, 3, True, explicit=explicit)
        np.testing.assert_allclose(
            voltage, [0.0, 0.12, 0.41, 0.9, 0.9, 0.41, 0.12, 0.0])
        np.testing.assert_array_equal(direction, [0, 0, 0, 0, 1, 1, 1, 1])

    def test_generated_and_explicit_sweeps_reject_unsafe_voltage(self):
        with self.assertRaisesRegex(ValueError, "outside 0..1 V"):
            calibration_voltage_sequence(0.0, 5.0, 3, False)
        with self.assertRaisesRegex(ValueError, "between 0 and 1 V"):
            parse_heater_voltages("0, 0.5, 5.0")
        with self.assertRaisesRegex(ValueError, "outside 0..1 V"):
            calibration_voltage_sequence(
                0.0, 1.0, 3, False,
                explicit=np.asarray([0.0, 0.5, 1.0001]))

    def test_triggered_spike_measurement_uses_fixed_hardware_indices(self):
        rng = np.random.default_rng(7)
        repetitions, length, step = 16, 2048, 400
        baselines = np.linspace(-0.03, 0.04, repetitions)
        reps = baselines[:, None] + rng.normal(0.0, 0.0005, (repetitions, length))
        expected_peaks = np.asarray([560, 890, 1320])
        for peak in expected_peaks:
            reps[:, peak - 1:peak + 2] += [0.1, 0.2, 0.1]

        result = measure_triggered_spikes(
            reps, step)

        np.testing.assert_array_equal(result.peak_indices, expected_peaks)
        np.testing.assert_allclose(result.baseline_levels, baselines, atol=0.001)
        self.assertAlmostEqual(result.signed_height, 0.2, delta=0.001)
        self.assertAlmostEqual(result.absolute_height, 0.2, delta=0.001)
        self.assertEqual(result.per_peak_height.shape, (16, 3))
        np.testing.assert_array_less(result.start_indices, result.peak_indices)
        np.testing.assert_array_less(result.peak_indices, result.end_indices)
        self.assertTrue(np.all(result.widths_samples >= 3))

    def test_detection_runs_on_sixteen_capture_average_only(self):
        rng = np.random.default_rng(41)
        repetitions, length, step = 16, 2048, 300
        reps = rng.normal(0.0, 0.004, (repetitions, length))
        reps[:, 699:702] += [0.006, 0.012, 0.006]
        # A large but non-repeatable three-sample disturbance must not become
        # a detected spike merely because it exists in one raw capture.
        reps[3, 1099:1102] += [0.025, 0.050, 0.025]

        result = measure_triggered_spikes(reps, step)

        self.assertEqual(result.peak_indices.size, 1)
        self.assertLessEqual(abs(int(result.peak_indices[0]) - 700), 1)
        self.assertGreater(result.start_indices[0], 690)
        self.assertLess(result.end_indices[0], 710)
        self.assertEqual(result.per_peak_height.shape, (16, 1))

    def test_fixed_peak_schedule_accepts_extinguished_response(self):
        repetitions, length, step = 16, 1024, 200
        peaks = np.asarray([350, 700])
        reps = np.repeat(np.linspace(-0.02, 0.03, repetitions)[:, None],
                         length, axis=1)

        result = measure_spikes_at_indices(
            reps, step, peaks)

        np.testing.assert_array_equal(result.peak_indices, peaks)
        self.assertAlmostEqual(result.signed_height, 0.0, places=12)
        self.assertAlmostEqual(result.absolute_height, 0.0, places=12)


    def test_fixed_boundaries_tolerate_one_sample_peak_motion(self):
        reps = np.zeros((4, 1024), dtype=np.float64)
        for repetition, shift in enumerate((-1, 0, 1, -1)):
            for peak in (350, 700):
                moved = peak + shift
                reps[repetition, moved - 1:moved + 2] = [0.1, 0.2, 0.1]

        result = measure_spikes_at_indices(
            reps, 200, np.asarray([350, 700]),
            start_indices=np.asarray([347, 697]),
            end_indices=np.asarray([353, 703]),
            polarities=np.asarray([1, 1]))

        self.assertAlmostEqual(result.signed_height, 0.2)
        np.testing.assert_allclose(result.per_peak_height, 0.2)

    def test_known_window_maximum_is_taken_after_averaging(self):
        repetitions, length, step = 32, 1024, 200
        reps = np.zeros((repetitions, length), dtype=np.float64)
        reps[:, 500] = 0.010
        # Every raw repetition has a larger transient at a different sample.
        # After averaging none can exceed the repeatable 10 mV event.
        for repetition in range(repetitions):
            reps[repetition, 540 + repetition] = 0.032

        result = measure_spikes_in_windows(
            reps, step, np.asarray([500]),
            start_indices=np.asarray([470]),
            end_indices=np.asarray([590]),
            polarities=np.asarray([1]))

        self.assertEqual(int(result.peak_indices[0]), 500)
        self.assertAlmostEqual(result.absolute_height, 0.010, places=12)
        self.assertAlmostEqual(
            float(result.per_peak_height.mean(axis=0)[0]), 0.010,
            places=12)
    def test_chattering_reference_yields_every_known_optical_peak(self):
        rng = np.random.default_rng(83)
        repetitions, length, step = 16, 4096, 200
        reference_peaks = np.asarray([
            500, 560, 620, 680,
            1300, 1360, 1420, 1480,
            2300, 2360, 2420, 2480,
        ])
        reference_reps = rng.normal(
            0.0, 0.0004, (repetitions, length))
        reference_reps[:, step:] += 0.025
        pulse = np.asarray([0.10, 0.25, 0.40, 0.40, 0.25, 0.10])
        for peak in reference_peaks:
            reference_reps[:, peak - 2:peak + 4] += pulse

        reference = measure_reference_spikes(
            reference_reps, step, threshold_sigma=5.0,
            minimum_peak_distance_samples=40)

        self.assertEqual(reference.peak_indices.size, reference_peaks.size)
        self.assertLessEqual(
            int(np.max(np.abs(reference.peak_indices - reference_peaks))), 1)

        latency = 37
        optical_amplitudes = np.linspace(0.006, 0.017, reference_peaks.size)
        optical_reps = rng.normal(
            0.0, 0.0002, (repetitions, length))
        for peak, amplitude in zip(reference_peaks, optical_amplitudes):
            optical_reps[:, peak + latency] += amplitude
        optical_average = optical_reps.mean(axis=0)
        nominal, starts, ends, signs = optical_schedule_from_loopback(
            reference, optical_average, latency,
            padding_samples=5, response_polarity=1)

        np.testing.assert_array_equal(
            nominal, reference.peak_indices + latency)
        np.testing.assert_array_equal(starts, nominal - 5)
        np.testing.assert_array_equal(ends, nominal + 5)
        measured = measure_spikes_in_windows(
            optical_reps, step, nominal,
            start_indices=starts, end_indices=ends, polarities=signs)

        np.testing.assert_array_equal(
            measured.peak_indices, reference_peaks + latency)
        self.assertAlmostEqual(
            measured.signed_height,
            float(np.mean(optical_amplitudes)),
            delta=0.0002)
    def test_triggered_spike_measurement_preserves_negative_sign(self):
        reps = np.zeros((8, 1024), dtype=np.float64)
        reps[:, 299:302] = [-0.075, -0.15, -0.075]
        reps[:, 699:702] = [-0.075, -0.15, -0.075]
        result = measure_triggered_spikes(reps, 200)
        self.assertAlmostEqual(result.signed_height, -0.15)
        self.assertAlmostEqual(result.absolute_height, 0.15)

    def test_optical_analysis_selects_negative_pulses_and_rejects_outlier(self):
        peaks = np.asarray([100, 200, 300, 400, 500, 600, 700, 800])
        waveform = np.zeros(1024, dtype=np.float64)
        waveform[peaks[:5]] = [-0.2, -0.2, -0.2, -0.2, -0.6]
        waveform[peaks[5:]] = [0.08, 0.09, 0.10]
        measurement = TriggeredSpikeMeasurement(
            signed_height=0.0, absolute_height=0.0,
            per_rep_height=np.zeros(16),
            per_peak_height=np.zeros((16, peaks.size)),
            peak_indices=peaks, baseline_levels=np.zeros(16),
            averaged_waveform=waveform,
            start_indices=peaks - 1, end_indices=peaks + 1,
            polarities=np.asarray([-1] * 5 + [1] * 3, dtype=np.int8),
            widths_samples=np.full(peaks.size, 3),
            fwhm_samples=np.full(peaks.size, 1),
            areas_v_samples=np.zeros(peaks.size),
            detection_threshold_v=0.01,
            boundary_thresholds_v=np.zeros(peaks.size),
            noise_sigma_v=0.001)

        analysis = analyze_optical_peaks(
            measurement, polarity="auto", sigma_limit=1.5,
            filter_enabled=True)

        self.assertEqual(analysis.polarity, "negative")
        self.assertEqual(analysis.peak_indices.size, 5)
        self.assertEqual(np.count_nonzero(analysis.accepted), 4)
        self.assertAlmostEqual(analysis.raw_mean_v, 0.28)
        self.assertAlmostEqual(analysis.filtered_mean_v, 0.2)
    def test_noise_only_loopback_is_rejected(self):
        rng = np.random.default_rng(19)
        noise = rng.normal(0.0, 1.0, (16, 4096))

        with self.assertRaisesRegex(ValueError, "loopback correlation is too weak"):
            align_stacks_to_loopback(
                {0: noise.copy(), 3: noise}, 3, max_lag=32)

    def test_main_lobe_correlation_rejects_larger_ac_recovery(self):
        length = 4096
        events = np.asarray([400, 1000, 1700, 2500, 3300])
        reference = np.zeros(length)
        observed = np.zeros(length)
        latency = 37
        for event in events:
            reference[event] = -0.500
            reference[event + 18:event + 42] += (
                0.220 * np.exp(-np.arange(24) / 8.0))
            observed[event + latency] = -0.015
            observed[event + latency + 18:event + latency + 42] += (
                0.030 * np.exp(-np.arange(24) / 8.0))

        result = estimate_main_lobe_lag(
            observed, reference, 300,
            observed_polarity=-1, template_polarity=-1,
            template_peak_indices=events)

        self.assertEqual(result.lag_samples, latency)
        self.assertGreater(result.score, 0.9)

    def test_main_lobe_correlation_accepts_explicit_channel_inversion(self):
        length = 2048
        events = np.asarray([300, 800, 1300, 1800])
        reference = np.zeros(length)
        observed = np.zeros(length)
        for event in events:
            reference[event] = -0.500
            observed[event + 29] = 0.012
            observed[event + 47] = -0.025

        result = estimate_main_lobe_lag(
            observed, reference, 200,
            observed_polarity=1, template_polarity=-1,
            template_peak_indices=events)

        self.assertEqual(result.lag_samples, 29)

    def test_main_lobe_correlation_auto_detects_inverted_lane(self):
        length = 4096
        events = np.asarray([500, 1100, 1700, 2300, 2900, 3500])
        reference = np.zeros(length)
        reference[events] = 0.5
        observed = np.roll(-reference * 0.03, 37)

        result, relative_polarity = (
            estimate_main_lobe_lag_auto_polarity(
                observed, reference, 100, template_polarity=1,
                template_peak_indices=events))

        self.assertEqual(result.lag_samples, 37)
        self.assertGreater(result.score, 0.99)
        self.assertEqual(relative_polarity, -1)
    def test_loopback_simulation_recovers_reference_and_optical_latency(self):
        report = run_simulation(seed=83)

        self.assertEqual(report["maximum_reference_lag_error_samples"], 0)
        self.assertEqual(report["recovered_optical_latency_samples"], 47)
        self.assertEqual(report["recovered_spike_count"], 8)
        self.assertLess(abs(report["spike_amplitude_error_v"]), 0.001)
        self.assertFalse(report["software_repetition_alignment_applied"])
        self.assertEqual(report["maximum_reference_lag_error_samples"], 0)

    def test_rejects_invalid_shape(self):
        with self.assertRaises(ValueError):
            measure_periodic_pulses(np.zeros(8), 4, 1)
        with self.assertRaises(ValueError):
            measure_periodic_pulses(np.zeros((2, 16)), 4, 4)
        with self.assertRaises(ValueError):
            measure_triggered_spikes(np.zeros((2, 16)), 2)
        with self.assertRaises(ValueError):
            parse_heater_voltages("0.2, 1.1")


if __name__ == "__main__":
    unittest.main()
