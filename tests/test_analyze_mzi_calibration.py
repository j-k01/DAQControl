from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_mzi_calibration import (  # noqa: E402
    analyze_batches,
    branch_lut,
    choose_monotonic_branch,
    fit_power_curve,
)
from optical_experiment import (  # noqa: E402
    create_experiment,
    save_heater_capture,
    update_manifest,
    write_json,
)


def scalar_channels(amplitudes_mv):
    reference_mv = float(amplitudes_mv[3])
    return {
        str(channel): {
            "amplitude_v": float(amplitude_mv) / 1.0e3,
            "amplitude_std_v": 0.005 / 1.0e3,
            "gain_vs_reference": float(amplitude_mv) / reference_mv,
            "phase_vs_reference_rad": 0.1 * channel,
            "latency_modulo_period_s": 1.0e-9 * channel,
            "latency_modulo_period_ns": float(channel),
            "offset_v": 0.0,
            "residual_rms_v": 0.002 / 1.0e3,
        }
        for channel, amplitude_mv in amplitudes_mv.items()
    }


def create_batch(root, input_dac, heater, response_by_adc):
    batch = root / f"batch_dac{input_dac}"
    batch.mkdir()
    voltages = np.linspace(0.0, 1.0, 20)
    metadata = {
        "hardware": {"sample_rate_hz": 1.0e9},
        "acquisition": {
            "repetitions_per_heater_capture": 16,
            "reference_adc_channel": 3,
        },
        "stimulus": {
            "mode": "shared_dds_pure_tone",
            "actual_frequency_hz": 100.0e3,
        },
        "heater_sweep": {
            "primary_heater_net": heater,
            "heater_nets": [heater],
            "planned_voltages_v": voltages,
            "planned_directions": np.zeros(voltages.size, dtype=np.int8),
        },
    }
    experiment = create_experiment(
        batch, f"calibration_{heater}_dac{input_dac}", metadata)
    for index, voltage in enumerate(voltages):
        stacks = {
            channel: np.zeros((16, 64), dtype=np.int16)
            for channel in range(4)
        }
        point = save_heater_capture(
            experiment, index=index, voltage_v=float(voltage), direction=0,
            stacks=stacks)
        amplitudes = {
            channel: float(response_by_adc[channel](voltage))
            for channel in range(3)
        }
        amplitudes[3] = 400.0 * (1.0 + 0.01 * np.sin(3.0 * voltage))
        tone = {
            "voltage_v": float(voltage),
            "direction": 0,
            "channels": scalar_channels(amplitudes),
        }
        (point / "tone_analysis.json").write_text(
            json.dumps(tone, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    update_manifest(experiment, capture_status="complete")
    write_json(batch / "characterization.json", {
        "schema": "daq_mzi_one_input_characterization",
        "capture_status": "complete",
        "input_dac": input_dac,
        "experiments": [{
            "heater_net": heater,
            "directory": experiment.relative_to(batch).as_posix(),
            "capture_status": "complete",
        }],
    })
    return batch


class AnalyzeMziCalibrationTests(unittest.TestCase):
    def test_power_curve_fit_recovers_phase_slope(self):
        voltage = np.linspace(0.0, 1.0, 40)
        expected_slope = 2.4 * np.pi
        amplitude = 1.2 + 0.7 * np.cos(expected_slope * voltage ** 2 + 0.35)
        fit = fit_power_curve(voltage, amplitude, max_fringes=2.0)
        self.assertGreater(fit.r_squared, 0.9999)
        self.assertAlmostEqual(
            fit.phase_slope_rad_per_v2, expected_slope, delta=0.02)
        self.assertAlmostEqual(fit.modulation_mv, 0.7, delta=0.002)

    def test_monotonic_branch_produces_invertible_voltage_lut(self):
        voltage = np.linspace(0.0, 1.0, 31)
        amplitude = 1.0 + np.cos(1.4 * np.pi * voltage ** 2 + 0.2)
        branch = choose_monotonic_branch(voltage, amplitude)
        targets, requested_voltage = branch_lut(branch, levels=17)
        self.assertEqual(targets.size, 17)
        self.assertEqual(requested_voltage.size, 17)
        self.assertGreater(branch.span_mv, 1.0)
        self.assertTrue(np.all(np.diff(targets) >= 0.0))
        self.assertTrue(
            np.all(np.diff(requested_voltage) >= 0.0) or
            np.all(np.diff(requested_voltage) <= 0.0))

    def test_combined_inputs_select_best_path_for_physical_heater(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weak = lambda voltage: 0.30 + 0.01 * voltage
            medium = lambda voltage: 0.40 + 0.15 * np.cos(
                1.5 * np.pi * voltage ** 2 + 0.1)
            strong = lambda voltage: 0.80 + 0.65 * np.cos(
                1.8 * np.pi * voltage ** 2 + 0.25)
            batches = [
                create_batch(root, 0, "h_7_1", {
                    0: weak, 1: medium, 2: strong}),
                create_batch(root, 1, "h_7_1", {
                    0: medium, 1: weak, 2: weak}),
                create_batch(root, 2, "h_7_1", {
                    0: weak, 1: weak, 2: medium}),
            ]
            output, payload = analyze_batches(
                batches, root / "analysis", max_fringes=2.0,
                lut_levels=21, show=False)

            self.assertTrue(payload["complete_input_coverage"])
            self.assertEqual(len(payload["elements"]), 1)
            element = payload["elements"][0]
            self.assertEqual(element["heater_net"], "h_7_1")
            self.assertEqual(element["input_dac"], 0)
            self.assertEqual(element["output_adc"], 2)
            self.assertFalse(payload["complete_element_coverage"])
            self.assertGreater(element["fit"]["r_squared"], 0.90)
            self.assertTrue((output / "mzi_calibration.json").exists())
            self.assertTrue((output / "mzi_calibration_summary.csv").exists())
            self.assertTrue((output / "mzi_voltage_lut.csv").exists())
            self.assertTrue((output / "mzi_calibration_overview.png").exists())
            self.assertTrue((output / "mzi_calibration_curves.pdf").exists())


if __name__ == "__main__":
    unittest.main()
