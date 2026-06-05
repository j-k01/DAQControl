#!/usr/bin/env python3
"""Program DAC0 with the 7 ns trapezoidal pulse used for bring-up.

Default behavior:
  - PL UART: COM10
  - DAC source: DAC0 BRAM
  - Pulse width: 7 ns at 1 GSPS
  - Repetition rate: 10 MHz
  - Digital amplitude: 0x7FFF, offset 0

Any option accepted by scripts/program_dac0_waveform_uart.py can be appended
after this wrapper command to override the defaults.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROGRAMMER = ROOT / "scripts" / "program_dac0_waveform_uart.py"

DEFAULT_ARGS = [
    "--shape",
    "trapulse",
    "--port",
    "COM10",
    "--frequency-mhz",
    "10",
    "--sample-rate-mhz",
    "1000",
    "--pulse-width-ns",
    "7",
    "--amplitude",
    "0x7FFF",
    "--offset",
    "0",
    "--words",
    "8192",
]


def main() -> None:
    if not PROGRAMMER.exists():
        raise SystemExit(f"Missing waveform programmer: {PROGRAMMER}")

    # User-supplied duplicates override these defaults because argparse keeps
    # the last value it sees for scalar options.
    sys.argv = [str(PROGRAMMER), *DEFAULT_ARGS, *sys.argv[1:]]
    runpy.run_path(str(PROGRAMMER), run_name="__main__")


if __name__ == "__main__":
    main()
