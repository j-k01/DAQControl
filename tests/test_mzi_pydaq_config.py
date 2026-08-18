from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


class FakePin:
    def __init__(self, name, channel):
        self.net_name = name
        self.channel = channel
        self.chnl = channel
        self.writes = []

    def vout(self, voltage):
        self.writes.append(float(voltage))


class FakeBoard:
    def __init__(self, name, uid, _cs_pin, *pins):
        self.name = name
        self.uid = uid
        self.pins = {pin.net_name: pin for pin in pins}
        self.ack_response = "ACK"
        for pin in pins:
            pin.board = self

    def _expect_ack(self, command):
        prefix, channel, voltage = command.strip().split(",")
        if prefix != f"W{self.uid}":
            raise RuntimeError(f"wrong board command {command!r}")
        pin = next(pin for pin in self.pins.values()
                   if pin.channel == int(channel))
        pin.writes.append(float(voltage))
        return self.ack_response


class FakeManager:
    def __init__(self, _name, *boards):
        self.pins = {
            name: pin for board in boards for name, pin in board.pins.items()
        }


class FakeNetlist:
    def __init__(self, manager):
        self.pins_dict = dict(manager.pins)


class MziPydaqConfigTests(unittest.TestCase):
    def test_complete_configuration_has_guard_between_spi_writes(self):
        pydaq = ModuleType("pydaq")
        daq = ModuleType("pydaq.daq")
        ser = ModuleType("pydaq.ser")
        daq.Netlist = FakeNetlist
        ser.AOPIN = FakePin
        ser.EVAL_AD5370 = FakeBoard
        ser.BoardManager = FakeManager
        ser.config_detected_devices = lambda *_args, **_kwargs: None
        pydaq.daq = daq
        pydaq.ser = ser
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        spec = importlib.util.spec_from_file_location(
            "mzi_pydaq_config_guard_test", scripts / "mzi_pydaq_config.py")
        module = importlib.util.module_from_spec(spec)

        with patch.dict(sys.modules, {
                "pydaq": pydaq, "pydaq.daq": daq, "pydaq.ser": ser}):
            spec.loader.exec_module(module)

        sleeps = []
        sent = []
        with patch.object(module.time, "sleep", side_effect=sleeps.append):
            module.set_mzi_voltages(
                {"h_1_1": 0.25, "h_1_2": 0.50},
                on_sent=lambda net, voltage: sent.append((net, voltage)))

        self.assertEqual(sleeps, [module.HEATER_SPI_GUARD_S])
        self.assertEqual(module.netlist.pins_dict["h_1_1"].writes, [0.25])
        self.assertEqual(module.netlist.pins_dict["h_1_2"].writes, [0.50])
        self.assertEqual(sent, [("h_1_1", 0.25), ("h_1_2", 0.50)])

        writes_before = {
            net: list(module.netlist.pins_dict[net].writes)
            for net in ("h_1_1", "h_1_2")
        }
        sent.clear()
        with self.assertRaisesRegex(ValueError, "outside 0..1 V"):
            module.set_mzi_voltages(
                {"h_1_1": 1.0001, "h_1_2": 0.50},
                on_sent=lambda net, voltage: sent.append((net, voltage)))
        self.assertEqual(sent, [])
        for net, previous in writes_before.items():
            self.assertEqual(module.netlist.pins_dict[net].writes, previous)

        module.eval0.ack_response = ""
        sent.clear()
        with self.assertRaisesRegex(RuntimeError, "not acknowledged"):
            module.set_mzi_voltages(
                {"h_1_1": 0.75},
                on_sent=lambda net, voltage: sent.append((net, voltage)))
        self.assertEqual(sent, [])
        self.assertEqual(module.netlist.pins_dict["h_1_1"].writes,
                         [0.25, 0.75])


if __name__ == "__main__":
    unittest.main()
