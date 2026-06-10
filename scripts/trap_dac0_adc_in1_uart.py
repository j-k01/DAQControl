#!/usr/bin/env python3
"""Program a configurable trapezoid pulse train on DAC0, then capture ADC IN1 over UART.

One-shot bring-up for the DAC0 -> ADC IN1 cabled loopback on a freshly loaded
board (run program_and_load.tcl first):

  1. selects ADC IN1 input coupling (COUP 1, AC by default) and optionally
     pulses the ADS54J60 auto-init restart (RW3[2])
  2. builds the seamless-tiling trapezoid pulse program; defaults are the
     35 ns spike train: 7 ns IZH-profile pulse + 28 ns baseline gap at 1 GSPS
  3. uploads it to the DAC0 BRAM and selects the BRAM source on DAC0 only
  4. PCAP capture: restarts the DAC program players, captures both ADC chip
     BRAMs, and streams the frames back over UART
  5. reconstructs the ADC IN1 stream (adc_ch0 = raw sources 0+1), writes
     CSV/PNG/summary, and leaves the DAC0 pulse train free-running

ADC IN1 samples at 500 MS/s, so the 35 ns default period is 17.5 ADC samples
and the expected pulse rate is 1000/35 = 28.571 MHz. The card's DAC output and
ADC input are AC-coupled, so the captured train rides around a zero mean.
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

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise SystemExit("numpy is required: python -m pip install numpy") from exc


def parse_int(text: str) -> int:
    return int(text, 0)


# STAT "gth_gate:" tokens that must all be present before a capture can finish:
# without an active JESD RX link the capture engine never sees ADC beats and
# the firmware reports "ERR capture timeout".
GTH_REQUIRED = ("hmc_done", "qpll_locked", "tx_ready", "rx_ready", "litejesd_active", "litejesd_ready")


def read_gth_gate(port: serial.Serial) -> set[str]:
    port.reset_input_buffer()
    port.write(b"STAT\n")
    port.flush()
    line = cap.wait_for_line_prefix(port, "gth_gate:")
    time.sleep(0.2)
    port.reset_input_buffer()  # drop the rest of the STAT report
    return set(line.split()[1:])


def jesd_missing(tokens: set[str]) -> list[str]:
    return [flag for flag in GTH_REQUIRED if flag not in tokens]


def preflight_jesd(port: serial.Serial) -> None:
    missing = jesd_missing(read_gth_gate(port))
    if not missing:
        print("JESD link OK: " + " ".join(GTH_REQUIRED))
        return

    print(f"JESD link down (missing: {' '.join(missing)}); restarting GTH/LiteJESD (TXRS)...")
    cap.uart_command_ok(port, "TXRS")
    time.sleep(1.0)
    missing = jesd_missing(read_gth_gate(port))

    if missing:
        print(f"Still missing {' '.join(missing)}; pulsing ADS54J60 auto-init restart (RW3[2])...")
        cap.uart_command_ok(port, "WRTE 3 0x00000004")
        cap.uart_command_ok(port, "WRTE 3 0x00000000")
        time.sleep(1.5)
        missing = jesd_missing(read_gth_gate(port))

    if missing:
        raise SystemExit(
            f"JESD link did not come up (missing: {' '.join(missing)}). "
            "Power-cycle the board, rerun program_and_load.tcl, and check the "
            "FMC card seating and SSMC cables. Inspect with: "
            "python scripts/uart_cmds.py --port <port> STAT CAPS ADCS"
        )
    print("JESD link recovered: " + " ".join(GTH_REQUIRED))


def build_words(args: argparse.Namespace) -> tuple[list[int], int, int]:
    sample_rate_hz = args.dac_sample_rate_mhz * 1.0e6
    period_samples = trap.ns_to_samples("period", args.period_ns, sample_rate_hz)
    width_samples = trap.ns_to_samples("pulse width", args.pulse_width_ns, sample_rate_hz)
    if width_samples >= period_samples:
        raise SystemExit(
            f"pulse width ({width_samples} samples) must be shorter than the "
            f"period ({period_samples} samples)"
        )
    word_count = trap.seamless_word_count(period_samples, trap.MAX_PROGRAM_WORDS)
    period = trap.make_period(period_samples, width_samples, args.amplitude, args.offset)
    samples = period * (2 * word_count // period_samples)
    words = [trap.pack_pair(samples[2 * i], samples[2 * i + 1]) for i in range(word_count)]
    return words, period_samples, width_samples


def analyze_in1(samples: list[int], adc_rate_mhz: float, expected_mhz: float) -> list[str]:
    data = np.asarray(samples, dtype=np.float64)
    centered = data - np.mean(data)
    mag = np.abs(np.fft.rfft(centered * np.hanning(len(centered))))
    if len(mag):
        mag[0] = 0.0
    peak_bin = int(np.argmax(mag))
    peak_mhz = peak_bin * adc_rate_mhz / len(centered)
    lines = [
        f"in1_samples={len(samples)}",
        f"in1_min={int(np.min(data))} in1_max={int(np.max(data))} "
        f"in1_ptp={int(np.ptp(data))} in1_mean={float(np.mean(data)):.1f}",
        f"in1_rms_counts={float(np.sqrt(np.mean(centered * centered))):.1f}",
        f"fft_peak_mhz={peak_mhz:.3f} expected_pulse_rate_mhz={expected_mhz:.3f}",
    ]
    if np.ptp(data) < 100:
        lines.append(
            "WARNING: IN1 looks flat (<100 counts p-p). Check the DAC0 -> IN1 "
            "cable and ADC init (STAT / ADCS), or try --reinit-adc."
        )
    return lines


def write_in1_csv(path: Path, samples: list[int], adc_rate_mhz: float) -> None:
    step_ns = 1.0e3 / adc_rate_mhz
    with path.open("w", newline="") as f:
        f.write("sample_index,time_ns,in1_counts\n")
        for index, value in enumerate(samples):
            f.write(f"{index},{index * step_ns:.3f},{value}\n")


def write_in1_plot(
    path: Path,
    samples: list[int],
    adc_rate_mhz: float,
    period_ns: float,
    zoom_periods: int,
    max_points: int,
    show: bool,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("matplotlib is required for plotting: python -m pip install matplotlib") from exc

    step_ns = 1.0e3 / adc_rate_mhz
    zoom_count = min(len(samples), max(8, int(round(zoom_periods * period_ns / step_ns))))

    fig, axes = plt.subplots(2, 1, figsize=(14, 7), constrained_layout=True)
    zoom_t = [i * step_ns for i in range(zoom_count)]
    axes[0].plot(zoom_t, samples[:zoom_count], lw=1.0, marker=".", ms=3)
    axes[0].set_title(f"ADC IN1, first {zoom_periods} pulse periods ({period_ns:g} ns each)")
    axes[0].set_xlabel("time [ns]")
    axes[0].set_ylabel("signed16 counts")
    axes[0].grid(True, alpha=0.25)

    x_idx, full = cap.decimate(samples, max_points)
    axes[1].plot([i * step_ns * 1.0e-3 for i in x_idx], full, lw=0.7)
    axes[1].set_title("ADC IN1, full capture")
    axes[1].set_xlabel("time [us]")
    axes[1].set_ylabel("signed16 counts")
    axes[1].grid(True, alpha=0.25)

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
    parser.add_argument("--period-ns", type=float, default=35.0, help="Pulse repetition period.")
    parser.add_argument("--pulse-width-ns", type=float, default=7.0, help="Trapezoid width.")
    parser.add_argument("--dac-sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--amplitude", type=parse_int, default=0x6000, help="Signed DAC counts.")
    parser.add_argument("--offset", type=parse_int, default=0, help="Baseline in signed DAC counts.")
    parser.add_argument("--coupling", choices=["ac", "dc"], default="ac", help="ADC IN1 input coupling.")
    parser.add_argument(
        "--reinit-adc",
        action="store_true",
        help="Pulse the ADS54J60 auto-init restart (RW3[2]) before uploading.",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip the STAT gth_gate JESD-link check/auto-recovery before capturing.",
    )
    parser.add_argument("--frames", type=int, default=4096, help="ADC capture frames (4 IN1 samples each).")
    parser.add_argument("--adc-sample-rate-mhz", type=float, default=500.0)
    parser.add_argument("--rw2", type=parse_int, default=trap.DAC_NORMAL_RW2)
    parser.add_argument(
        "--expect-build-id",
        type=parse_int,
        help="Fail unless selector 3 reports this build ID (current gateware: 0xDA01003C).",
    )
    parser.add_argument("--verify-upload-words", type=int, default=4)
    parser.add_argument("--outdir", default="captures")
    parser.add_argument("--prefix", default="", help="Output file prefix; default trap<period>ns.")
    parser.add_argument("--zoom-periods", type=int, default=20)
    parser.add_argument("--max-points", type=int, default=8000)
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    if args.frames <= 0 or args.frames > cap.MAX_CAPTURE_FRAMES:
        raise SystemExit(f"--frames must be 1..{cap.MAX_CAPTURE_FRAMES}")

    words, period_samples, width_samples = build_words(args)
    frame_count = len(words) // 2
    rw3_run = ((frame_count << 8) & 0xFFFFFF00) | 0x60
    gap_ns = args.period_ns - args.pulse_width_ns
    expected_mhz = 1.0e3 / args.period_ns
    prefix = args.prefix or f"trap{args.period_ns:g}ns"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(
        f"DAC0 trapezoid pulse train: period={args.period_ns:g} ns "
        f"({period_samples} samples), pulse={args.pulse_width_ns:g} ns "
        f"({width_samples} samples), gap={gap_ns:g} ns, "
        f"amp={args.amplitude} offset={args.offset}"
    )
    print(
        f"program: {len(words)} u32 words = {2 * len(words)} samples = "
        f"{2 * len(words) // period_samples} whole periods (seamless BRAM loop)"
    )

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=20.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        if args.expect_build_id is not None:
            cap.check_build_id(port, args.expect_build_id)
        cap.set_rw2(port, args.rw2)
        cap.uart_command_ok(port, f"COUP 1 {args.coupling}")
        if not args.no_preflight:
            preflight_jesd(port)
        if args.reinit_adc:
            print("Pulsing ADS54J60 auto-init restart (RW3[2])...")
            cap.uart_command_ok(port, "WRTE 3 0x00000004")
            cap.uart_command_ok(port, "WRTE 3 0x00000000")
            time.sleep(1.0)

        print(f"Uploading {len(words)} DAC program words to DAC channel 0...")
        cap.upload_program(port, words, 0)
        cap.verify_program_upload(port, words, 0, args.verify_upload_words)

        # Other DACs stay on DDS; DAC0 plays the BRAM pulse train.
        port.write(b"NSRC all dds\n")
        port.flush()
        print(cap.wait_for_line_prefix(port, "DAC source"))
        port.write(b"NSRC 0 bram\n")
        port.flush()
        print(cap.wait_for_line_prefix(port, "DAC source"))

        print(f"Capturing {args.frames} ADC frames with PCAP...")
        try:
            presync, frame_words = cap.capture_frames(port, "PCAP", args.frames)
        except (RuntimeError, TimeoutError) as exc:
            raise SystemExit(
                f"capture failed: {exc}\n"
                "The capture engine saw no ADC data (JESD RX/ADS54J60 likely "
                "not initialized). Inspect with: python scripts/uart_cmds.py "
                f"--port {args.port} STAT CAPS ADCS, then retry with --reinit-adc."
            ) from exc
        presync_text = presync.decode("ascii", errors="replace").replace("\r", "").strip()
        if presync_text:
            print(f"presync: {presync_text!r}")

        # Pin the documented run word so the pulse train keeps free-running.
        cap.uart_command_ok(port, f"WRTE 3 0x{rw3_run:08X}")

    captures = cap.split_frame_captures(frame_words)
    in1 = cap.build_converter_streams(captures, [0])["adc_ch0"]

    summary_lines = [
        f"period_ns={args.period_ns:g} pulse_width_ns={args.pulse_width_ns:g} gap_ns={gap_ns:g}",
        f"amplitude={args.amplitude} offset={args.offset} coupling={args.coupling}",
        f"program_words={len(words)} frame_count={frame_count} rw2=0x{args.rw2:08X}",
        f"capture_frames={args.frames} adc_sample_rate_mhz={args.adc_sample_rate_mhz:g}",
    ]
    summary_lines.extend(analyze_in1(in1, args.adc_sample_rate_mhz, expected_mhz))
    summary = "\n".join(summary_lines)
    print(summary)

    csv_path = outdir / f"{prefix}_in1.csv"
    write_in1_csv(csv_path, in1, args.adc_sample_rate_mhz)
    print(f"Wrote {csv_path}")
    (outdir / f"{prefix}_in1_summary.txt").write_text(summary + "\n")
    print(f"Wrote {outdir / f'{prefix}_in1_summary.txt'}")
    if not args.no_plot:
        write_in1_plot(
            outdir / f"{prefix}_in1.png",
            in1,
            args.adc_sample_rate_mhz,
            args.period_ns,
            args.zoom_periods,
            args.max_points,
            args.show,
        )
    print("DAC0 pulse train left running.")


if __name__ == "__main__":
    main()
