#!/usr/bin/env python3
"""Generate the LiteJESD204B DAC TX Verilog wrapper.

The Vivado project consumes the generated Verilog in src/jesd. Migen/LiteX are
only needed when regenerating this file.
"""

from pathlib import Path
import argparse
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
LITEJESD_SRC = ROOT / "third_party" / "litejesd204b"
DEFAULT_OUTPUT = ROOT / "src" / "jesd" / "litejesd_dac_tx.v"


def import_generator_dependencies():
    extra_pydeps = os.environ.get("LITEJESD_PYDEPS")
    if extra_pydeps:
        sys.path.insert(0, extra_pydeps)
    sys.path.insert(0, str(LITEJESD_SRC))

    try:
        from migen import ClockDomain, ClockSignal, Module, ResetSignal, Signal
        from litex.build.xilinx import XilinxPlatform
        from litex.soc.interconnect import stream
        from litejesd204b.common import (
            JESD204BPhysicalSettings,
            JESD204BSettings,
            JESD204BTransportSettings,
        )
        from litejesd204b.core import LiteJESD204BCoreTX
    except ImportError as exc:
        raise SystemExit(
            "Missing generator dependency. Install Migen/LiteX in the Python "
            "environment used for regeneration, for example:\n"
            "  python -m pip install migen litex\n"
            f"Original import error: {exc}"
        ) from exc

    return {
        "ClockDomain": ClockDomain,
        "ClockSignal": ClockSignal,
        "Module": Module,
        "ResetSignal": ResetSignal,
        "Signal": Signal,
        "XilinxPlatform": XilinxPlatform,
        "stream": stream,
        "JESD204BPhysicalSettings": JESD204BPhysicalSettings,
        "JESD204BSettings": JESD204BSettings,
        "JESD204BTransportSettings": JESD204BTransportSettings,
        "LiteJESD204BCoreTX": LiteJESD204BCoreTX,
    }


deps = import_generator_dependencies()
ClockDomain = deps["ClockDomain"]
ClockSignal = deps["ClockSignal"]
Module = deps["Module"]
ResetSignal = deps["ResetSignal"]
Signal = deps["Signal"]
XilinxPlatform = deps["XilinxPlatform"]
stream = deps["stream"]
JESD204BPhysicalSettings = deps["JESD204BPhysicalSettings"]
JESD204BSettings = deps["JESD204BSettings"]
JESD204BTransportSettings = deps["JESD204BTransportSettings"]
LiteJESD204BCoreTX = deps["LiteJESD204BCoreTX"]


class ExportPHY:
    """Small PHY record matching the LiteJESD204B TX core expectations."""

    def __init__(self, lane):
        self.n = lane
        self.sink = stream.Endpoint([("data", 32), ("ctrl", 4)])
        self.source = stream.Endpoint([("data", 32), ("ctrl", 4)])


class LiteJESDDacTX(Module):
    """TX-only JESD204B block for the DAQ DAC launch path.

    Settings match the intended DAC39J84-style 8-lane, 16-bit, 8B/10B link:

    - L = 8 lanes
    - M = 8 converters
    - N = N' = 16 bits
    - S = 2 samples per converter per frame
    - F = 4 octets per frame per lane
    - K = 32 frames per multiframe
    """

    def __init__(self):
        self.clock_domains.cd_jesd = ClockDomain("jesd")

        self.enable = Signal(name="enable")
        self.sync_n = Signal(name="sync_n")
        self.sysref = Signal(name="sysref")
        self.stpl_enable = Signal(name="stpl_enable")
        self.ready = Signal(name="ready")

        self.converters = [Signal(32, name=f"converter{i}") for i in range(8)]
        self.tx_data = [Signal(32, name=f"tx_data{i}") for i in range(8)]
        self.tx_ctrl = [Signal(4, name=f"tx_ctrl{i}") for i in range(8)]

        self.phy_cds = []
        phys = []
        for lane in range(8):
            cd = ClockDomain(f"jesd_phy{lane}_tx")
            setattr(self.clock_domains, f"cd_jesd_phy{lane}_tx", cd)
            self.phy_cds.append(cd)
            phys.append(ExportPHY(lane))

        physical = JESD204BPhysicalSettings(l=8, m=8, n=16, np=16)
        transport = JESD204BTransportSettings(f=4, s=2, k=32, cs=0)
        settings = JESD204BSettings(
            physical,
            transport,
            did=0x00,
            bid=0x00,
            framing=True,
            scrambling=True,
        )

        core = LiteJESD204BCoreTX(
            phys,
            settings,
            converter_data_width=32,
            scrambling=True,
            stpl_random=False,
        )
        self.submodules.core = core
        core.register_jsync(self.sync_n, polarity=0)
        core.register_jref(self.sysref)

        self.comb += [
            core.enable.eq(self.enable),
            core.stpl_enable.eq(self.stpl_enable),
            self.ready.eq(core.ready),
        ]

        for lane in range(8):
            self.comb += [
                getattr(core.sink, f"converter{lane}").eq(self.converters[lane]),
                phys[lane].sink.ready.eq(1),
                self.tx_data[lane].eq(phys[lane].sink.data),
                self.tx_ctrl[lane].eq(phys[lane].sink.ctrl),
            ]


def generate(output):
    dut = LiteJESDDacTX()
    ios = {
        dut.enable,
        dut.sync_n,
        dut.sysref,
        dut.stpl_enable,
        dut.ready,
        dut.cd_jesd.clk,
        dut.cd_jesd.rst,
    }
    ios |= set(dut.converters + dut.tx_data + dut.tx_ctrl)
    for cd in dut.phy_cds:
        ios |= {cd.clk, cd.rst}

    platform = XilinxPlatform("xczu9eg-ffvb1156-2-e", [])
    verilog = platform.get_verilog(dut, name="litejesd_dac_tx", ios=ios)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(verilog.main_source, encoding="utf-8")
    for filename, contents in sorted(verilog.data_files.items()):
        (output.parent / filename).write_text(contents, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Generated Verilog output path.",
    )
    args = parser.parse_args()
    generate(args.output.resolve())
    print(f"Generated {args.output}")


if __name__ == "__main__":
    main()
