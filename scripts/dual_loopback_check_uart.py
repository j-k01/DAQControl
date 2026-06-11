#!/usr/bin/env python3
"""Verify two DAC->ADC cabled loopbacks in one capture, with PASS/FAIL.

Programs two DACs with trapezoid pulse trains at *different* periods, takes a
single PCAP capture, and checks that each ADC input shows a strong FFT peak at
its own DAC's pulse rate. Distinct periods prove the cables are not swapped:
IN-A must see rate A and IN-B must see rate B.

Defaults match the bring-up cabling:
  DAC0 (OUT1) 35 ns period -> IN1 (expects 1000/35 = 28.571 MHz)
  DAC1 (OUT2) 28 ns period -> IN2 (expects 1000/28 = 35.714 MHz)

Both DAC program BRAMs share one frame count (RW3[31:8]), so both programs
are built at the same word count: the largest even u32 count <= 8192 whose
sample count tiles whole periods of BOTH trains (35 & 28 ns: lcm = 140 ns ->
8190 words = 16380 samples = 468 and 585 whole periods).

Typical run (after program_and_load.tcl):

  python scripts/dual_loopback_check_uart.py --port COM10

Exit status 0 = both loopbacks PASS, 1 = any FAIL.
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

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise SystemExit("numpy is required: python -m pip install numpy") from exc


def parse_int(text: str) -> int:
    return int(text, 0)


def shared_word_count(period_samples_a: int, period_samples_b: int, max_words: int) -> int:
    """Largest even u32 word count tiling whole periods of both trains."""
    tile = math.lcm(period_samples_a, period_samples_b)
    if tile % 2:
        tile *= 2  # need an even sample count so the word count is whole
    max_samples = 2 * max_words
    if tile > max_samples:
        raise SystemExit(
            f"periods of {period_samples_a} and {period_samples_b} samples need a "
            f"{tile}-sample tile, which exceeds the {max_samples}-sample program BRAM; "
            "pick periods with a smaller least common multiple"
        )
    return (max_samples // tile) * tile // 2


def build_train(period_ns: float, width_ns: float, amplitude: int, offset: int,
                sample_rate_hz: float, word_count: int) -> tuple[list[int], int]:
    period_samples = trap.ns_to_samples("period", period_ns, sample_rate_hz)
    width_samples = trap.ns_to_samples("pulse width", width_ns, sample_rate_hz)
    if width_samples >= period_samples:
        raise SystemExit(
            f"pulse width ({width_samples} samples) must be shorter than the "
            f"period ({period_samples} samples)"
        )
    period = trap.make_period(period_samples, width_samples, amplitude, offset)
    samples = period * (2 * word_count // period_samples)
    words = [trap.pack_pair(samples[2 * i], samples[2 * i + 1]) for i in range(word_count)]
    return words, period_samples


def fft_peak_mhz(samples: list[int], adc_rate_mhz: float) -> float:
    data = np.asarray(samples, dtype=np.float64)
    centered = data - np.mean(data)
    mag = np.abs(np.fft.rfft(centered * np.hanning(len(centered))))
    if len(mag):
        mag[0] = 0.0
    return int(np.argmax(mag)) * adc_rate_mhz / len(centered)


def judge_input(label: str, samples: list[int], adc_rate_mhz: float, expected_mhz: float,
                other_mhz: float, tol_mhz: float, min_ptp: int) -> tuple[bool, list[str]]:
    data = np.asarray(samples, dtype=np.float64)
    ptp = int(np.ptp(data))
    peak_mhz = fft_peak_mhz(samples, adc_rate_mhz)
    ok = True
    lines = [
        f"{label}: ptp={ptp} counts ({combo.counts_to_volts(ptp):.4f} Vpp), "
        f"fft_peak={peak_mhz:.3f} MHz, "
        f"expected={expected_mhz:.3f} MHz (tol +/-{tol_mhz:g})"
    ]
    if ptp < min_ptp:
        ok = False
        lines.append(f"{label}: FAIL - looks flat (<{min_ptp} counts p-p); check cable/ADC init")
    elif abs(peak_mhz - expected_mhz) > tol_mhz:
        ok = False
        if abs(peak_mhz - other_mhz) <= tol_mhz:
            lines.append(
                f"{label}: FAIL - peak matches the OTHER DAC's rate "
                f"({other_mhz:.3f} MHz); cables are probably swapped"
            )
        else:
            lines.append(f"{label}: FAIL - peak {peak_mhz:.3f} MHz is not the programmed rate")
    else:
        lines.append(f"{label}: PASS")
    return ok, lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10", help="PL UART port (CP2108 channel MI_02).")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--dac-a", type=int, default=0, choices=[0, 1, 2, 3], help="First DAC channel.")
    parser.add_argument("--input-a", type=int, default=1, choices=[1, 2, 3, 4], help="ADC input cabled to DAC A.")
    parser.add_argument("--period-a-ns", type=float, default=35.0)
    parser.add_argument("--dac-b", type=int, default=1, choices=[0, 1, 2, 3], help="Second DAC channel.")
    parser.add_argument("--input-b", type=int, default=2, choices=[1, 2, 3, 4], help="ADC input cabled to DAC B.")
    parser.add_argument("--period-b-ns", type=float, default=28.0)
    parser.add_argument("--pulse-width-ns", type=float, default=7.0)
    parser.add_argument("--dac-sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--amplitude", type=parse_int, default=0x6000)
    parser.add_argument(
        "--offset",
        type=parse_int,
        default=trap.POSITIVE_BASELINE,
        help="Baseline in signed DAC counts. The default keeps the programmed "
        "trapezoid purely positive with headroom; 0 puts the gap at mid-scale.",
    )
    parser.add_argument("--coupling", choices=["ac", "dc"], default="ac")
    parser.add_argument("--frames", type=int, default=4096)
    parser.add_argument("--adc-sample-rate-mhz", type=float, default=1000.0,
                        help="ADS54J60 LMFS=4211 on 10G lanes = 1 GS/s per input.")
    parser.add_argument("--rw2", type=parse_int, default=trap.DAC_NORMAL_RW2)
    parser.add_argument("--expect-build-id", type=parse_int,
                        help="Fail unless selector 3 reports this build ID.")
    parser.add_argument("--tolerance-mhz", type=float, default=0.5)
    parser.add_argument("--min-ptp", type=int, default=100)
    parser.add_argument("--verify-upload-words", type=int, default=4)
    parser.add_argument("--reinit-adc", action="store_true")
    parser.add_argument("--no-preflight", action="store_true")
    parser.add_argument("--outdir", default="captures")
    parser.add_argument("--prefix", default="dualloop")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--zoom-periods", type=int, default=20)
    parser.add_argument("--max-points", type=int, default=8000)
    args = parser.parse_args()

    if args.dac_a == args.dac_b:
        raise SystemExit("--dac-a and --dac-b must differ")
    if args.input_a == args.input_b:
        raise SystemExit("--input-a and --input-b must differ")
    if args.frames <= 0 or args.frames > cap.MAX_CAPTURE_FRAMES:
        raise SystemExit(f"--frames must be 1..{cap.MAX_CAPTURE_FRAMES}")

    trap.check_waveform_range(args.amplitude, args.offset)
    sample_rate_hz = args.dac_sample_rate_mhz * 1.0e6
    pa = trap.ns_to_samples("period A", args.period_a_ns, sample_rate_hz)
    pb = trap.ns_to_samples("period B", args.period_b_ns, sample_rate_hz)
    word_count = shared_word_count(pa, pb, trap.MAX_PROGRAM_WORDS)
    words_a, _ = build_train(args.period_a_ns, args.pulse_width_ns, args.amplitude,
                             args.offset, sample_rate_hz, word_count)
    words_b, _ = build_train(args.period_b_ns, args.pulse_width_ns, args.amplitude,
                             args.offset, sample_rate_hz, word_count)
    frame_count = word_count // 2
    rw3_run = ((frame_count << 8) & 0xFFFFFF00) | 0x60
    expected_a = 1.0e3 / args.period_a_ns
    expected_b = 1.0e3 / args.period_b_ns

    name_a = f"DAC{args.dac_a}->IN{args.input_a}"
    name_b = f"DAC{args.dac_b}->IN{args.input_b}"
    print(f"A: {name_a} {args.period_a_ns:g} ns period ({expected_a:.3f} MHz)")
    print(f"B: {name_b} {args.period_b_ns:g} ns period ({expected_b:.3f} MHz)")
    print(f"shared program length: {word_count} u32 words = {2 * word_count} samples "
          f"({2 * word_count // pa} A periods, {2 * word_count // pb} B periods)")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=20.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        if args.expect_build_id is not None:
            cap.check_build_id(port, args.expect_build_id)
        cap.set_rw2(port, args.rw2)
        cap.uart_command_ok(port, f"COUP {args.input_a} {args.coupling}")
        cap.uart_command_ok(port, f"COUP {args.input_b} {args.coupling}")
        if not args.no_preflight:
            combo.preflight_jesd(port)
        if args.reinit_adc:
            print("Pulsing ADS54J60 auto-init restart (RW3[2])...")
            cap.uart_command_ok(port, "WRTE 3 0x00000004")
            cap.uart_command_ok(port, "WRTE 3 0x00000000")
            time.sleep(1.0)

        for channel, words in ((args.dac_a, words_a), (args.dac_b, words_b)):
            print(f"Uploading {len(words)} words to DAC channel {channel}...")
            cap.upload_program(port, words, channel)
            cap.verify_program_upload(port, words, channel, args.verify_upload_words)

        port.write(b"NSRC all dds\n")
        port.flush()
        print(cap.wait_for_line_prefix(port, "DAC source"))
        for channel in (args.dac_a, args.dac_b):
            port.write(f"NSRC {channel} bram\n".encode("ascii"))
            port.flush()
            print(cap.wait_for_line_prefix(port, "DAC source"))

        print(f"Capturing {args.frames} ADC frames with PCAP...")
        try:
            presync, frame_words = cap.capture_frames(port, "PCAP", args.frames)
        except (RuntimeError, TimeoutError) as exc:
            raise SystemExit(
                f"capture failed: {exc}\n"
                "Inspect with: python scripts/uart_cmds.py "
                f"--port {args.port} STAT CAPS ADCS, then retry with --reinit-adc."
            ) from exc
        presync_text = presync.decode("ascii", errors="replace").replace("\r", "").strip()
        if presync_text:
            print(f"presync: {presync_text!r}")

        # Pin the documented run word so both pulse trains keep free-running.
        cap.uart_command_ok(port, f"WRTE 3 0x{rw3_run:08X}")

    captures = cap.split_frame_captures(frame_words)
    conv_a = args.input_a - 1
    conv_b = args.input_b - 1
    streams = cap.build_converter_streams(captures, [conv_a, conv_b])
    samples_a = streams[f"adc_ch{conv_a}"]
    samples_b = streams[f"adc_ch{conv_b}"]

    ok_a, lines_a = judge_input(f"IN{args.input_a} ({name_a})", samples_a,
                                args.adc_sample_rate_mhz, expected_a, expected_b,
                                args.tolerance_mhz, args.min_ptp)
    ok_b, lines_b = judge_input(f"IN{args.input_b} ({name_b})", samples_b,
                                args.adc_sample_rate_mhz, expected_b, expected_a,
                                args.tolerance_mhz, args.min_ptp)

    summary_lines = [
        f"A: dac={args.dac_a} input=IN{args.input_a} period_ns={args.period_a_ns:g} expected_mhz={expected_a:.3f}",
        f"B: dac={args.dac_b} input=IN{args.input_b} period_ns={args.period_b_ns:g} expected_mhz={expected_b:.3f}",
        f"pulse_width_ns={args.pulse_width_ns:g} amplitude={args.amplitude} offset={args.offset} coupling={args.coupling}",
        f"program_words={word_count} frame_count={frame_count} capture_frames={args.frames}",
    ]
    summary_lines.extend(lines_a)
    summary_lines.extend(lines_b)
    overall = ok_a and ok_b
    summary_lines.append(f"overall: {'PASS' if overall else 'FAIL'}")
    summary = "\n".join(summary_lines)
    print(summary)

    for tag, samples, period_ns in (
        (f"in{args.input_a}", samples_a, args.period_a_ns),
        (f"in{args.input_b}", samples_b, args.period_b_ns),
    ):
        csv_path = outdir / f"{args.prefix}_{tag}.csv"
        combo.write_in1_csv(csv_path, samples, args.adc_sample_rate_mhz, f"{tag}_counts")
        print(f"Wrote {csv_path}")
        if not args.no_plot:
            combo.write_in1_plot(
                outdir / f"{args.prefix}_{tag}.png",
                samples,
                args.adc_sample_rate_mhz,
                period_ns,
                args.zoom_periods,
                args.max_points,
                args.show,
                f"ADC {tag.upper()}",
            )
    (outdir / f"{args.prefix}_summary.txt").write_text(summary + "\n")
    print(f"Wrote {outdir / f'{args.prefix}_summary.txt'}")
    print("Both pulse trains left running.")
    if not overall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
