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

from headless_tone_characterization import (  # noqa: E402
    CharacterizationConfig,
    VOLTS_PER_COUNT,
    automatic_capture_kb,
    run_characterization,
    select_heaters,
)
from mzi_heater_map import MZI_NET_NAMES  # noqa: E402
from optical_experiment import load_manifest  # noqa: E402
from tone_calibration import dds_phase_increment  # noqa: E402


class FakeDaq:
    def __init__(self):
        self.configurations = []

    def configure_tone(self, input_dac, frequency_hz):
        increment, actual = dds_phase_increment(frequency_hz)
        sources = [
            "DDS" if channel == input_dac else "Off"
            for channel in range(3)
        ] + ["DDS"]
        self.configurations.append((input_dac, frequency_hz, sources))
        register = sum(
            (1 if source == "DDS" else 0) << (4 * channel)
            for channel, source in enumerate(sources))
        return {
            "sources": sources,
            "register17": register,
            "phase_increment": increment,
            "actual_frequency_hz": actual,
            "dds_reply": "DDS inc=fake",
        }


class FakeHeaters:
    def __init__(self):
        self.connected = True
        self.current = {net: -1.0 for net in MZI_NET_NAMES}
        self.full_writes = []
        self.single_writes = []

    def available_nets(self):
        return MZI_NET_NAMES

    def set_voltages(self, values):
        values = dict(values)
        self.full_writes.append(values)
        self.current.update(values)

    def set_voltage(self, net, value):
        self.single_writes.append((net, float(value)))
        self.current[net] = float(value)


def synthetic_capture(config, heaters):
    repetitions = config.repetitions
    samples = config.capture_bytes // 4
    _increment, frequency = dds_phase_increment(config.frequency_hz)
    n = np.arange(samples, dtype=np.float64)
    phase = 2.0 * np.pi * frequency * n / 1.0e9
    active = [
        (net, voltage) for net, voltage in heaters.current.items()
        if voltage != 0.0
    ]
    selected_voltage = active[0][1] if active else 0.0
    amplitudes_v = {
        0: 0.002 + 0.004 * selected_voltage,
        1: 0.003 + 0.002 * selected_voltage,
        2: 0.001 + 0.001 * selected_voltage,
        3: 0.40,
    }
    stacks = {}
    for channel, amplitude in amplitudes_v.items():
        counts = np.rint(
            amplitude * np.sin(phase - 0.1 * channel) /
            VOLTS_PER_COUNT).astype(np.int16)
        stacks[channel] = np.stack(
            [counts for _ in range(repetitions)])
    return {
        "stack": stacks,
        "meta": {
            "reps": repetitions,
            "bytes_per_rep": config.capture_bytes,
            "stride": config.capture_bytes,
            "total_per_chip": config.capture_bytes * repetitions,
            "coverage_by_chip": [1.0, 1.0],
            "drain_attempts": 1,
        },
    }


class HeadlessToneCharacterizationTests(unittest.TestCase):
    def test_heater_selection_supports_rows_and_explicit_nets(self):
        self.assertEqual(
            select_heaters("h_7_3,h_1_2", None),
            ("h_1_2", "h_7_3"))
        row_nets = select_heaters(None, "2,7-8")
        self.assertEqual(len(row_nets), 18)
        self.assertEqual(
            {int(net.split("_")[1]) for net in row_nets}, {2, 7, 8})
        self.assertEqual(
            select_heaters(None, None, 0),
            tuple(f"h_7_{column}" for column in range(1, 7)))
        self.assertEqual(
            select_heaters(None, None, 1),
            tuple(f"h_8_{column}" for column in range(1, 7)))
        self.assertEqual(
            select_heaters(None, None, 2),
            tuple(f"h_9_{column}" for column in range(1, 7)))
        self.assertEqual(select_heaters("all", None, 0), MZI_NET_NAMES)

    def test_low_frequency_auto_capture_contains_enough_cycles(self):
        self.assertEqual(automatic_capture_kb(100.0e3), 640)
        self.assertEqual(automatic_capture_kb(1.0e6), 64)
        self.assertEqual(automatic_capture_kb(10.0e3), 1024)

    def test_each_heater_sweep_starts_and_ends_with_explicit_all_zero_drive(self):
        with tempfile.TemporaryDirectory() as directory:
            config = CharacterizationConfig(
                port="COM-test",
                input_dac=0,
                frequency_hz=1.0e6,
                repetitions=4,
                capture_kb=8,
                heater_nets=("h_1_1", "h_7_3"),
                start_v=0.0,
                stop_v=0.5,
                points=2,
                reverse=False,
                point_settle_s=0.0,
                baseline_settle_s=0.0,
                capture_root=Path(directory),
                name="headless test",
            )
            daq = FakeDaq()
            heaters = FakeHeaters()

            batch = run_characterization(
                config,
                daq=daq,
                heater_controller=heaters,
                capture_function=lambda: synthetic_capture(config, heaters),
                sleep=lambda _seconds: None)

            self.assertEqual(daq.configurations[0][0], 0)
            self.assertEqual(
                daq.configurations[0][2], ["DDS", "Off", "Off", "DDS"])
            self.assertGreaterEqual(len(heaters.full_writes), 5)
            for write in heaters.full_writes:
                self.assertEqual(set(write), set(MZI_NET_NAMES))
                self.assertTrue(all(value == 0.0 for value in write.values()))
            self.assertTrue(all(value == 0.0 for value in heaters.current.values()))

            batch_manifest = json.loads(
                (batch / "characterization.json").read_text(encoding="utf-8"))
            self.assertEqual(batch_manifest["capture_status"], "complete")
            self.assertEqual(len(batch_manifest["experiments"]), 2)
            self.assertIn(
                "not assumed to mean zero optical weight",
                batch_manifest["baseline_interpretation"])

            for record in batch_manifest["experiments"]:
                experiment = batch / record["directory"]
                manifest = load_manifest(experiment)
                self.assertEqual(manifest["schema"], "daq_optical_sweep")
                self.assertEqual(
                    manifest["stimulus"]["mode"], "shared_dds_pure_tone")
                self.assertEqual(manifest["capture_status"], "complete")
                self.assertEqual(len(manifest["heater_captures"]), 2)
                baseline = manifest["heater_sweep"][
                    "heater_voltages_before_sweep"]
                self.assertEqual(set(baseline), set(MZI_NET_NAMES))
                self.assertTrue(all(value == 0.0 for value in baseline.values()))
                self.assertTrue((experiment / "tone_summary.npz").exists())
                point = experiment / manifest["heater_captures"][1]["directory"]
                with np.load(point / "raw_captures.npz") as raw:
                    self.assertEqual(raw["raw_ch0"].shape, (4, 2048))
                point_metadata = json.loads(
                    (point / "heater.json").read_text(encoding="utf-8"))
                live = point_metadata["capture_meta"]["heater_voltages_v"]
                selected = manifest["heater_sweep"]["primary_heater_net"]
                self.assertEqual(live[selected], 0.5)
                self.assertTrue(all(
                    value == 0.0 for net, value in live.items()
                    if net != selected))


if __name__ == "__main__":
    unittest.main()
