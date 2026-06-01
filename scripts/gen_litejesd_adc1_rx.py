#!/usr/bin/env python3
"""Generate the LiteJESD204B ADC1 RX Verilog wrapper.

The generated RTL is committed under src/jesd so Vivado does not need Migen or
LiteX during normal builds.
"""

from pathlib import Path
import argparse
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
LITEJESD_SRC = ROOT / "third_party" / "litejesd204b"
DEFAULT_OUTPUT = ROOT / "src" / "jesd" / "litejesd_adc1_rx.v"


def import_generator_dependencies():
    extra_pydeps = os.environ.get("LITEJESD_PYDEPS")
    if extra_pydeps:
        sys.path.insert(0, extra_pydeps)
    sys.path.insert(0, str(LITEJESD_SRC))

    try:
        from migen import ClockDomain, Module, Signal
        from litex.build.xilinx import XilinxPlatform
        from litex.soc.interconnect import stream
        from litejesd204b.common import (
            JESD204BPhysicalSettings,
            JESD204BSettings,
            JESD204BTransportSettings,
        )
        from litejesd204b.core import LiteJESD204BCoreRX
    except ImportError as exc:
        raise SystemExit(
            "Missing generator dependency. Install Migen/LiteX in the Python "
            "environment used for regeneration, for example:\n"
            "  python -m pip install migen litex\n"
            f"Original import error: {exc}"
        ) from exc

    return {
        "ClockDomain": ClockDomain,
        "Module": Module,
        "Signal": Signal,
        "XilinxPlatform": XilinxPlatform,
        "stream": stream,
        "JESD204BPhysicalSettings": JESD204BPhysicalSettings,
        "JESD204BSettings": JESD204BSettings,
        "JESD204BTransportSettings": JESD204BTransportSettings,
        "LiteJESD204BCoreRX": LiteJESD204BCoreRX,
    }


deps = import_generator_dependencies()
ClockDomain = deps["ClockDomain"]
Module = deps["Module"]
Signal = deps["Signal"]
XilinxPlatform = deps["XilinxPlatform"]
stream = deps["stream"]
JESD204BPhysicalSettings = deps["JESD204BPhysicalSettings"]
JESD204BSettings = deps["JESD204BSettings"]
JESD204BTransportSettings = deps["JESD204BTransportSettings"]
LiteJESD204BCoreRX = deps["LiteJESD204BCoreRX"]


class ExportRXPHY:
    """Small PHY record matching the LiteJESD204B RX core expectations."""

    def __init__(self, lane):
        self.n = lane
        self.source = stream.Endpoint([("data", 32), ("ctrl", 4)])
        self.sink = stream.Endpoint([("data", 32), ("ctrl", 4)])
        self.rx_align = Signal(name=f"rx_align{lane}")


class LiteJESDAdc1RX(Module):
    """RX-only JESD204B block for one ADS54J60 in LMFS=4211 mode.

    Settings match one dual-channel ADS54J60 from the Sundance FMC-ADC500-CD:

    - L = 4 lanes
    - M = 2 converters
    - N = N' = 16 bits
    - S = 1 sample per converter per frame
    - F = 1 octet per frame per lane
    - K = 32 frames per multiframe
    """

    def __init__(self):
        self.clock_domains.cd_jesd = ClockDomain("jesd")

        self.enable = Signal(name="enable")
        self.ilas_check_enable = Signal(reset=1, name="ilas_check_enable")
        self.stpl_enable = Signal(name="stpl_enable")
        self.sysref = Signal(name="sysref")
        self.sync_n = Signal(name="sync_n")
        self.ready = Signal(name="ready")
        self.link_ready = Signal(4, name="link_ready")
        self.link_sync = Signal(4, name="link_sync")
        self.rx_align = Signal(4, name="rx_align")

        self.rx_data = [Signal(32, name=f"rx_data{i}") for i in range(4)]
        self.rx_ctrl = [Signal(4, name=f"rx_ctrl{i}") for i in range(4)]
        self.converters = [Signal(64, name=f"converter{i}") for i in range(2)]

        self.phy_cds = []
        phys = []
        for lane in range(4):
            cd = ClockDomain(f"jesd_phy{lane}_rx")
            setattr(self.clock_domains, f"cd_jesd_phy{lane}_rx", cd)
            self.phy_cds.append(cd)
            phys.append(ExportRXPHY(lane))

        physical = JESD204BPhysicalSettings(l=4, m=2, n=16, np=16)
        transport = JESD204BTransportSettings(f=1, s=1, k=32, cs=0, hd=0)
        settings = JESD204BSettings(
            physical,
            transport,
            did=0x00,
            bid=0x00,
            framing=True,
            scrambling=True,
        )

        core = LiteJESD204BCoreRX(
            phys,
            settings,
            converter_data_width=64,
            scrambling=True,
            ilas_check=True,
            stpl_random=False,
        )
        self.submodules.core = core
        core.register_jsync(self.sync_n, polarity=0)
        core.register_jref(self.sysref)

        self.comb += [
            core.enable.eq(self.enable),
            core.ilas_check.eq(self.ilas_check_enable),
            core.stpl_enable.eq(self.stpl_enable),
            self.ready.eq(core.ready),
        ]

        for converter in range(2):
            self.comb += self.converters[converter].eq(
                getattr(core.source, f"converter{converter}")
            )

        for lane in range(4):
            self.comb += [
                phys[lane].source.valid.eq(1),
                phys[lane].source.data.eq(self.rx_data[lane]),
                phys[lane].source.ctrl.eq(self.rx_ctrl[lane]),
                self.link_ready[lane].eq(core.links[lane].ready),
                self.link_sync[lane].eq(core.links[lane].jsync),
                self.rx_align[lane].eq(phys[lane].rx_align),
            ]


def generate(output):
    dut = LiteJESDAdc1RX()
    ios = {
        dut.enable,
        dut.ilas_check_enable,
        dut.stpl_enable,
        dut.sysref,
        dut.sync_n,
        dut.ready,
        dut.link_ready,
        dut.link_sync,
        dut.rx_align,
        dut.cd_jesd.clk,
        dut.cd_jesd.rst,
    }
    ios |= set(dut.rx_data + dut.rx_ctrl + dut.converters)
    for cd in dut.phy_cds:
        ios |= {cd.clk, cd.rst}

    platform = XilinxPlatform("xczu9eg-ffvb1156-2-e", [])
    verilog = platform.get_verilog(dut, name="litejesd_adc1_rx", ios=ios)

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
