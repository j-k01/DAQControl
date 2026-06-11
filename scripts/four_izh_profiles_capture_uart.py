#!/usr/bin/env python3
"""Put four different Izhikevich neuron profiles on the four DACs, capture the
loopback on IN1..IN4, and stack the traces in one side-by-side figure.

Mapping (Izhikevich 2003, "Simple Model of Spiking Neurons", Fig. 2):

  DAC0 -> IN1  regular spiking      (RS)  a=0.02 b=0.2  c=-65 d=8
  DAC1 -> IN2  intrinsically burst  (IB)  a=0.02 b=0.2  c=-55 d=4
  DAC2 -> IN3  chattering           (CH)  a=0.02 b=0.2  c=-50 d=2
  DAC3 -> IN4  fast spiking         (FS)  a=0.10 b=0.2  c=-65 d=2

What separates the profiles is the firing *pattern*, which plays out over tens
to hundreds of model-ms - far longer than the 16.384 us (1 GS/s) capture window
holds at the profiles' default timestep. So after programming the profiles this
raises the integration timestep (NEUR all dt, default 0x8000 = 0.5 model-ms per
step, the step Izhikevich integrates with in the paper) and drops the update
divider to its 20 ns floor (NEUR all period 1). That packs ~410 model-ms into
the window, enough to see RS space out, IB burst-then-spike, CH chatter, and FS
fire fast.

Requires the quad loopback cable (OUT1->IN1 ... OUT4->IN4).

  python scripts/four_izh_profiles_capture_uart.py --port COM10

The eight firmware profiles don't all fit on four DACs at once, so --profiles
picks which four to load (DAC0..DAC3). The other four:

  python scripts/four_izh_profiles_capture_uart.py --port COM10 \
      --profiles lts,tc,resonator,rebound --prefix four_izh_set2

Writes captures/<prefix>_in{1..4}.csv, a stacked <prefix>.png, and a summary.
The neurons are left spiking on all four DACs.
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
import neuron_spikes_check_uart as nspk  # noqa: E402

# Canonical firmware name -> (short tag, human label) for every IZH profile.
PROFILE_LABELS = {
    "regular":    ("RS",  "Regular spiking (RS)"),
    "bursting":   ("IB",  "Intrinsically bursting (IB)"),
    "chattering": ("CH",  "Chattering (CH)"),
    "fast":       ("FS",  "Fast spiking (FS)"),
    "lts":        ("LTS", "Low-threshold spiking (LTS)"),
    "tc":         ("TC",  "Thalamo-cortical (TC)"),
    "resonator":  ("RZ",  "Resonator (RZ)"),
    "rebound":    ("RB",  "Rebound burst (RB)"),
}
# Firmware aliases accepted on the command line.
PROFILE_ALIASES = {
    "rs": "regular", "ib": "bursting", "ch": "chattering", "fs": "fast",
    "rz": "resonator", "rb": "rebound",
}
DEFAULT_PROFILES = "regular,bursting,chattering,fast"

NEURON_CLK_MHZ = 50.0


def parse_int(text: str) -> int:
    return int(text, 0)


def to_q16_16(value: float) -> int:
    """Model units -> signed Q16.16 word (e.g. 10.0 -> 0x000A0000)."""
    return round(value * 65536.0) & 0xFFFFFFFF


def parse_profiles(text: str) -> list[tuple[int, str, str, str]]:
    """Parse --profiles into a [(ch, token, tag, label), ...] plan (DAC0..)."""
    plan = []
    tokens = [t.strip() for t in text.split(",") if t.strip()]
    for ch, raw in enumerate(tokens):
        token = PROFILE_ALIASES.get(raw.lower(), raw.lower())
        if token not in PROFILE_LABELS:
            raise SystemExit(
                f"--profiles: unknown profile {raw!r}; choose from "
                + ", ".join(PROFILE_LABELS) + " (aliases rs/ib/ch/fs/rz/rb)")
        tag, label = PROFILE_LABELS[token]
        plan.append((ch, token, tag, label))
    if not 1 <= len(plan) <= 4:
        raise SystemExit("--profiles must name 1..4 profiles (one per DAC)")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="COM10", help="PL UART port (CP2108 channel MI_02).")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--profiles", default=DEFAULT_PROFILES,
                        help="Comma-separated profiles for DAC0..DAC3 (default "
                        + DEFAULT_PROFILES + "). Names: " + ", ".join(PROFILE_LABELS)
                        + "; aliases rs/ib/ch/fs/rz/rb.")
    parser.add_argument("--dt", type=parse_int, default=0x8000,
                        help="Integration timestep (NEUR all dt N, Q16.16). Default 0x8000 = "
                        "0.5 model-ms/step (the paper's step). Raise for more model time per "
                        "window, but >~1 model-ms/step risks forward-Euler blow-up.")
    parser.add_argument("--period", type=int, default=1,
                        help="Update divider in neuron-clock cycles (NEUR all period N). "
                        "1 = the 20 ns floor on the 50 MHz neuron clock.")
    parser.add_argument("--iconst", type=float, default=None,
                        help="Override the constant drive current iconst (NEUR all iconst, "
                        "model units -> Q16.16; profiles default to 10.0). Lowering it parks "
                        "IB/CH nearer their bursting threshold so the bursts stand out.")
    parser.add_argument("--i-input", type=float, default=None, dest="i_input",
                        help="Override the instantaneous I input (NEUR all i, model units -> "
                        "Q16.16; profiles default to 0).")
    parser.add_argument("--coupling", choices=["ac", "dc"], default="ac")
    parser.add_argument("--frames", type=int, default=4096)
    parser.add_argument("--adc-sample-rate-mhz", type=float, default=1000.0,
                        help="ADS54J60 LMFS=4211 on 10G lanes = 1 GS/s per input.")
    parser.add_argument("--command", choices=["CAPT", "PCAP"], default="CAPT")
    parser.add_argument("--rw2", type=parse_int, default=trap.DAC_NORMAL_RW2)
    parser.add_argument("--expect-build-id", type=parse_int)
    parser.add_argument("--settle-s", type=float, default=0.5)
    parser.add_argument("--threshold-frac", type=float, default=0.5)
    parser.add_argument("--min-gap-samples", type=int, default=50)
    parser.add_argument("--min-ptp", type=int, default=100)
    parser.add_argument("--reinit-adc", action="store_true")
    parser.add_argument("--no-preflight", action="store_true")
    parser.add_argument("--outdir", default="captures")
    parser.add_argument("--prefix", default="four_izh")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--max-points", type=int, default=8000)
    args = parser.parse_args()
    plan = parse_profiles(args.profiles)

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
        for ch, _, _, _ in plan:
            cap.uart_command_ok(port, f"COUP {ch + 1} {args.coupling}")
        if not args.no_preflight:
            combo.preflight_jesd(port)
        if args.reinit_adc:
            print("Pulsing ADS54J60 auto-init restart (RW3[2])...")
            cap.uart_command_ok(port, "WRTE 3 0x00000004")
            cap.uart_command_ok(port, "WRTE 3 0x00000000")
            time.sleep(1.0)

        # Program each profile (this also resets v/u and restores the default
        # period 256 / dt 0x1000), then override period + dt on all four so the
        # patterns fit the capture window.
        for ch, token, tag, _ in plan:
            cap.uart_command_ok(port, f"NEUR {ch} {token}")
            print(f"  DAC{ch} <- {token} ({tag})")
        cap.uart_command_ok(port, f"NEUR all period {args.period}")
        cap.uart_command_ok(port, f"NEUR all dt 0x{args.dt:X}")
        if args.iconst is not None:
            cap.uart_command_ok(port, f"NEUR all iconst 0x{to_q16_16(args.iconst):X}")
        if args.i_input is not None:
            cap.uart_command_ok(port, f"NEUR all i 0x{to_q16_16(args.i_input):X}")
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
    streams = cap.build_converter_streams(captures, [0, 1, 2, 3])

    step_us = 1.0 / args.adc_sample_rate_mhz
    model_ms_per_us = (args.dt / 65536.0) * NEURON_CLK_MHZ / args.period
    window_us = args.frames * 4 * step_us

    summary_lines = [
        f"dt=0x{args.dt:X} ({args.dt / 65536.0:g} model-ms/step) period={args.period} "
        f"(one step per {args.period * 1000.0 / NEURON_CLK_MHZ:g} ns at {NEURON_CLK_MHZ:g} MHz)",
        f"sim timebase: {model_ms_per_us:g} model-ms per wall-us "
        f"-> {window_us * model_ms_per_us:.1f} model-ms in the {window_us:.3f} us window",
        f"coupling={args.coupling} frames={args.frames} command={args.command}",
    ]
    drive = []
    if args.iconst is not None:
        drive.append(f"iconst={args.iconst:g} (0x{to_q16_16(args.iconst):X})")
    if args.i_input is not None:
        drive.append(f"i={args.i_input:g} (0x{to_q16_16(args.i_input):X})")
    if drive:
        summary_lines.append("drive override: " + ", ".join(drive))

    per_input = []
    for ch, token, tag, label in plan:
        samples = streams[f"adc_ch{ch}"]
        spikes, threshold, ptp = nspk.detect_spikes(
            samples, args.threshold_frac, args.min_gap_samples, args.min_ptp)
        per_input.append((ch, token, tag, label, samples, spikes))
        rate = ""
        if len(spikes) >= 2:
            import numpy as np
            intervals = np.diff(np.asarray(spikes, dtype=np.float64)) * step_us
            mean_us = float(np.mean(intervals))
            rate = (f", mean interval={mean_us:.3f} us (~{1.0 / mean_us:.4f} MHz wall, "
                    f"~{mean_us * model_ms_per_us:.1f} model-ms)")
        summary_lines.append(
            f"IN{ch + 1} DAC{ch} {label}: ptp={ptp} counts "
            f"({combo.counts_to_volts(ptp):.4f} Vpp), spikes={len(spikes)}{rate}")

        csv_path = outdir / f"{args.prefix}_in{ch + 1}.csv"
        combo.write_in1_csv(csv_path, samples, args.adc_sample_rate_mhz, f"in{ch + 1}_counts")
        print(f"Wrote {csv_path}")

    if not args.no_plot:
        write_stacked_plot(outdir / f"{args.prefix}.png", per_input, step_us,
                           model_ms_per_us, args.max_points, args.show)

    summary = "\n".join(summary_lines)
    print(summary)
    (outdir / f"{args.prefix}_summary.txt").write_text(summary + "\n")
    print(f"Wrote {outdir / f'{args.prefix}_summary.txt'}")
    print("Neurons left spiking on all four DACs.")


def write_stacked_plot(path, per_input, step_us, model_ms_per_us, max_points, show):
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("matplotlib is required for plotting: python -m pip install matplotlib") from exc

    fig, axes = plt.subplots(len(per_input), 1,
                             figsize=(14, max(3.0, 2.75 * len(per_input))),
                             sharex=True, constrained_layout=True)
    if len(per_input) == 1:
        axes = [axes]
    for ax, (ch, token, tag, label, samples, spikes) in zip(axes, per_input):
        x_idx, decimated = cap.decimate(samples, max_points)
        model_ms = [i * step_us * model_ms_per_us for i in x_idx]
        volts = [combo.counts_to_volts(c) for c in decimated]
        ax.plot(model_ms, volts, lw=0.7)
        for index in spikes:
            ax.axvline(index * step_us * model_ms_per_us, color="r", alpha=0.3, lw=0.6)
        ax.set_title(label, loc="left", fontsize=11)
        ax.set_ylabel("volts")
        ax.grid(True, alpha=0.25)
        ax.secondary_yaxis(
            "right", functions=(combo.volts_to_counts, combo.counts_to_volts)
        ).set_ylabel("signed16 counts")
    axes[-1].set_xlabel(f"simulation time [model ms] ({model_ms_per_us:g} ms/us)")
    axes[0].secondary_xaxis(
        "top", functions=(lambda m: m / model_ms_per_us, lambda t: t * model_ms_per_us),
    ).set_xlabel("hardware time [us]")
    fig.suptitle("Izhikevich profiles captured on ADC (in loopback)", fontsize=13)
    fig.savefig(path, dpi=150)
    print(f"Wrote {path}")
    if show:
        plt.show()
    else:
        plt.close(fig)


if __name__ == "__main__":
    main()
