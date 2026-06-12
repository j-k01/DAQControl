#!/usr/bin/env python3
"""Interactive matplotlib viewer for saved ADC capture CSVs.

Auto-detects the three CSV formats this repo writes:
  - trap_dac0_adc_in1_uart.py / sine_dac0_adc_in1_uart.py:
    sample_index,time_ns,in<N>_counts
  - capture_plot_adc_uart.py combined: stream,sample_index,sample_signed
  - capture_plot_adc_uart.py raw sources: source,index,word_hex,lo16_signed,hi16_signed

Opens a zoomable/pannable matplotlib window by default; --save also writes a PNG.

Examples:
  python scripts/view_adc_csv.py captures/trap35ns_in1.csv
  python scripts/view_adc_csv.py captures/trap35ns_in1.csv --fft
  python scripts/view_adc_csv.py captures/adc_capture_sources_0_1_2_3_combined.csv --stream adc_ch0
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

try:
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover
    raise SystemExit("matplotlib is required: python -m pip install matplotlib") from exc

# ADS54J60 full-scale input is 1.9 Vpp differential at the ADC pins
# (same convention as trap_dac0_adc_in1_uart.py).
ADC_FULL_SCALE_VPP = 1.9
ADC_VOLTS_PER_COUNT = ADC_FULL_SCALE_VPP / 65536.0


def load_csv(path: Path) -> dict[str, tuple[list[float] | None, list[int]]]:
    """Return {trace_name: (time_ns or None, samples)}."""
    with path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [row for row in reader if row]

    if (
        len(header) >= 3
        and header[:2] == ["sample_index", "time_ns"]
        and re.fullmatch(r"in\d+_counts", header[2])
    ):
        times = [float(row[1]) for row in rows]
        samples = [int(row[2]) for row in rows]
        return {header[2][: -len("_counts")]: (times, samples)}

    if header[:3] == ["stream", "sample_index", "sample_signed"]:
        traces: dict[str, tuple[list[float] | None, list[int]]] = {}
        for row in rows:
            traces.setdefault(row[0], (None, []))[1].append(int(row[2]))
        return traces

    if header[:5] == ["source", "index", "word_hex", "lo16_signed", "hi16_signed"]:
        traces = {}
        for row in rows:
            traces.setdefault(f"source{row[0]}_lo16", (None, []))[1].append(int(row[3]))
            traces.setdefault(f"source{row[0]}_hi16", (None, []))[1].append(int(row[4]))
        return traces

    raise SystemExit(f"unrecognized CSV header: {header}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--stream", help="Only plot traces whose name contains this text.")
    parser.add_argument(
        "--sample-rate-mhz",
        type=float,
        default=1000.0,
        help="Builds the time axis when the CSV has no time_ns column (ADC inputs are 1 GS/s).",
    )
    parser.add_argument("--fft", action="store_true", help="Add an FFT magnitude panel per trace.")
    parser.add_argument("--start-ns", type=float, default=0.0, help="Trim before this time.")
    parser.add_argument("--span-ns", type=float, help="Only plot this much time after --start-ns.")
    parser.add_argument("--save", type=Path, help="Also write the figure to this PNG path.")
    parser.add_argument("--no-show", action="store_true", help="Skip the interactive window.")
    args = parser.parse_args()

    traces = load_csv(args.csv_path)
    if args.stream:
        traces = {name: data for name, data in traces.items() if args.stream in name}
    if not traces:
        raise SystemExit("no traces matched")

    step_ns = 1.0e3 / args.sample_rate_mhz
    panels = len(traces) * (2 if args.fft else 1)
    fig, axes = plt.subplots(
        panels, 1, figsize=(14, max(3.5, 2.8 * panels)), constrained_layout=True
    )
    if panels == 1:
        axes = [axes]

    ax_index = 0
    for name, (times, samples) in traces.items():
        t = times if times is not None else [i * step_ns for i in range(len(samples))]
        lo = 0
        hi = len(samples)
        if args.start_ns > 0 or args.span_ns is not None:
            end_ns = args.start_ns + args.span_ns if args.span_ns is not None else t[-1]
            lo = next((i for i, v in enumerate(t) if v >= args.start_ns), 0)
            hi = next((i for i, v in enumerate(t) if v > end_ns), len(samples))
        ax = axes[ax_index]
        ax_index += 1
        ax.plot(t[lo:hi], samples[lo:hi], lw=0.8)
        ax.set_title(f"{args.csv_path.name}: {name} ({hi - lo} samples)")
        ax.set_xlabel("time [ns]")
        ax.set_ylabel("signed16 counts")
        ax.grid(True, alpha=0.25)
        ax.secondary_yaxis(
            "right",
            functions=(lambda c: c * ADC_VOLTS_PER_COUNT, lambda v: v / ADC_VOLTS_PER_COUNT),
        ).set_ylabel(f"volts ({ADC_FULL_SCALE_VPP:g} Vpp FS, ADC-pin referred)")

        if args.fft:
            try:
                import numpy as np
            except ImportError as exc:
                raise SystemExit("numpy is required for --fft: python -m pip install numpy") from exc
            data = np.asarray(samples[lo:hi], dtype=np.float64)
            data -= np.mean(data)
            mag = np.abs(np.fft.rfft(data * np.hanning(len(data))))
            if len(mag):
                mag[0] = 0.0
            freqs_mhz = np.fft.rfftfreq(len(data), d=step_ns * 1.0e-3)
            fax = axes[ax_index]
            ax_index += 1
            fax.semilogy(freqs_mhz, mag + 1e-9, lw=0.8)
            fax.set_title(f"{name} FFT (peak {freqs_mhz[int(np.argmax(mag))]:.2f} MHz)")
            fax.set_xlabel("frequency [MHz]")
            fax.set_ylabel("|FFT|")
            fax.grid(True, alpha=0.25)

    if args.save:
        fig.savefig(args.save, dpi=150)
        print(f"Wrote {args.save}")
    if not args.no_show:
        plt.show()
    else:
        plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())
