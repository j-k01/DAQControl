#!/usr/bin/env python3
"""Verify all four DAC->ADC cabled loopbacks with distinct sine tones.

Cabling under test: OUT1->IN1, OUT2->IN2, OUT3->IN3, OUT4->IN4. Every DAC
channel gets its own pure sine frequency, so each ADC input must show a
clean FFT peak at its own tone; a peak at a different channel's tone means
the cables are crossed.

The DDS source cannot do this (all channels share one RW3 phase-increment
field), so each tone is rendered into that channel's program BRAM instead.
All four programs are 8192 u32 words = 16384 samples at 1 GSPS (one shared
RW3 frame count), and each requested frequency is snapped to an integer
number of cycles per 16384 samples (multiples of ~61.04 kHz) so the BRAM
loop is seamless. The ADC capture is 16384 samples at 1 GS/s (ADS54J60
LMFS=4211 on 10G lanes, 4 samples per 250 MHz JESD beat), and the DAC and
ADC clocks are locked to the same card clock, so every snapped tone lands
exactly on a capture FFT bin (bin index == cycles per program).

Typical run (after program_and_load.tcl):

  python scripts/quad_sine_loopback_check_uart.py --port COM10

Exit status 0 = all four loopbacks PASS, 1 = any FAIL.
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

PROGRAM_WORDS = 8192          # full program BRAM on every channel
PROGRAM_SAMPLES = 2 * PROGRAM_WORDS


def parse_int(text: str) -> int:
    return int(text, 0)


def parse_freqs(text: str) -> list[float]:
    freqs = [float(token) for token in text.split(",") if token.strip()]
    if len(freqs) != 4:
        raise SystemExit("--freqs-mhz needs exactly 4 comma-separated values (DAC0..DAC3)")
    return freqs


def snap_tone(freq_mhz: float, dac_rate_mhz: float, adc_rate_mhz: float) -> tuple[int, float]:
    """Snap to an integer cycle count per program so the BRAM loop is seamless."""
    cycles = int(round(freq_mhz * PROGRAM_SAMPLES / dac_rate_mhz))
    if cycles < 1:
        raise SystemExit(f"{freq_mhz:g} MHz is below the {dac_rate_mhz / PROGRAM_SAMPLES:.6f} MHz tone resolution")
    actual = cycles * dac_rate_mhz / PROGRAM_SAMPLES
    if actual >= adc_rate_mhz / 2:
        raise SystemExit(
            f"{freq_mhz:g} MHz tone is at/above the ADC Nyquist ({adc_rate_mhz / 2:g} MHz) and would alias")
    return cycles, actual


def build_sine_words(cycles: int, amplitude: int, offset: int) -> list[int]:
    phase = 2.0 * math.pi * cycles / PROGRAM_SAMPLES
    samples = [trap.clamp_s16(offset + amplitude * math.sin(phase * i)) for i in range(PROGRAM_SAMPLES)]
    return [trap.pack_pair(samples[2 * i], samples[2 * i + 1]) for i in range(PROGRAM_WORDS)]


def spectrum(samples: list[int]) -> "np.ndarray":
    data = np.asarray(samples, dtype=np.float64)
    centered = data - np.mean(data)
    mag = np.abs(np.fft.rfft(centered * np.hanning(len(centered))))
    if len(mag):
        mag[0] = 0.0
    return mag


def judge_input(adc_input: int, samples: list[int], expected_mhz: float,
                all_expected: dict[int, float], adc_rate_mhz: float,
                tol_mhz: float, min_ptp: int, min_purity: float) -> tuple[bool, list[str]]:
    label = f"IN{adc_input}"
    data = np.asarray(samples, dtype=np.float64)
    ptp = int(np.ptp(data))
    mag = spectrum(samples)
    power = mag * mag
    peak_bin = int(np.argmax(mag))
    peak_mhz = peak_bin * adc_rate_mhz / len(samples)
    total = float(np.sum(power))
    lo = max(0, peak_bin - 3)
    purity = float(np.sum(power[lo:peak_bin + 4]) / total) if total > 0 else 0.0

    lines = [
        f"{label}: ptp={ptp} counts ({combo.counts_to_volts(ptp):.4f} Vpp), "
        f"fft_peak={peak_mhz:.4f} MHz, "
        f"expected={expected_mhz:.4f} MHz (tol +/-{tol_mhz:g}), purity={purity:.3f}"
    ]
    ok = True
    if ptp < min_ptp:
        ok = False
        lines.append(f"{label}: FAIL - looks flat (<{min_ptp} counts p-p); "
                     f"check the OUT{adc_input} -> {label} cable and ADC init")
    elif abs(peak_mhz - expected_mhz) > tol_mhz:
        ok = False
        culprit = next(
            (ch for ch, freq in all_expected.items()
             if ch != adc_input - 1 and abs(peak_mhz - freq) <= tol_mhz),
            None,
        )
        if culprit is not None:
            lines.append(
                f"{label}: FAIL - tone belongs to DAC{culprit} (OUT{culprit + 1}); "
                f"cable is connected to the wrong output")
        else:
            lines.append(f"{label}: FAIL - peak {peak_mhz:.4f} MHz is not the programmed tone")
    elif purity < min_purity:
        ok = False
        lines.append(f"{label}: FAIL - tone is not clean (purity {purity:.3f} < {min_purity:g}); "
                     "check cable seating / interference")
    else:
        lines.append(f"{label}: PASS")
    return ok, lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10", help="PL UART port (CP2108 channel MI_02).")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--freqs-mhz", type=parse_freqs, default=[10.0, 20.0, 30.0, 40.0],
                        help="Four tones for DAC0..DAC3 in MHz, e.g. 10,20,30,40. Each is "
                        "snapped to an integer cycle count per 16384-sample program.")
    parser.add_argument("--amplitude", type=parse_int, default=0x6000)
    parser.add_argument("--offset", type=parse_int, default=0,
                        help="DC offset in DAC counts; sines are bipolar so 0 (mid-scale) is the default.")
    parser.add_argument("--dac-sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--coupling", choices=["ac", "dc"], default="ac")
    parser.add_argument("--frames", type=int, default=4096)
    parser.add_argument("--adc-sample-rate-mhz", type=float, default=1000.0,
                        help="ADS54J60 LMFS=4211 on 10G lanes = 1 GS/s per input.")
    parser.add_argument("--rw2", type=parse_int, default=trap.DAC_NORMAL_RW2)
    parser.add_argument("--expect-build-id", type=parse_int,
                        help="Fail unless selector 3 reports this build ID.")
    parser.add_argument("--tolerance-mhz", type=float, default=0.5)
    parser.add_argument("--min-ptp", type=int, default=100)
    parser.add_argument("--min-purity", type=float, default=0.25,
                        help="Minimum fraction of spectral power within the peak +/-3 bins.")
    parser.add_argument("--verify-upload-words", type=int, default=4)
    parser.add_argument("--reinit-adc", action="store_true")
    parser.add_argument("--no-preflight", action="store_true")
    parser.add_argument("--outdir", default="captures")
    parser.add_argument("--prefix", default="quadsine")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--zoom-periods", type=int, default=10)
    parser.add_argument("--max-points", type=int, default=8000)
    args = parser.parse_args()

    if args.frames <= 0 or args.frames > cap.MAX_CAPTURE_FRAMES:
        raise SystemExit(f"--frames must be 1..{cap.MAX_CAPTURE_FRAMES}")
    if args.amplitude <= 0:
        raise SystemExit("--amplitude must be positive")
    if abs(args.offset) + args.amplitude > 32767:
        raise SystemExit("sine clips signed16: |offset| + amplitude must be <= 32767")

    tones: dict[int, float] = {}
    programs: dict[int, list[int]] = {}
    for channel, freq in enumerate(args.freqs_mhz):
        cycles, actual = snap_tone(freq, args.dac_sample_rate_mhz, args.adc_sample_rate_mhz)
        tones[channel] = actual
        programs[channel] = build_sine_words(cycles, args.amplitude, args.offset)
        print(f"DAC{channel} (OUT{channel + 1}) -> IN{channel + 1}: {actual:.4f} MHz "
              f"({cycles} cycles per program; requested {freq:g})")
    if len({round(f, 6) for f in tones.values()}) != 4:
        raise SystemExit("the four tones must be distinct after snapping; spread --freqs-mhz further apart")

    frame_count = PROGRAM_WORDS // 2
    rw3_run = ((frame_count << 8) & 0xFFFFFF00) | 0x60
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    with serial.Serial(args.port, args.baud, timeout=args.timeout, write_timeout=20.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        if args.expect_build_id is not None:
            cap.check_build_id(port, args.expect_build_id)
        cap.set_rw2(port, args.rw2)
        for adc_input in (1, 2, 3, 4):
            cap.uart_command_ok(port, f"COUP {adc_input} {args.coupling}")
        if not args.no_preflight:
            combo.preflight_jesd(port)
        if args.reinit_adc:
            print("Pulsing ADS54J60 auto-init restart (RW3[2])...")
            cap.uart_command_ok(port, "WRTE 3 0x00000004")
            cap.uart_command_ok(port, "WRTE 3 0x00000000")
            time.sleep(1.0)

        for channel in range(4):
            print(f"Uploading {PROGRAM_WORDS} words to DAC channel {channel}...")
            cap.upload_program(port, programs[channel], channel)
            cap.verify_program_upload(port, programs[channel], channel, args.verify_upload_words)

        port.write(b"NSRC all bram\n")
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

        # Pin the documented run word so all four tones keep free-running.
        cap.uart_command_ok(port, f"WRTE 3 0x{rw3_run:08X}")

    captures = cap.split_frame_captures(frame_words)
    streams = cap.build_converter_streams(captures, [0, 1, 2, 3])

    summary_lines = [
        "tones: " + " ".join(f"DAC{ch}->IN{ch + 1}={freq:.4f}MHz" for ch, freq in tones.items()),
        f"amplitude={args.amplitude} offset={args.offset} coupling={args.coupling}",
        f"program_words={PROGRAM_WORDS} frame_count={frame_count} capture_frames={args.frames}",
    ]
    overall = True
    for adc_input in (1, 2, 3, 4):
        samples = streams[f"adc_ch{adc_input - 1}"]
        ok, lines = judge_input(adc_input, samples, tones[adc_input - 1], tones,
                                args.adc_sample_rate_mhz, args.tolerance_mhz,
                                args.min_ptp, args.min_purity)
        overall = overall and ok
        summary_lines.extend(lines)

        tag = f"in{adc_input}"
        csv_path = outdir / f"{args.prefix}_{tag}.csv"
        combo.write_in1_csv(csv_path, samples, args.adc_sample_rate_mhz, f"{tag}_counts")
        print(f"Wrote {csv_path}")
        if not args.no_plot:
            combo.write_in1_plot(
                outdir / f"{args.prefix}_{tag}.png",
                samples,
                args.adc_sample_rate_mhz,
                1.0e3 / tones[adc_input - 1],
                args.zoom_periods,
                args.max_points,
                args.show,
                f"ADC {tag.upper()} ({tones[adc_input - 1]:.4f} MHz tone)",
            )

    summary_lines.append(f"overall: {'PASS' if overall else 'FAIL'}")
    summary = "\n".join(summary_lines)
    print(summary)
    (outdir / f"{args.prefix}_summary.txt").write_text(summary + "\n")
    print(f"Wrote {outdir / f'{args.prefix}_summary.txt'}")
    print("All four sine tones left running.")
    if not overall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
