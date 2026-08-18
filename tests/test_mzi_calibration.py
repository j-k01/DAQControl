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
    calibration_voltage_sequence,
    measure_periodic_pulses,
    measure_spikes_at_indices,
    measure_triggered_spikes,
    parse_heater_voltages,
    probe_fpga_pico_bridge,
    PydaqMziController,
)


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

    def test_triggered_spike_measurement_preserves_negative_sign(self):
        reps = np.zeros((8, 1024), dtype=np.float64)
        reps[:, 299:302] = [-0.075, -0.15, -0.075]
        reps[:, 699:702] = [-0.075, -0.15, -0.075]
        result = measure_triggered_spikes(reps, 200)
        self.assertAlmostEqual(result.signed_height, -0.15)
        self.assertAlmostEqual(result.absolute_height, 0.15)

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
