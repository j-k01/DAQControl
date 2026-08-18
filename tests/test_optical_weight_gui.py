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
        self.current_status = None
        self.force_current_status = None

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
        self.calls.append(("current_step", cps, zero, high, current, hold_last))
        return "OK CURS"

    def program_current(self, samples, cps, hold_last=False):
        values = tuple(float(value) for value in samples)
        self.calls.append(("current_wave", values, cps, hold_last))
        raw = ((int(cps) & 0xFFFF) |
               (((len(values) - 1) & 0x3FF) << 16) |
               ((1 if hold_last else 0) << 26) | (1 << 30))
        self.current_status = {
            "raw": raw, "cps": int(cps), "count": len(values),
            "hold_last": bool(hold_last), "running": True,
        }
        return "OK CURW"

    def get_current_player_status(self, timeout=2.0):
        self.calls.append(("current_status", timeout))
        status = self.force_current_status or self.current_status
        if status is None:
            raise RuntimeError("current player was never programmed")
        return dict(status)


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

    def test_connection(self, probes=5):
        return {"probes": probes, "mean_ms": 1.25, "max_ms": 2.5}

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
        self.assertEqual(self.window.workspace_tabs.tabText(1), "Optical experiment")
        self.assertEqual(self.window.workspace_tabs.tabText(2), "Optical results")
        self.assertNotIn(
            "Optical Weight",
            [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())])
        self.assertEqual(self.window.liveavg_timer.interval(), 25)
        self.assertEqual(self.window.mzi_reps.value(), 16)
        self.assertEqual(self.window.mzi_experiment_name.text(), "optical_sweep")
        self.assertEqual(self.window.mzi_capture_size.currentData(), 64 * 1024)
        self.assertEqual(self.window.mzi_detect_sigma.value(), 5.0)
        self.assertEqual(self.window.mzi_boundary_sigma.value(), 2.0)
        self.assertEqual(self.window.mzi_min_seed.value(), 2)
        self.assertEqual(self.window.mzi_spacing.currentData(), "voltage")
        self.assertEqual(self.window.mzi_points.value(), 20)
        self.assertEqual(self.window.mzi_settle.value(), 20.0)
        pulse = self.window._optical_pulse_counts()
        self.assertEqual(len(pulse), 40)
        self.assertLess(min(pulse), 0)
        self.assertEqual(self.window.mzi_peak_polarity.currentData(), "auto")
        self.assertTrue(self.window.mzi_outlier_filter.isChecked())
        self.assertEqual(self.window.mzi_outlier_sigma.value(), 2.5)
        self.assertEqual(self.window.mzi_trace_y_min.value(), -30.0)
        self.assertEqual(self.window.mzi_trace_y_max.value(), 30.0)
        self.assertEqual(
            [profile.currentText() for profile in self.window.mzi_profiles],
            ["regular"] * 4)
        self.assertEqual(len(self.window._mzi_heater_buttons), 54)
        self.assertEqual(self.window._selected_mzi_nets(), ("h_1_1",))
        self.assertIn("3px solid #EF5350",
                      self.window._mzi_heater_buttons["h_1_1"].styleSheet())
        self.assertIn("-- V", self.window._mzi_heater_buttons["h_1_2"].text())
        spec = self.window._mzi_gui_spec()
        self.assertEqual(spec["current_mode"], "Square")
        self.assertEqual(spec["current_ma"], 15.0)
        self.assertAlmostEqual(spec["current_actual_frequency_hz"], 5000.0)
        self.assertEqual(spec["current_duty_percent"], 50.0)
        self.assertEqual(spec["xbar_sources"],
                         ["Spike 0", "Spike 1", "Spike 2", "Spike 3"])
        self.assertEqual(spec["adc_channels"], [0, 1, 2, 3])
        self.assertEqual(spec["nets"], ("h_1_1",))
        self.assertEqual(spec["sweep_label"], "h_1_1")

    def test_pico_and_heater_controls_do_not_require_uart(self):
        self.window._set_controls_enabled(False)

        self.assertTrue(self.window.mzi_pico_init_btn.isEnabled())
        self.assertTrue(self.window.mzi_pico_test_btn.isEnabled())
        self.assertTrue(self.window.mzi_set_selected_btn.isEnabled())
        self.assertTrue(self.window.mzi_zero_selected_btn.isEnabled())
        self.assertTrue(self.window.mzi_zero_all_btn.isEnabled())
        self.assertTrue(self.window.mzi_config_apply_btn.isEnabled())
        self.assertTrue(self.window.mzi_import_btn.isEnabled())
        self.assertFalse(self.window.mzi_program_btn.isEnabled())
        self.assertFalse(self.window.mzi_quick_btn.isEnabled())

    def test_heater_limits_are_consistent_and_reject_before_controller(self):
        self.assertEqual(self.window.mzi_selected_voltage.maximum(), 1.0)
        self.assertEqual(self.window.mzi_vstart.maximum(), 1.0)
        self.assertEqual(self.window.mzi_vstop.maximum(), 1.0)
        self.assertEqual(self.window.mzi_restore.maximum(), 1.0)
        fake = FakeMziController()
        self.window._mzi_controller = fake

        with self.assertRaisesRegex(ValueError, "outside 0..1 V"):
            self.window._set_mzi_heater_voltages({"h_1_1": 5.0})

        self.assertEqual(fake.voltages, [])

    def test_pico_initialization_feedback(self):
        fake = FakeMziController()
        self.window._mzi_controller = fake

        result = self.window._run_mzi_init_pico()
        self.window._mzi_resume_autosample = False
        self.window._mzi_resume_tap = False
        self.window._on_mzi_cal_result(result)

        self.assertTrue(fake.connected)
        self.assertEqual(result["heater_count"], 54)
        self.assertIn("READY", self.window.mzi_pico_status.text())

    def test_pico_health_feedback(self):
        self.window._mzi_controller = FakeMziController()

        result = self.window._run_mzi_test_pico()
        self.window._mzi_resume_autosample = False
        self.window._mzi_resume_tap = False
        self.window._on_mzi_cal_result(result)

        self.assertEqual(result["probes"], 5)
        self.assertIn("PASS", self.window.mzi_pico_status.text())
        self.assertIn("5/5", self.window.mzi_pico_status.text())

    def test_program_setup_configures_all_neurons_routes_and_square(self):
        fake = FakeDac()
        self.window.dac = fake
        self.window.mzi_profiles[1].setCurrentText("bursting")
        self.window.mzi_profiles[2].setCurrentText("chattering")
        self.window.mzi_profiles[3].setCurrentText("fast")
        spec = self.window._mzi_gui_spec()

        self.window._program_mzi_test(spec)

        for neuron in range(4):
            neuron_params = {
                (call[2], call[3]) for call in fake.calls
                if call[0] == "param" and call[1] == neuron
            }
            self.assertIn(("i", 0), neuron_params)
            self.assertIn(("iconst", 0), neuron_params)
            self.assertIn(("source", neuron, f"Spike {neuron}"), fake.calls)
            pulse = next(call for call in fake.calls
                         if call[0] == "pulse" and call[1] == neuron)
            self.assertEqual(len(pulse[2]), 40)
        current = next(call for call in fake.calls if call[0] == "current_wave")
        samples, cps, hold_last = current[1], current[2], current[3]
        self.assertEqual(len(samples), 1000)
        self.assertEqual(cps, 10)
        self.assertFalse(hold_last)
        self.assertEqual(set(samples), {0.0, 15.0})
        self.assertEqual(samples.count(15.0), 500)
        self.assertIn(("current_status", 2.0), fake.calls)
        self.assertTrue(spec["current_player_readback"]["running"])
        self.assertEqual(spec["current_player_readback"]["count"], 1000)

    def test_optical_editor_windows_stage_profiles_and_pulse(self):
        self.window._open_mzi_neuron_window()
        self.assertTrue(self.window._mzi_neuron_win.isVisible())
        self.window._open_mzi_pulse_window()
        self.assertTrue(self.window._pulse_win.isVisible())
        self.assertEqual(self.window._pulse_win.target_cb.currentData(), "all")
        custom_pulse = [0, 4000, 8000, 4000, 0]
        self.window._pulse_win.editor.set_values(custom_pulse)
        spec = self.window._mzi_gui_spec()
        self.assertEqual(spec["pulse_counts"], custom_pulse)

        fake = FakeDac()
        self.window.dac = fake
        self.window._program_mzi_test(spec)
        programmed = [
            call[2] for call in fake.calls if call[0] == "pulse"]
        self.assertEqual(programmed, [tuple(custom_pulse)] * 4)
    def test_program_setup_rejects_false_current_player_success(self):
        fake = FakeDac()
        fake.force_current_status = {
            "raw": (999 << 16) | 10,
            "cps": 10,
            "count": 1000,
            "hold_last": False,
            "running": False,
        }
        self.window.dac = fake

        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            self.window._program_mzi_test(self.window._mzi_gui_spec())

    def test_engine_timeout_is_reported_as_adc_path_failure(self):
        reply = ("ERR BCPT timeout (engine) rep=0 "
                 "st0=0xBC542000 st1=0xBC542000")
        message = dac_scope_qt.describe_burst_capture_failure("BCPT", reply)

        self.assertIn("ADC/JESD data-path failure", message)
        self.assertIn("remaining beats 8192/8192", message)
        self.assertNotIn("must be configured", message)
    def test_heater_map_multi_selection_and_programming(self):
        fake_mzi = FakeMziController()
        self.window.dac = FakeDac()
        self.window._mzi_controller = fake_mzi
        self.window._mzi_heater_buttons["h_1_2"].setChecked(True)
        self.window.mzi_selected_voltage.setValue(0.375)
        spec = self.window._mzi_gui_spec()

        result = self.window._run_mzi_set_heaters(
            {net: self.window.mzi_selected_voltage.value()
             for net in self.window._selected_mzi_nets()})
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
        self.assertIn("acknowledged all 2", self.window.mzi_write_status.text())

    def test_zero_voltage_is_visible_after_acknowledgement(self):
        self.window._mzi_controller = FakeMziController()
        result = self.window._run_mzi_set_heaters({"h_1_1": 0.0})
        self.window._mzi_resume_autosample = False
        self.window._mzi_resume_tap = False
        self.window._on_mzi_cal_result(result)

        self.assertIn("0.000 V", self.window._mzi_heater_buttons["h_1_1"].text())
        self.assertIn("background: #245C3D",
                      self.window._mzi_heater_buttons["h_1_1"].styleSheet())
        self.assertIn("PASS", self.window.mzi_write_status.text())

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
        self.assertEqual(result["kind"], "heater_set")
        self.window._mzi_resume_autosample = False
        self.window._mzi_resume_tap = False
        self.window._on_mzi_cal_result(result)
        self.assertIn("failed", self.window.mzi_write_status.text())
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
            self.assertEqual(saved["raw_ch0"].shape, (16, 16384))
            self.assertEqual(saved["raw_ch3"].shape, (16, 16384))
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
        self.window._mzi_resume_autosample = False
        self.window._mzi_resume_tap = False
        self.window._on_mzi_cal_result(result)
        self.assertEqual(len(self.window.mzi_sweep_trace_plots), 3)
        self.assertEqual(self.window.workspace_tabs.currentIndex(), 2)

    def test_saved_experiment_imports_without_board_connection(self):
        fake_mzi = FakeMziController()
        self.window.dac = FakeDac()
        self.window._mzi_controller = fake_mzi
        self.window._multisample_once = self._capture_for(fake_mzi)
        spec = self.window._mzi_gui_spec()
        spec.update(
            voltages=np.asarray([0.0, 0.5, 1.0]),
            directions=np.asarray([0, 0, 0], dtype=np.int8),
            spacing="voltage")
        captured = self.window._run_mzi_calibration(spec)
        experiment = captured["path"]

        self.window.dac = None
        imported = self.window._run_mzi_import_experiment(experiment)
        self.window._on_mzi_import_result(imported)

        self.assertNotIn("_err", imported)
        self.assertEqual(imported["kind"], "imported_sweep")
        self.assertEqual(len(imported["measurements"]), 3)
        self.assertEqual(self.window.mzi_dataset_combo.count(), 1)
        self.assertEqual(len(self.window.mzi_sweep_trace_plots), 3)
        self.assertEqual(self.window.workspace_tabs.currentIndex(), 2)
        self.assertIn("Loaded", self.window.mzi_dataset_status.text())
        self.window._on_mzi_import_result(imported)
        self.assertEqual(self.window.mzi_dataset_combo.count(), 1)
    def test_checked_heaters_are_swept_together(self):
        fake_dac = FakeDac()
        fake_mzi = FakeMziController()
        self.window.dac = fake_dac
        self.window._mzi_controller = fake_mzi
        self.window._mzi_heater_buttons["h_1_2"].setChecked(True)
        self.window._multisample_once = self._capture_for(fake_mzi)
        spec = self.window._mzi_gui_spec()
        spec.update(
            voltages=np.asarray([0.0, 0.5]),
            directions=np.asarray([0, 0], dtype=np.int8),
            spacing="voltage")

        result = self.window._run_mzi_calibration(spec)

        self.assertNotIn("_err", result)
        manifest = json.loads((Path(result["path"]) / "experiment.json").read_text())
        self.assertEqual(
            manifest["heater_sweep"]["heater_nets"], ["h_1_1", "h_1_2"])
        self.assertTrue(manifest["heater_sweep"]["shared_sweep_voltage"])
        self.assertEqual(spec["nets"], ("h_1_1", "h_1_2"))
        self.assertEqual(fake_mzi.voltages[:4], [
            ("h_1_1", 0.0), ("h_1_2", 0.0),
            ("h_1_1", 0.5), ("h_1_2", 0.5),
        ])
        self.assertIn("3px solid #EF5350",
                      self.window._mzi_heater_buttons["h_1_1"].styleSheet())
        self.assertIn("3px solid #EF5350",
                      self.window._mzi_heater_buttons["h_1_2"].styleSheet())

    def test_row_header_selects_six_heaters_for_sweep(self):
        self.window._mzi_heater_buttons["h_1_1"].setChecked(False)
        row = [f"h_8_{column}" for column in range(1, 7)]

        self.window._on_mzi_group_toggle(row)

        self.assertEqual(self.window._selected_mzi_nets(), tuple(row))
        self.assertEqual(self.window._mzi_gui_spec()["nets"], tuple(row))
        for net in row:
            self.assertIn(
                "3px solid #EF5350",
                self.window._mzi_heater_buttons[net].styleSheet())

    def test_empty_heater_selection_cannot_start_sweep(self):
        self.window._mzi_heater_buttons["h_1_1"].setChecked(False)

        with self.assertRaisesRegex(ValueError, "at least one heater"):
            self.window._mzi_gui_spec()


if __name__ == "__main__":
    unittest.main()
