from pathlib import Path
import sys
import unittest

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tone_calibration import (  # noqa: E402
    analyze_tone_capture, dds_phase_increment, fit_tone,
)


class ToneCalibrationTests(unittest.TestCase):
    def test_dds_frequency_range_quantizes_within_one_lsb(self):
        lsb_hz = 1.0e9 / (1 << 24)
        for requested in (10.0e3, 100.0e3, 1.0e6, 10.0e6):
            increment, actual = dds_phase_increment(requested)
            self.assertGreater(increment, 0)
            self.assertLessEqual(abs(actual - requested), 0.5 * lsb_hz)

    def test_aligned_average_recovers_gain_phase_and_latency(self):
        rng = np.random.default_rng(20260901)
        _increment, frequency = dds_phase_increment(1.0e6)
        sample_rate = 1.0e9
        sample_count = 16384
        repetitions = 16
        n = np.arange(sample_count, dtype=np.float64)
        omega = 2.0 * np.pi * frequency / sample_rate
        amplitudes = {0: 0.014, 1: 0.021, 2: 0.008, 3: 0.45}
        delays = {0: 37.0, 1: 83.0, 2: 146.0, 3: 0.0}
        stacks = {}
        for channel in range(4):
            clean = (
                amplitudes[channel] *
                np.sin(omega * (n - delays[channel])) + 0.001 * channel)
            stacks[channel] = np.stack([
                clean + rng.normal(0.0, 0.004, sample_count)
                for _ in range(repetitions)
            ])

        result = analyze_tone_capture(
            stacks, frequency, reference_adc=3, start_sample=64)

        for channel in range(3):
            lane = result["channels"][channel]
            self.assertAlmostEqual(
                lane["amplitude_v"], amplitudes[channel], delta=2.0e-4)
            self.assertAlmostEqual(
                lane["gain_vs_reference"],
                amplitudes[channel] / amplitudes[3], delta=5.0e-4)
            self.assertAlmostEqual(
                lane["latency_modulo_period_ns"],
                delays[channel], delta=1.5)
        self.assertAlmostEqual(
            result["channels"][3]["latency_modulo_period_ns"], 0.0, delta=1e-6)

    def test_coherent_average_reduces_fit_residual(self):
        rng = np.random.default_rng(9)
        _increment, frequency = dds_phase_increment(250.0e3)
        n = np.arange(8192, dtype=np.float64)
        clean = 0.02 * np.sin(2.0 * np.pi * frequency * n / 1.0e9)
        stack = np.stack([
            clean + rng.normal(0.0, 0.01, clean.size) for _ in range(16)
        ])
        single = fit_tone(stack[0], frequency, start_sample=64)
        averaged = fit_tone(stack.mean(axis=0), frequency, start_sample=64)
        self.assertLess(averaged.residual_rms, single.residual_rms * 0.4)


if __name__ == "__main__":
    unittest.main()
