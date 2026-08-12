from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if not SCRIPTS.exists():
    SCRIPTS = Path.cwd() / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import dac_scope_qt
from dac_scope_qt import (
    MZI_NET_NAMES, QtWidgets, ScopeWindow, heater_mapping_payload)


class FakeDac:
    def __init__(self):
        self.calls = []

    def set_neuron_timing(self, dt_hex, period=1):
        self.calls.append(("timing", dt_hex, period))
        return ["OK NEUR", "OK NEUR"]

    def set_neuron_param(self, target, param, q16):
        self.calls.append(("param", target, param, q16))
        return "OK NEUR"

    def program_pulse(self, counts, target="all"):
        self.calls.append(("pulse", target, tuple(counts)))
        return "PULS OK"

    def set_spike_cal(self, target, gain, offset):
        self.calls.append(("scal", target, gain, offset))
        return "OK SCAL"

    def set_source(self, channel, source):
        self.calls.append(("source", channel, source))
        return "OK NSRC"

    def program_current_step(self, cps, zero, high, current, hold_last=True):
        self.calls.append(("current", cps, zero, high, current, hold_last))
        return "OK CURS"


class FakeMziController:
    def __init__(self):
        self.connected = False
        self.voltages = []

    def connect(self, **_kwargs):
        self.connected = True

    def available_nets(self):
        return MZI_NET_NAMES

    def set_voltage(self, net, voltage):
        self.voltages.append((net, voltage))

    def set_voltages(self, voltages, *, on_sent=None):
        for net, voltage in voltages.items():
            self.voltages.append((net, voltage))
            if on_sent is not None:
                on_sent(net, voltage)

    def close(self):
        self.connected = False


class OpticalWeightGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.capture_dir = tempfile.TemporaryDirectory()
        self.old_configs_path = dac_scope_qt.HEATER_CONFIGS_PATH
        dac_scope_qt.HEATER_CONFIGS_PATH = str(
            Path(self.capture_dir.name) / "configs.json")
        args = SimpleNamespace(
            port="COM10", board_ip="192.168.2.10", cmd_port=5006,
            local_ip="192.168.2.1", local_port=5005, decim=128,
            window=8192, time_span=1024, rcvbuf=1 << 20, initial="DDS",
            cic=False, fps=20.0, capture_dir=self.capture_dir.name,
            autoconnect=False,
        )
        self.window = ScopeWindow(args)
        self.window.timer.stop()

    def tearDown(self):
        self.window.dac = None
        self.window.close()
        dac_scope_qt.HEATER_CONFIGS_PATH = self.old_configs_path
        self.capture_dir.cleanup()

    def test_optical_test_defaults(self):
        self.assertEqual(self.window.tabs.tabText(3), "Optical Weight")
        self.assertEqual(self.window.liveavg_timer.interval(), 25)
        self.assertEqual(self.window.mzi_current_ma.value(), 15.0)
        self.assertEqual(self.window.mzi_reps.value(), 16)
        self.assertEqual(self.window.mzi_experiment_name.text(), "optical_sweep")
        self.assertEqual(self.window.mzi_capture_size.currentData(), 64 * 1024)
        self.assertEqual(self.window.mzi_detect_sigma.value(), 5.0)
        self.assertEqual(self.window.mzi_boundary_sigma.value(), 2.0)
        self.assertEqual(self.window.mzi_min_seed.value(), 2)
        self.assertEqual(self.window.mzi_spacing.currentData(), "power")
        self.assertEqual(len(self.window._mzi_heater_buttons), 54)
        self.assertEqual(self.window._selected_mzi_nets(), ("h_1_1",))
        self.assertIn("Pico-acknowledged V", self.window.mzi_heater_group.title())
        self.assertIn("-- V", self.window._mzi_heater_buttons["h_1_2"].text())
        self.assertIn("background: #474D53",
                      self.window._mzi_heater_buttons["h_1_2"].styleSheet())
        spec = self.window._mzi_gui_spec()
        self.assertEqual(spec["zero_count"] + spec["high_count"], 1024)

    def test_program_test_zeros_both_static_currents(self):
        fake = FakeDac()
        self.window.dac = fake
        spec = self.window._mzi_gui_spec()

        self.window._program_mzi_test(spec)

        params = {(call[2], call[3]) for call in fake.calls if call[0] == "param"}
        self.assertIn(("i", 0), params)
        self.assertIn(("iconst", 0), params)
        self.assertIn(("source", 0, "Spike 0"), fake.calls)
        self.assertIn(("current", 1, 256, 768, 15.0, True), fake.calls)
        pulse = next(call for call in fake.calls if call[0] == "pulse")
        self.assertEqual(pulse[1], 0)
        self.assertEqual(len(pulse[2]), 50)

    def test_heater_map_multi_selection_and_programming(self):
        fake_mzi = FakeMziController()
        self.window.dac = FakeDac()
        self.window._mzi_controller = fake_mzi
        self.window._mzi_heater_buttons["h_1_2"].setChecked(True)
        self.window.mzi_selected_voltage.setValue(0.375)
        spec = self.window._mzi_gui_spec()

        result = self.window._run_mzi_set_heaters(
            {net: self.window.mzi_selected_voltage.value()
             for net in spec["nets"]})
        self.window._mzi_resume_autosample = False
        self.window._mzi_resume_tap = False
        self.window._on_mzi_cal_result(result)

        self.assertEqual(spec["nets"], ("h_1_1", "h_1_2"))
        self.assertEqual(fake_mzi.voltages, [
            ("h_1_1", 0.375), ("h_1_2", 0.375)])
        self.assertEqual(self.window._mzi_heater_voltages["h_1_1"], 0.375)
        self.assertEqual(self.window._mzi_heater_voltages["h_1_2"], 0.375)
        self.assertIn("0.375", self.window._mzi_heater_buttons["h_1_2"].text())
        self.assertIn("background: #245C3D",
                      self.window._mzi_heater_buttons["h_1_2"].styleSheet())

    def test_partial_spi_failure_updates_only_successful_heater(self):
        class FailingMziController(FakeMziController):
            def set_voltages(self, voltages, *, on_sent=None):
                for index, (net, voltage) in enumerate(voltages.items()):
                    if index == 1:
                        raise RuntimeError("simulated SPI failure")
                    self.voltages.append((net, voltage))
                    if on_sent is not None:
                        on_sent(net, voltage)

        self.window._mzi_controller = FailingMziController()
        result = self.window._run_mzi_set_heaters({
            "h_1_1": 0.25,
            "h_1_2": 0.50,
        })

        self.assertIn("_err", result)
        self.assertEqual(self.window._mzi_heater_voltages["h_1_1"], 0.25)
        self.assertIsNone(self.window._mzi_heater_voltages["h_1_2"])
        self.assertIn("background: #245C3D",
                      self.window._mzi_heater_buttons["h_1_1"].styleSheet())
        self.assertIn("background: #474D53",
                      self.window._mzi_heater_buttons["h_1_2"].styleSheet())

    def test_named_config_load_stages_without_programming(self):
        self.window._mzi_heater_voltages = {
            net: 0.0 for net in MZI_NET_NAMES}
        self.window._mzi_heater_voltages["h_1_1"] = 0.2
        self.window._mzi_heater_voltages["h_1_2"] = 0.4
        self.window._mzi_selected_heaters = {"h_1_1", "h_1_2"}
        self.window.mzi_config_name.setText("row pair")
        self.window._on_mzi_config_save()

        saved = json.loads(Path(dac_scope_qt.HEATER_CONFIGS_PATH).read_text())
        self.assertEqual(saved["row pair"]["heater_voltages_v"]["h_1_2"], 0.4)
        self.window._mzi_heater_voltages["h_1_1"] = 0.0
        self.window._mzi_heater_voltages["h_1_2"] = 0.0
        self.window._on_mzi_config_load()

        self.assertEqual(self.window._mzi_heater_voltages["h_1_2"], 0.0)
        self.assertEqual(self.window._mzi_staged_heater_voltages["h_1_2"], 0.4)
        self.assertEqual(self.window._selected_mzi_nets(), ("h_1_1", "h_1_2"))
        self.assertIn("not applied", self.window.mzi_config_status.text())

    def test_mapping_export_payload_contains_physical_channels(self):
        payload = heater_mapping_payload({"h_1_1": 0.25})

        self.assertEqual(payload["pico"], "PICO-002")
        self.assertEqual(payload["boards"]["EVAL0"], {"uid": 0, "cs_pin": 17})
        self.assertEqual(payload["heaters"]["h_1_1"]["board"], "EVAL0")
        self.assertEqual(payload["heaters"]["h_1_1"]["channel"], 29)
        self.assertEqual(payload["heaters"]["h_1_1"]["commanded_voltage_v"], 0.25)

    @staticmethod
    def _capture_for(fake_mzi):
        def capture(_nbytes, repetitions):
            voltage = fake_mzi.voltages[-1][1]
            amplitude = int(1000 + 4000 * voltage)
            stack = {
                channel: np.zeros((repetitions, 16384), dtype=np.int16)
                for channel in range(4)
            }
            for peak in (6000, 9000, 12000):
                stack[0][:, peak - 1:peak + 2] = [
                    amplitude // 2, amplitude, amplitude // 2]
            return {
                "stack": stack, "offs": np.zeros(repetitions, dtype=np.int32),
                "meta": {"cov": 1.0, "reps": repetitions},
                "diag": {"anchor": 0, "observable": True,
                         "offsets": np.zeros(repetitions, dtype=np.int32)},
            }
        return capture

    def test_selected_voltage_reuses_aligned_capture_and_saves_average(self):
        fake_dac = FakeDac()
        fake_mzi = FakeMziController()
        self.window.dac = fake_dac
        self.window._mzi_controller = fake_mzi
        calls = []

        def capture(nbytes, repetitions):
            calls.append((nbytes, repetitions))
            return self._capture_for(fake_mzi)(nbytes, repetitions)

        self.window._multisample_once = capture
        spec = self.window._mzi_gui_spec()
        result = self.window._run_mzi_point(spec, 0.375)

        self.assertEqual(result["kind"], "point")
        self.assertEqual(calls, [(64 * 1024, 16)])
        self.assertEqual(fake_mzi.voltages, [("h_1_1", 0.375)])
        with np.load(result["path"]) as saved:
            self.assertEqual(saved["raw_adc_counts"].shape, (16, 16384))
            self.assertEqual(saved["averaged_waveform_v"].shape, (16384,))
            np.testing.assert_array_equal(
                saved["peak_indices"], [6000, 9000, 12000])
            np.testing.assert_array_equal(
                saved["spike_start_indices"], [5999, 8999, 11999])
            np.testing.assert_array_equal(
                saved["spike_end_indices"], [6001, 9001, 12001])

    def test_named_sweep_writes_raw_heater_directories_and_curve(self):
        fake_dac = FakeDac()
        fake_mzi = FakeMziController()
        self.window.dac = fake_dac
        self.window._mzi_controller = fake_mzi
        self.window.mzi_experiment_name.setText("named row test")
        self.window._multisample_once = self._capture_for(fake_mzi)
        spec = self.window._mzi_gui_spec()
        spec.update(
            voltages=np.asarray([0.0, 0.5, 1.0]),
            directions=np.asarray([0, 0, 0], dtype=np.int8),
            spacing="voltage")

        result = self.window._run_mzi_calibration(spec)

        self.assertEqual(result["kind"], "sweep")
        experiment = Path(result["path"])
        self.assertTrue((experiment / "experiment.json").exists())
        self.assertTrue((experiment / "optical_curve.png").exists())
        manifest = json.loads((experiment / "experiment.json").read_text())
        self.assertEqual(manifest["experiment_name"], "named row test")
        self.assertEqual(manifest["capture_status"], "complete")
        self.assertEqual(manifest["heater_sweep"]["heater_nets"], ["h_1_1"])
        heaters = sorted(experiment.glob("heater_*"))
        self.assertEqual(len(heaters), 3)
        with np.load(heaters[0] / "raw_captures.npz") as raw:
            self.assertEqual(raw["raw_ch0"].shape, (16, 16384))
            self.assertEqual(raw["raw_ch3"].shape, (16, 16384))
        np.testing.assert_allclose(result["normalized"], [0.0, 0.5, 1.0])

    def test_multi_heater_sweep_records_all_selected_heaters(self):
        fake_dac = FakeDac()
        fake_mzi = FakeMziController()
        self.window.dac = fake_dac
        self.window._mzi_controller = fake_mzi
        self.window._mzi_selected_heaters = {"h_1_1", "h_1_2"}
        self.window._multisample_once = self._capture_for(fake_mzi)
        spec = self.window._mzi_gui_spec()
        spec.update(
            voltages=np.asarray([0.0, 0.5]),
            directions=np.asarray([0, 0], dtype=np.int8),
            spacing="voltage")

        result = self.window._run_mzi_calibration(spec)

        self.assertNotIn("_err", result)
        manifest = json.loads((Path(result["path"]) / "experiment.json").read_text())
        self.assertEqual(manifest["heater_sweep"]["heater_nets"],
                         ["h_1_1", "h_1_2"])
        self.assertTrue(manifest["heater_sweep"]["shared_sweep_voltage"])
        self.assertEqual(fake_mzi.voltages[-2:], [
            ("h_1_1", 0.0), ("h_1_2", 0.0)])


if __name__ == "__main__":
    unittest.main()
