from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pico_usb.load_and_test import (
    discover_local_xsdb,
    local_xsdb_command,
    make_tcl,
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


if __name__ == "__main__":
    unittest.main()