from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import pico_usb.load_and_test as loader
from pico_usb.load_and_test import (
    discover_local_xsdb,
    local_xsdb_command,
    make_tcl,
    rank_serial_ports,
    require_artifacts,
)


class PicoLoaderTests(unittest.TestCase):
    def test_local_tcl_uses_staged_artifact_directory(self):
        text = make_tcl("C:/Temp/daq pico/artifacts")

        self.assertIn('set work_dir "C:/Temp/daq pico/artifacts"', text)
        self.assertIn("fpga -file $bitstream", text)
        self.assertIn("dow -data $initramfs $initramfs_addr", text)

    def test_explicit_local_xsdb_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "xsdb.bat"
            tool.write_text("@echo off\n", encoding="ascii")

            self.assertEqual(discover_local_xsdb(str(tool)), tool.resolve())

    def test_batch_xsdb_runs_through_cmd(self):
        command = local_xsdb_command(
            Path("C:/Xilinx/2024.1/Vivado/bin/xsdb.bat"),
            Path("C:/Temp/load_usb_host.tcl"),
        )

        self.assertEqual(command[:3], ["cmd.exe", "/d", "/c"])
        self.assertTrue(command[3].endswith("xsdb.bat"))
        self.assertTrue(command[4].endswith("load_usb_host.tcl"))

    def test_runtime_artifact_manifest_is_complete(self):
        paths = require_artifacts()

        self.assertEqual(len(paths), 11)
        self.assertTrue(all(path.is_file() for path in paths.values()))

    def test_cp2108_interfaces_are_ranked_for_the_requested_role(self):
        ports = [
            SimpleNamespace(device="COM11", description="CP2108 Interface 0",
                            interface="", hwid=""),
            SimpleNamespace(device="COM9", description="CP2108 Interface 2",
                            interface="", hwid=""),
            SimpleNamespace(device="COM3", description="USB Serial Device",
                            interface="", hwid=""),
        ]

        self.assertEqual(rank_serial_ports(ports, 0)[0], "COM11")
        self.assertEqual(rank_serial_ports(ports, 2)[0], "COM9")

    def test_uboot_is_interrupted_before_xsdb_returns(self):
        import threading

        output = bytearray(b"Hit any key to stop autoboot:  2")
        lock = threading.Lock()
        done = threading.Event()

        class FakePort:
            def __init__(self):
                self.writes = []

            def write(self, data):
                self.writes.append(data)
                with lock:
                    output.extend(b"\r\nZynqMP>")

            def flush(self):
                pass

        port = FakePort()
        loader.interrupt_uboot(port, output, lock, done)

        self.assertEqual(port.writes, [b" "])

    def test_daq_runtime_requires_real_jesd_and_burst_success(self):
        class FakeSerial:
            def __init__(self, *_args, **_kwargs):
                self.lines = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def reset_input_buffer(self):
                self.lines.clear()

            def write(self, data):
                command = data.decode("ascii").strip()
                if command == "RDRW 17":
                    self.lines.append(b"REG17=0x1111\r\n")
                elif command == "STAT":
                    flags = " ".join(sorted(loader.GTH_REQUIRED))
                    self.lines.append(f"gth_gate: {flags}\r\n".encode("ascii"))
                elif command == "BCAP 64k":
                    self.lines.append(
                        b"OK BCAP bytes_per_chip=65536 beats=4096\r\n")

            def flush(self):
                pass

            def readline(self):
                return self.lines.pop(0) if self.lines else b""

        with patch.object(loader.serial, "Serial", FakeSerial):
            port, reply = loader.verify_daq_runtime("COM9", "COM11")

        self.assertEqual(port, "COM9")
        self.assertTrue(reply.startswith("OK BCAP"))


if __name__ == "__main__":
    unittest.main()