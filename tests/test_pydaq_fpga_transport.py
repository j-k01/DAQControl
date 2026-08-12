from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

import fpga_pico_serial
import pydaq_fpga_transport


class FakeConnection:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False

    def close(self):
        self.closed = True


def fake_pydaq_module():
    return SimpleNamespace(
        serial=SimpleNamespace(name="real pyserial"),
        find_boards=lambda: None,
        config_detected_devices=lambda boards: None,
        _board_dict={},
    )


class PydaqFpgaTransportTests(unittest.TestCase):
    def install_on(self, module, **kwargs):
        with mock.patch(
            "pydaq_fpga_transport.importlib.import_module", return_value=module
        ):
            return pydaq_fpga_transport.install(**kwargs)

    def test_install_exposes_virtual_port_without_global_serial_patch(self):
        module = fake_pydaq_module()
        original = module.serial
        installation = self.install_on(module)

        ports = module.serial.tools.list_ports.comports()
        self.assertEqual([port.device for port in ports], ["FPGA-PICO"])
        self.assertIs(module.serial.SerialException, fpga_pico_serial.SerialException)
        installation.uninstall()
        self.assertIs(module.serial, original)

    def test_factory_forces_configured_bridge_endpoint(self):
        module = fake_pydaq_module()
        with mock.patch(
            "pydaq_fpga_transport.fpga_pico_serial.Serial", FakeConnection
        ):
            installation = self.install_on(
                module,
                board_ip="192.0.2.10",
                local_ip="192.0.2.1",
                udp_port=5050,
            )
            connection = module.serial.Serial(
                "FPGA-PICO", 115200, timeout=1, exclusive=True
            )

        self.assertEqual(connection.args, ("FPGA-PICO", 115200))
        self.assertEqual(connection.kwargs["board_ip"], "192.0.2.10")
        self.assertEqual(connection.kwargs["local_ip"], "192.0.2.1")
        self.assertEqual(connection.kwargs["udp_port"], 5050)
        self.assertEqual(connection.kwargs["transport"], "ethernet")
        self.assertEqual(connection.kwargs["timeout"], 1)
        self.assertTrue(connection.kwargs["exclusive"])
        installation.uninstall()

    def test_install_must_precede_discovery(self):
        module = fake_pydaq_module()
        module._board_dict["PICO-003"] = FakeConnection()
        with self.assertRaisesRegex(RuntimeError, "already detected"):
            self.install_on(module)

    def test_uninstall_closes_connections_only_when_requested(self):
        module = fake_pydaq_module()
        original = module.serial
        installation = self.install_on(module)
        connection = FakeConnection()
        module._board_dict["PICO-003"] = connection

        with self.assertRaisesRegex(RuntimeError, "still has detected boards"):
            installation.uninstall()
        installation.uninstall(close_connections=True)

        self.assertTrue(connection.closed)
        self.assertEqual(module._board_dict, {})
        self.assertIs(module.serial, original)

    def test_configuration_validation(self):
        module = fake_pydaq_module()
        with self.assertRaisesRegex(ValueError, "transport"):
            self.install_on(module, transport="invalid")
        with self.assertRaisesRegex(ValueError, "udp_port"):
            self.install_on(module, udp_port=0)
