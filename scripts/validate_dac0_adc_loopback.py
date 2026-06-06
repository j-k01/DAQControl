#!/usr/bin/env python3
"""Validate the physical DAC0 -> ADC IN1 loopback with programmed waveforms.

The script uses only ADC1 converter0, because the current hardware setup has one
ADC input physically cabled and only one ADS54J60 initialized.  It uploads a
known DAC0 BRAM program, captures with PCAP, reconstructs ADC1 converter0, and
fits the captured stream against the expected waveform over a delay sweep.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from pathlib import Path

import numpy as np
import serial

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import capture_plot_adc_uart as cap  # noqa: E402


RW2_DAC0_LOOPBACK_ALL_ACTIVE = 0x010000F8


def unpack_program_samples(words: list[int]) -> list[int]:
    samples: list[int] = []
    for word in words:
        samples.append(cap.signed16(word & 0xFFFF))
        samples.append(cap.signed16((word >> 16) & 0xFFFF))
    return samples


def make_trapulse_program(
    words: int,
    sample_rate_mhz: float,
    frequency_mhz: float,
    width_ns: float,
    amplitude: int,
    offset: int,
    sample_format: str,
) -> list[int]:
    sample_rate_hz = sample_rate_mhz * 1.0e6
    frequency_hz = frequency_mhz * 1.0e6
    period_samples = max(1, int(round(sample_rate_hz / frequency_hz)))
    width_samples = max(1, int(round(width_ns * sample_rate_hz / 1.0e9)))

    def sample(index: int) -> float:
        phase = index % period_samples
        if phase >= width_samples:
            return offset
        if width_samples == 7:
            profile = (0.25, 0.5, 1.0, 1.0, 1.0, 0.5, 0.25)
            return offset + amplitude * profile[phase]
        denom = max(1, width_samples - 1)
        pos = phase / denom
        if pos < 0.25:
            return offset + amplitude * (pos / 0.25)
        if pos < 0.75:
            return offset + amplitude
        return offset + amplitude * ((1.0 - pos) / 0.25)

    return [
        cap.pack_pair(sample(2 * index), sample(2 * index + 1), sample_format)
        for index in range(words)
    ]


def make_test_program(test: str, args: argparse.Namespace) -> tuple[list[int], str]:
    sample_rate_hz = args.sample_rate_mhz * 1.0e6
    if test.startswith("sine"):
        frequency_mhz = float(test[len("sine"):])
        cycles = frequency_mhz * 1.0e6 * (args.program_words * 2) / sample_rate_hz
        return (
            cap.make_sine_program(
                args.program_words,
                cycles,
                args.amplitude,
                args.offset,
                args.sample_format,
            ),
            f"sine {frequency_mhz:g} MHz",
        )
    if test.startswith("triangle"):
        frequency_mhz = float(test[len("triangle"):])
        return (
            cap.make_triangle_program(
                args.program_words,
                args.triangle_step,
                args.offset,
                args.amplitude,
                args.sample_format,
                frequency_mhz * 1.0e6,
                sample_rate_hz,
            ),
            f"triangle {frequency_mhz:g} MHz",
        )
    if test.startswith("trapezoid"):
        frequency_mhz = float(test[len("trapezoid"):])
        return (
            cap.make_trapezoid_program(
                args.program_words,
                frequency_mhz * 1.0e6,
                sample_rate_hz,
                args.amplitude,
                args.offset,
                args.sample_format,
            ),
            f"trapezoid {frequency_mhz:g} MHz",
        )
    if test.startswith("trapulse"):
        frequency_mhz = float(test[len("trapulse"):])
        return (
            make_trapulse_program(
                args.program_words,
                args.sample_rate_mhz,
                frequency_mhz,
                args.pulse_width_ns,
                args.amplitude,
                args.offset,
                args.sample_format,
            ),
            f"trapulse {frequency_mhz:g} MHz width={args.pulse_width_ns:g} ns",
        )
    raise ValueError(
        f"unknown test {test!r}; use names like sine100, triangle50, trapezoid50, trapulse10"
    )


def expected_segment(program_samples: np.ndarray, shift: int, count: int) -> np.ndarray:
    indexes = (np.arange(count) + shift) % len(program_samples)
    return program_samples[indexes].astype(np.float64)


def fit_delay(
    adc_samples: list[int],
    program_samples: list[int],
    max_shift: int,
    skip: int,
) -> dict[str, float | int | bool]:
    y = np.asarray(adc_samples[skip:], dtype=np.float64)
    y = y - np.mean(y)
    n = len(y)
    program = np.asarray(program_samples, dtype=np.float64)
    max_shift = min(max_shift, len(program) - 1)

    best: dict[str, float | int | bool] = {
        "corr": -2.0,
        "shift": 0,
        "inverted": False,
        "scale": 0.0,
        "offset": 0.0,
        "residual_rms": 0.0,
        "signal_rms": 0.0,
        "snr_db": -999.0,
    }

    for shift in range(max_shift + 1):
        x = expected_segment(program, shift + skip, n)
        x = x - np.mean(x)
        x_norm = float(np.dot(x, x))
        y_norm = float(np.dot(y, y))
        if x_norm == 0.0 or y_norm == 0.0:
            continue
        corr = float(np.dot(x, y) / math.sqrt(x_norm * y_norm))
        inverted = False
        if corr < 0:
            corr = -corr
            inverted = True
            x = -x

        if corr > float(best["corr"]):
            # Linear fit on the non-DC-removed expected waveform.
            x_fit = expected_segment(program, shift + skip, n)
            if inverted:
                x_fit = -x_fit
            a = np.vstack([x_fit, np.ones(n)]).T
            scale, offset = np.linalg.lstsq(a, np.asarray(adc_samples[skip:], dtype=np.float64), rcond=None)[0]
            fitted = scale * x_fit + offset
            residual = np.asarray(adc_samples[skip:], dtype=np.float64) - fitted
            residual_rms = float(np.sqrt(np.mean(residual * residual)))
            signal_rms = float(np.sqrt(np.mean((fitted - np.mean(fitted)) ** 2)))
            snr_db = 20.0 * math.log10(signal_rms / residual_rms) if residual_rms > 0 else 999.0
            best = {
                "corr": corr,
                "shift": shift,
                "inverted": inverted,
                "scale": float(scale),
                "offset": float(offset),
                "residual_rms": residual_rms,
                "signal_rms": signal_rms,
                "snr_db": snr_db,
            }
    return best


def write_overlay(
    path: Path,
    name: str,
    adc_samples: list[int],
    program_samples: list[int],
    fit: dict[str, float | int | bool],
    skip: int,
    plot_samples: int,
) -> None:
    import matplotlib.pyplot as plt

    shift = int(fit["shift"])
    inverted = bool(fit["inverted"])
    scale = float(fit["scale"])
    offset = float(fit["offset"])
    count = min(plot_samples, len(adc_samples) - skip)
    x_raw = expected_segment(np.asarray(program_samples), shift + skip, count)
    if inverted:
        x_raw = -x_raw
    expected = scale * x_raw + offset
    observed = np.asarray(adc_samples[skip:skip + count], dtype=np.float64)
    residual = observed - expected

    fig, axes = plt.subplots(2, 1, figsize=(14, 7), constrained_layout=True)
    axes[0].plot(observed, label="ADC converter0", lw=1.1)
    axes[0].plot(expected, label="expected DAC0 program, fitted", lw=1.0, alpha=0.8)
    axes[0].set_title(
        f"{name}: corr={float(fit['corr']):+.3f} shift={shift} "
        f"scale={scale:.4f} snr={float(fit['snr_db']):.1f} dB"
    )
    axes[0].set_ylabel("signed16")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(loc="best")
    axes[1].plot(residual, lw=0.9)
    axes[1].set_title(f"fit residual, RMS={float(fit['residual_rms']):.1f} counts")
    axes[1].set_xlabel("ADC sample index in zoom window")
    axes[1].set_ylabel("counts")
    axes[1].grid(True, alpha=0.25)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def send_source_setup(port: serial.Serial) -> None:
    # Keep non-DAC0 outputs in DDS and make DAC source0 BRAM-driven.  PCAP also
    # sets the program-enable bit, but the explicit NSRC makes the source
    # contract visible in STAT.
    port.write(b"NSRC all dds\n")
    port.flush()
    print(cap.wait_for_line_prefix(port, "DAC source"))
    port.write(b"NSRC 0 bram\n")
    port.flush()
    print(cap.wait_for_line_prefix(port, "DAC source"))


def run_one_test(
    port: serial.Serial,
    test: str,
    args: argparse.Namespace,
    outdir: Path,
) -> dict[str, float | int | str | bool]:
    program, label = make_test_program(test, args)
    print(f"\n=== {label} ===")
    cap.upload_program(port, program, 0)
    cap.verify_program_upload(port, program, 0, args.verify_upload_words)
    send_source_setup(port)
    cap.set_rw2(port, args.rw2)

    presync, frame_words = cap.capture_frames(port, "PCAP", args.frames)
    captures = cap.split_frame_captures(frame_words)
    streams = cap.build_converter_streams(captures)
    adc0 = streams["adc1_converter0"]
    program_samples = unpack_program_samples(program)
    fit = fit_delay(adc0, program_samples, args.max_shift, args.skip)
    adc0_array = np.asarray(adc0, dtype=np.float64)
    adc0_centered = adc0_array - np.mean(adc0_array)
    fft_mag = np.abs(np.fft.rfft(adc0_centered * np.hanning(len(adc0_centered))))
    if len(fft_mag) > 0:
        fft_mag[0] = 0.0
    peak_bin = int(np.argmax(fft_mag))
    fit["fft_peak_bin"] = peak_bin
    fit["fft_peak_mhz"] = peak_bin * args.sample_rate_mhz / len(adc0_centered)
    fit["adc_rms_counts"] = float(np.sqrt(np.mean(adc0_centered * adc0_centered)))
    fit["fft_peak_mag"] = float(fft_mag[peak_bin])
    fit["test"] = test
    fit["label"] = label

    source_tag = "0_1_2_3"
    prefix = f"{args.prefix}_{test}"
    cap.write_csv(outdir / f"{prefix}_sources_{source_tag}.csv", captures)
    cap.write_combined_csv(outdir / f"{prefix}_sources_{source_tag}_combined.csv", streams)
    write_overlay(
        outdir / f"{prefix}_adc0_overlay.png",
        label,
        adc0,
        program_samples,
        fit,
        args.skip,
        args.plot_samples,
    )
    summary = "\n".join([
        f"test={test}",
        f"label={label}",
        f"presync={presync.decode('ascii', errors='replace').strip()!r}",
        f"rw2=0x{args.rw2:08X}",
        f"frames={args.frames}",
        f"adc0_samples={len(adc0)}",
        f"corr={float(fit['corr']):.6f}",
        f"shift_samples={int(fit['shift'])}",
        f"inverted={int(bool(fit['inverted']))}",
        f"scale={float(fit['scale']):.8f}",
        f"offset_counts={float(fit['offset']):.3f}",
        f"signal_rms_counts={float(fit['signal_rms']):.3f}",
        f"residual_rms_counts={float(fit['residual_rms']):.3f}",
        f"snr_db={float(fit['snr_db']):.3f}",
        f"fft_peak_bin={int(fit['fft_peak_bin'])}",
        f"fft_peak_mhz={float(fit['fft_peak_mhz']):.6f}",
        f"adc_rms_counts={float(fit['adc_rms_counts']):.3f}",
    ])
    (outdir / f"{prefix}_summary.txt").write_text(summary + "\n")
    print(summary)
    return fit


def parse_tests(text: str) -> list[str]:
    tests = [item.strip() for item in text.replace(",", " ").split() if item.strip()]
    if not tests:
        raise argparse.ArgumentTypeError("at least one test is required")
    return tests


def parse_int(text: str) -> int:
    return int(text, 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM10")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--expect-build-id", type=parse_int, default=0xDA01002C)
    parser.add_argument("--rw2", type=parse_int, default=RW2_DAC0_LOOPBACK_ALL_ACTIVE)
    parser.add_argument("--tests", type=parse_tests, default=parse_tests("sine100 sine50 triangle50 trapezoid50"))
    parser.add_argument("--frames", type=int, default=2048)
    parser.add_argument("--program-words", type=int, default=8192)
    parser.add_argument("--sample-rate-mhz", type=float, default=1000.0)
    parser.add_argument("--amplitude", type=parse_int, default=0x3000)
    parser.add_argument("--offset", type=parse_int, default=0)
    parser.add_argument("--sample-format", choices=["twos", "offset"], default="twos")
    parser.add_argument("--triangle-step", type=parse_int, default=0x200)
    parser.add_argument("--pulse-width-ns", type=float, default=7.0)
    parser.add_argument("--skip", type=int, default=32)
    parser.add_argument("--max-shift", type=int, default=4096)
    parser.add_argument("--verify-upload-words", type=int, default=4)
    parser.add_argument("--plot-samples", type=int, default=512)
    parser.add_argument("--prefix", default="dac0_adc_validate")
    parser.add_argument("--outdir", default=r"D:\DAVIS\Research\HighSpeedDAQ\daq_captures")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    with serial.Serial(args.port, args.baud, timeout=120.0, write_timeout=20.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()
        if args.expect_build_id is not None:
            cap.check_build_id(port, args.expect_build_id)
        for test in args.tests:
            rows.append(run_one_test(port, test, args, outdir))

    summary_path = outdir / f"{args.prefix}_summary.csv"
    with summary_path.open("w", newline="") as f:
        fieldnames = [
            "test",
            "label",
            "corr",
            "shift",
            "inverted",
            "scale",
            "offset",
            "signal_rms",
            "residual_rms",
            "snr_db",
            "fft_peak_bin",
            "fft_peak_mhz",
            "adc_rms_counts",
            "fft_peak_mag",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})
    print(f"\nWrote {summary_path}")


if __name__ == "__main__":
    main()
