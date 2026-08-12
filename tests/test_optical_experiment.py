from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from optical_experiment import (
    create_experiment,
    load_manifest,
    save_heater_capture,
    update_manifest,
)
from process_optical_experiment import process_experiment


def metadata():
    return {
        "hardware": {"sample_rate_hz": 1.0e9},
        "acquisition": {
            "adc_channel": 0,
            "dac_channel": 0,
            "capture_bytes_per_chip_per_repetition": 8192,
            "samples_per_channel_per_repetition": 2048,
            "repetitions_per_heater_capture": 16,
            "all_adc_channels_saved": True,
        },
        "xbar": {"sources_by_dac": {"DAC0": "Spike 0"}},
        "stimulus": {
            "neuron": {"index": 0, "profile": "regular"},
            "current_source": {"step_sample": 200, "amplitude_ma": 15.0},
            "spike_pulse": {"length_dac_points": 3},
        },
        "heater_sweep": {
            "heater_net": "h_1_1",
            "planned_voltages_v": [0.0, 0.5, 1.0],
            "planned_directions": [0, 0, 0],
        },
        "detection": {
            "threshold_sigma": 5.0,
            "boundary_sigma": 2.0,
            "minimum_seed_samples": 2,
        },
    }


class OpticalExperimentTests(unittest.TestCase):
    def test_directory_schema_and_offline_curve_processing(self):
        with tempfile.TemporaryDirectory() as directory:
            experiment = create_experiment(
                Path(directory), "Row 1 / calibration", metadata())
            amplitudes = (1000, 5000, 3000)
            for index, (voltage, amplitude) in enumerate(
                    zip((0.0, 0.5, 1.0), amplitudes)):
                stacks = {
                    channel: np.zeros((16, 2048), dtype=np.int16)
                    for channel in range(4)
                }
                for peak in (500, 900, 1400):
                    stacks[0][:, peak - 1:peak + 2] = [
                        amplitude // 2, amplitude, amplitude // 2]
                point = save_heater_capture(
                    experiment, index=index, voltage_v=voltage, direction=0,
                    stacks=stacks, capture_meta={"coverage": [1.0, 1.0]})
                with np.load(point / "raw_captures.npz") as raw:
                    self.assertEqual(raw["raw_ch3"].shape, (16, 2048))
                    self.assertFalse(bool(raw["synthetic"]))
            update_manifest(experiment, capture_status="complete")

            result = process_experiment(experiment)

            np.testing.assert_allclose(result["normalized"], [0.0, 1.0, 0.5])
            self.assertTrue((experiment / "optical_curve.csv").exists())
            self.assertTrue((experiment / "optical_curve.png").exists())
            self.assertTrue((experiment / "analysis_summary.json").exists())
            manifest = load_manifest(experiment)
            self.assertEqual(manifest["experiment_name"], "Row 1 / calibration")
            self.assertEqual(manifest["completed_heater_captures"], 3)
            self.assertEqual(manifest["capture_status"], "complete")
            self.assertEqual(len(manifest["heater_captures"]), 3)
            for descriptor in manifest["heater_captures"]:
                point = experiment / descriptor["directory"]
                self.assertTrue((point / "heater.json").exists())
                self.assertTrue((point / "processed.npz").exists())
                self.assertTrue((point / "spike_measurements.csv").exists())
                self.assertTrue(
                    (point / "independently_detected_spikes.csv").exists())


if __name__ == "__main__":
    unittest.main()
