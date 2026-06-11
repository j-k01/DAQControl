#!/usr/bin/env python3
"""Put spiking IZH neurons on the DACs and verify spikes arrive at the ADC.

Sequence (after program_and_load.tcl):
  1. NEUR all <profile>   - program every neuron (they power up unprogrammed;
                            the profile also resets v/u and sets dt + iconst)
  2. NEUR all period <N>  - update divider in neuron-clock cycles
  3. NSRC all izh         - route the spike pulse shaper to all four DACs
  4. capture both ADC chips, reconstruct the requested inputs, and count
     spike pulses on each (threshold crossing with a refractory gap)

The capture window is 4096 frames = 32.768 us at 500 MS/s. The neuron bank
runs on its own 50 MHz clock, so the default period of 1 steps the dynamics
every 20 ns; the regular-spiking profile then fires every few tens of us -
at least one spike lands in the window. (With the power-on default period of
256 a step is 5.12 us and the first spike would take milliseconds, far
outside one capture.) Each spike is a 7 ns trapezoid from the pulse shaper,
about 3.5 samples wide at 500 MS/s, riding on the AC-coupled baseline.

Typical run:

  python scripts/neuron_spikes_check_uart.py --port COM10

Exit status 0 = every requested input saw >= --min-spikes pulses, 1 = FAIL.
The neurons are left spiking on all DACs afterwards.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import serial

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import capture_plot_adc_uart as cap  # noqa: E402
import program_dac0_trap_pulse_uart as trap  # noqa: E402
import trap_dac0_adc_in1_uart as combo  # noqa: E402

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise SystemExit("numpy is required: python -m pip install numpy") from exc

PROFILES = ("regular", "rs", "bursting", "ib", "chattering", "ch",
            "fast", "fs", "lts", "tc", "resonator", "rz", "rebound", "rb")


def parse_int(text: str) -> int:
    return int(text, 0)


def parse_inputs(text: str) -> list[int]:
    inputs = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        value = int(token)
        if value < 1 or value > 4:
            raise SystemExit("--inputs entries must be 1..4")
        if value not in inputs:
            inputs.append(value)
    if not inputs:
        raise SystemExit("--inputs must name at least one ADC input")
    return inputs


def detect_spikes(samples: list[int], threshold_frac: float, min_gap_samples: int,
                  min_ptp: int) -> tuple[list[int], float, int]:
    """Return (spike sample indices, threshold counts, p-p counts)."""
    data = np.asarray(samples, dtype=np.float64)
    ptp = int(np.ptp(data))
    baseline = float(np.median(data))
    centered = data - baseline
    peak = float(np.max(centered))
    if ptp < min_ptp or peak <= 0:
        return [], 0.0, ptp
    threshold = threshold_frac * peak
    above = centered > threshold
    rising = np.flatnonzero(above[1:] & ~above[:-1]) + 1
    spikes: list[int] = []
    for index in rising.tolist():
        if not spikes or index - spikes[-1] >= min_gap_samples:
            spikes.append(index)
    return spikes, threshold, ptp


def judge_input(label: str, samples: list[int], adc_rate_mhz: float, args: argparse.Namespace
                ) -> tuple[bool, list[str], list[int]]:
    spikes, threshold, ptp = detect_spikes(
        samples, args.threshold_frac, args.min_gap_samples, args.min_ptp)
    step_us = 1.0 / adc_rate_mhz
    window_us = len(samples) * step_us
    lines = [f"{label}: ptp={ptp} counts, threshold={threshold:.0f}, "
             f"spikes={len(spikes)} in {window_us:.3f} us"]
    if spikes:
        times = [f"{i * step_us:.3f}" for i in spikes[:12]]
        more = "" if len(spikes) <= 12 else f" (+{len(spikes) - 12} more)"
        lines.append(f"{label}: spike times us: {' '.join(times)}{more}")
        if len(spikes) >= 2:
            intervals = np.diff(np.asarray(spikes, dtype=np.float64)) * step_us
            lines.append(
                f"{label}: inter-spike interval mean={float(np.mean(intervals)):.3f} us "
                f"-> rate ~{1.0 / float(np.mean(intervals)):.4f} MHz")
    ok = len(spikes) >= args.min_spikes
    if ptp < args.min_ptp:
        lines.append(f"{label}: FAIL - looks flat (<{args.min_ptp} counts p-p); "
                     "check the DAC->IN cable and ADC init (STAT / ADCS)")
    elif not ok:
        lines.append(f"{label}: FAIL - {len(spikes)} spike(s), need >= {args.min_spikes}; "
                     "try --period 1, a longer window, or --threshold-frac 0.3")
    else:
        lines.append(f"{label}: PASS")
    return ok, lines, spikes


def write_spike_plot(path: Path, samples: list[int], spikes: list[int],
                     adc_rate_mhz: float, max_points: int, show: bool, label: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("matplotlib is required for plotting: python -m pip install matplotlib") from exc

    step_us = 1.0 / adc_rate_mhz
    fig, ax = plt.subplots(figsize=(14, 4), constrained_layout=True)
    x_idx, decimated = cap.decimate(samples, max_points)
    ax.plot([i * step_us for i in x_idx], decimated, lw=0.7)
    for index in spikes:
        ax.axvline(index * step_us, color="r", alpha=0.4, lw=0.8)
    ax.set_title(f"{label}: {len(spikes)} detected spike(s)")
    ax.set_xlabel("time [us]")
    ax.set_ylabel("signed16 counts")
    ax.grid(True, alpha=0.25)
    fig.savefig(path, dpi=150)
    print(f"Wrote {path}")
    if show:
        plt.show()
    else:
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10", help="PL UART port (CP2108 channel MI_02).")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--profile", default="regular", choices=PROFILES,
                        help="Neuron profile programmed on all channels.")
    parser.add_argument("--period", type=int, default=1,
                        help="Update divider in neuron-clock cycles (NEUR all period N). "
                        "1 = step the dynamics every neuron-clock cycle.")
    parser.add_argument("--inputs", type=parse_inputs, default=[1, 2],
                        help="Comma-separated ADC inputs to check, e.g. 1,2 (IN1..IN4).")
    parser.add_argument("--coupling", choices=["ac", "dc"], default="ac")
    parser.add_argument("--frames", type=int, default=4096)
    parser.add_argument("--adc-sample-rate-mhz", type=float, default=500.0)
    parser.add_argument("--command", choices=["CAPT", "PCAP"], default="CAPT",
                        help="Capture command; CAPT leaves the DAC program players alone.")
    parser.add_argument("--rw2", type=parse_int, default=trap.DAC_NORMAL_RW2)
    parser.add_argument("--expect-build-id", type=parse_int,
                        help="Fail unless selector 3 reports this build ID.")
    parser.add_argument("--settle-s", type=float, default=0.5,
                        help="Delay between NSRC all izh and the capture.")
    parser.add_argument("--min-spikes", type=int, default=1)
    parser.add_argument("--min-ptp", type=int, default=100)
    parser.add_argument("--threshold-frac", type=float, default=0.5,
                        help="Spike threshold as a fraction of the peak above the median.")
    parser.add_argument("--min-gap-samples", type=int, default=50,
                        help="Refractory gap between detected spikes (50 = 100 ns at 500 MS/s).")
    parser.add_argument("--reinit-adc", action="store_true")
    parser.add_argument("--no-preflight", action="store_true")
    parser.add_argument("--outdir", default="captures")
    parser.add_argument("--prefix", default="neurspike")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--max-points", type=int, default=8000)
    args = parser.parse_args()

    if args.frames <= 0 or args.frames > cap.MAX_CAPTURE_FRAMES:
        raise SystemExit(f"--frames must be 1..{cap.MAX_CAPTURE_FRAMES}")
    if args.period < 1 or args.period > 0xFFFFFF:
        raise SystemExit("--period must be 1..16777215 (24-bit)")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=20.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        if args.expect_build_id is not None:
            cap.check_build_id(port, args.expect_build_id)
        cap.set_rw2(port, args.rw2)
        for adc_input in args.inputs:
            cap.uart_command_ok(port, f"COUP {adc_input} {args.coupling}")
        if not args.no_preflight:
            combo.preflight_jesd(port)
        if args.reinit_adc:
            print("Pulsing ADS54J60 auto-init restart (RW3[2])...")
            cap.uart_command_ok(port, "WRTE 3 0x00000004")
            cap.uart_command_ok(port, "WRTE 3 0x00000000")
            time.sleep(1.0)

        # Profile first (it also resets v/u and restores the default period),
        # then override the update divider for a visible in-window spike rate.
        print(f"Programming all neurons: profile={args.profile}, period={args.period}")
        cap.uart_command_ok(port, f"NEUR all {args.profile}")
        cap.uart_command_ok(port, f"NEUR all period {args.period}")
        port.write(b"NSRC all izh\n")
        port.flush()
        print(cap.wait_for_line_prefix(port, "DAC source"))
        time.sleep(args.settle_s)

        print(f"Capturing {args.frames} ADC frames with {args.command}...")
        try:
            presync, frame_words = cap.capture_frames(port, args.command, args.frames)
        except (RuntimeError, TimeoutError) as exc:
            raise SystemExit(
                f"capture failed: {exc}\n"
                "Inspect with: python scripts/uart_cmds.py "
                f"--port {args.port} STAT CAPS ADCS, then retry with --reinit-adc."
            ) from exc
        presync_text = presync.decode("ascii", errors="replace").replace("\r", "").strip()
        if presync_text:
            print(f"presync: {presync_text!r}")

    captures = cap.split_frame_captures(frame_words)
    converters = [adc_input - 1 for adc_input in args.inputs]
    streams = cap.build_converter_streams(captures, converters)

    summary_lines = [
        f"profile={args.profile} period={args.period} (neuron-clock cycles)",
        f"inputs={','.join(str(i) for i in args.inputs)} coupling={args.coupling} "
        f"frames={args.frames} command={args.command}",
        f"threshold_frac={args.threshold_frac:g} min_gap_samples={args.min_gap_samples} "
        f"min_spikes={args.min_spikes}",
    ]
    overall = True
    for adc_input in args.inputs:
        samples = streams[f"adc_ch{adc_input - 1}"]
        label = f"IN{adc_input}"
        ok, lines, spikes = judge_input(label, samples, args.adc_sample_rate_mhz, args)
        overall = overall and ok
        summary_lines.extend(lines)

        tag = f"in{adc_input}"
        csv_path = outdir / f"{args.prefix}_{tag}.csv"
        combo.write_in1_csv(csv_path, samples, args.adc_sample_rate_mhz, f"{tag}_counts")
        print(f"Wrote {csv_path}")
        if not args.no_plot:
            write_spike_plot(
                outdir / f"{args.prefix}_{tag}.png",
                samples, spikes, args.adc_sample_rate_mhz,
                args.max_points, args.show, f"ADC {label} ({args.profile}, period {args.period})",
            )

    summary_lines.append(f"overall: {'PASS' if overall else 'FAIL'}")
    summary = "\n".join(summary_lines)
    print(summary)
    (outdir / f"{args.prefix}_summary.txt").write_text(summary + "\n")
    print(f"Wrote {outdir / f'{args.prefix}_summary.txt'}")
    print("Neurons left spiking on all DACs.")
    if not overall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
