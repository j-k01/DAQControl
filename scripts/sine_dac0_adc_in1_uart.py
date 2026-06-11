#!/usr/bin/env python3
"""Program a pure sine wave on DAC0, then capture ADC IN1 over UART.

Sine companion to trap_dac0_adc_in1_uart.py:

  1. selects ADC IN1 input coupling (COUP 1, AC by default) and preflights
     the JESD link (STAT gth_gate, with TXRS/ADS54J60 auto-recovery)
  2. quantizes the requested frequency to the nearest seamless BRAM loop
     (a whole number of sine periods in the program), so the tone stays
     phase-continuous when the program wraps -- a truly pure tone
  3. uploads it to the DAC0 BRAM and selects the BRAM source on DAC0 only
  4. PCAP-captures both ADC chip BRAMs and streams the frames back
  5. reconstructs IN1, checks the FFT peak against the programmed tone
     (folding through the 1 GS/s Nyquist if needed), writes
     CSV/PNG/summary, and leaves the sine free-running

With the full 16384-sample program at 1 GSPS the frequency grid is
~61 kHz, and any frequency of the form k/N GHz (N <= 16384 samples) is
exact: 10 MHz, 12.5 MHz, 100 MHz, ... The actual programmed frequency is
always printed.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import serial

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import capture_plot_adc_uart as cap  # noqa: E402
import program_dac0_trap_pulse_uart as trap  # noqa: E402
import trap_dac0_adc_in1_uart as combo  # noqa: E402


def best_sine_loop(freq_mhz: float, sample_rate_mhz: float, max_words: int) -> tuple[int, int, float]:
    """Pick (n_samples, periods) so the BRAM loop holds whole sine periods.

    Minimizes |f_actual - f_requested| over even sample counts up to the
    program size; on ties prefers the longest program. f_actual = k/N * fs.
    """
    best: tuple[float, int, int] | None = None
    for n in range(2, 2 * max_words + 1, 2):
        k = round(freq_mhz * n / sample_rate_mhz)
        if k < 1 or 2 * k >= n:  # at least one period, below Nyquist
            continue
        err = abs(k * sample_rate_mhz / n - freq_mhz)
        if best is None or err < best[0] - 1e-9 or (err < best[0] + 1e-9 and n > best[2]):
            best = (err, k, n)
    if best is None:
        raise SystemExit(
            f"cannot fit a sine at {freq_mhz:g} MHz below Nyquist "
            f"({sample_rate_mhz / 2:g} MHz) in {max_words} program words"
        )
    _, k, n = best
    return n, k, k * sample_rate_mhz / n


def build_sine_words(n_samples: int, periods: int, amplitude: int, offset: int) -> list[int]:
    samples = [
        trap.clamp_s16(offset + amplitude * math.sin(2.0 * math.pi * periods * i / n_samples))
        for i in range(n_samples)
    ]
    return [trap.pack_pair(samples[2 * i], samples[2 * i + 1]) for i in range(n_samples // 2)]


def folded_mhz(freq_mhz: float, adc_rate_mhz: float) -> float:
    """Where the tone lands in the ADC's first Nyquist zone."""
    f = freq_mhz % adc_rate_mhz
    return adc_rate_mhz - f if f > adc_rate_mhz / 2 else f


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10", help="PL UART port (CP2108 channel MI_02).")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--frequency-mhz", type=float, default=10.0, help="Requested sine frequency.")
    parser.add_argument("--dac-sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--amplitude", type=combo.parse_int, default=0x6000, help="Signed DAC counts.")
    parser.add_argument("--offset", type=combo.parse_int, default=0, help="Baseline in signed DAC counts.")
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
    parser.add_argument("--adc-sample-rate-mhz", type=float, default=1000.0,
                        help="ADS54J60 LMFS=4211 on 10G lanes = 1 GS/s per input.")
    parser.add_argument("--rw2", type=combo.parse_int, default=trap.DAC_NORMAL_RW2)
    parser.add_argument(
        "--expect-build-id",
        type=combo.parse_int,
        help="Fail unless selector 3 reports this build ID (current gateware: 0xDA01003C).",
    )
    parser.add_argument("--verify-upload-words", type=int, default=4)
    parser.add_argument("--outdir", default="captures")
    parser.add_argument("--prefix", default="", help="Output file prefix; default sine<freq>mhz.")
    parser.add_argument("--zoom-periods", type=int, default=20)
    parser.add_argument("--max-points", type=int, default=8000)
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    if args.frames <= 0 or args.frames > cap.MAX_CAPTURE_FRAMES:
        raise SystemExit(f"--frames must be 1..{cap.MAX_CAPTURE_FRAMES}")
    if not 0 < args.frequency_mhz < args.dac_sample_rate_mhz / 2:
        raise SystemExit(
            f"--frequency-mhz must be 0..{args.dac_sample_rate_mhz / 2:g} (DAC Nyquist)"
        )

    n_samples, periods, actual_mhz = best_sine_loop(
        args.frequency_mhz, args.dac_sample_rate_mhz, trap.MAX_PROGRAM_WORDS
    )
    words = build_sine_words(n_samples, periods, args.amplitude, args.offset)
    frame_count = len(words) // 2
    rw3_run = ((frame_count << 8) & 0xFFFFFF00) | 0x60
    period_ns = 1.0e3 / actual_mhz
    expected_mhz = folded_mhz(actual_mhz, args.adc_sample_rate_mhz)
    prefix = args.prefix or f"sine{args.frequency_mhz:g}mhz"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(
        f"DAC0 sine: requested {args.frequency_mhz:g} MHz -> programmed "
        f"{actual_mhz:.6f} MHz ({periods} periods in {n_samples} samples, "
        f"seamless BRAM loop), amp={args.amplitude} offset={args.offset}"
    )
    if abs(expected_mhz - actual_mhz) > 1e-9:
        print(
            f"note: above the {args.adc_sample_rate_mhz / 2:g} MHz ADC Nyquist; "
            f"IN1 will show the alias at {expected_mhz:.6f} MHz"
        )

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=20.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        if args.expect_build_id is not None:
            cap.check_build_id(port, args.expect_build_id)
        cap.set_rw2(port, args.rw2)
        cap.uart_command_ok(port, f"COUP 1 {args.coupling}")
        if not args.no_preflight:
            combo.preflight_jesd(port)
        if args.reinit_adc:
            print("Pulsing ADS54J60 auto-init restart (RW3[2])...")
            cap.uart_command_ok(port, "WRTE 3 0x00000004")
            cap.uart_command_ok(port, "WRTE 3 0x00000000")
            time.sleep(1.0)

        print(f"Uploading {len(words)} DAC program words to DAC channel 0...")
        cap.upload_program(port, words, 0)
        cap.verify_program_upload(port, words, 0, args.verify_upload_words)

        # Other DACs stay on DDS; DAC0 plays the BRAM sine.
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

        # Pin the documented run word so the sine keeps free-running.
        cap.uart_command_ok(port, f"WRTE 3 0x{rw3_run:08X}")

    captures = cap.split_frame_captures(frame_words)
    in1 = cap.build_converter_streams(captures, [0])["adc_ch0"]

    summary_lines = [
        f"requested_mhz={args.frequency_mhz:g} programmed_mhz={actual_mhz:.6f} "
        f"periods={periods} program_samples={n_samples}",
        f"amplitude={args.amplitude} offset={args.offset} coupling={args.coupling}",
        f"program_words={len(words)} frame_count={frame_count} rw2=0x{args.rw2:08X}",
        f"capture_frames={args.frames} adc_sample_rate_mhz={args.adc_sample_rate_mhz:g}",
    ]
    summary_lines.extend(combo.analyze_in1(in1, args.adc_sample_rate_mhz, expected_mhz))
    summary = "\n".join(summary_lines)
    print(summary)

    csv_path = outdir / f"{prefix}_in1.csv"
    combo.write_in1_csv(csv_path, in1, args.adc_sample_rate_mhz)
    print(f"Wrote {csv_path}")
    (outdir / f"{prefix}_in1_summary.txt").write_text(summary + "\n")
    print(f"Wrote {outdir / f'{prefix}_in1_summary.txt'}")
    if not args.no_plot:
        combo.write_in1_plot(
            outdir / f"{prefix}_in1.png",
            in1,
            args.adc_sample_rate_mhz,
            period_ns,
            args.zoom_periods,
            args.max_points,
            args.show,
        )
    print("DAC0 sine left running.")


if __name__ == "__main__":
    main()
