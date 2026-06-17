#!/usr/bin/env python3
"""Verify the 16:4 DAC source crossbar + per-neuron current monitors over the
physical DAC0 -> ADC0 loopback.

For each crossbar source we route it to DAC0 (NSRC), capture ADC0 via PCAP, and
check a source-appropriate metric:
  - dds   : a dominant FFT tone (the broadcast NCO sine)
  - bram0 : high correlation to a known uploaded sine program
  - tag   : dominant tone at fs/4 (the period-4 tag word 1111/2222/3333/4444)
  - mon0  : DC level tracks neuron 0's programmed input current (the NEW feature;
            needs ADC0 DC-coupled since the monitor is a held DC value)
  - spike0: periodic pulses when neuron 0 is driven to spike fast (best-effort)

The DAC0 loopback is read from one ADC capture channel, selected with --adc-ch
(default adc_ch0).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import serial

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import capture_plot_adc_uart as cap  # noqa: E402

FS_MHZ = 1000.0


def send(port: serial.Serial, command: str, settle: float = 0.3) -> str:
    """Write a command, return whatever the firmware echoes back within `settle`."""
    port.reset_input_buffer()
    port.write((command + "\n").encode("ascii"))
    port.flush()
    time.sleep(settle)
    return port.read_all().decode("ascii", errors="replace").strip()


def capture_adc0(port: serial.Serial, frames: int, adc_ch: str) -> np.ndarray:
    presync, frame_words = cap.capture_frames(port, "PCAP", frames)
    captures = cap.split_frame_captures(frame_words)
    streams = cap.build_converter_streams(captures)
    return np.asarray(streams[adc_ch], dtype=np.float64)


def fft_peak(sig: np.ndarray) -> tuple[float, float, float]:
    """Return (peak_freq_mhz, peak_over_median_ratio, peak_mag) on a windowed FFT."""
    x = sig - np.mean(sig)
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    if len(mag) > 1:
        mag[0] = 0.0
    peak_bin = int(np.argmax(mag))
    freqs = np.fft.rfftfreq(len(x), d=1.0 / FS_MHZ)  # in MHz (d in us)
    med = float(np.median(mag[1:])) or 1e-9
    return float(freqs[peak_bin]), float(mag[peak_bin] / med), float(mag[peak_bin])


def fit_corr(adc: np.ndarray, program_samples: list[int], skip: int = 32,
             max_shift: int = 4096) -> float:
    y = adc[skip:] - np.mean(adc[skip:])
    prog = np.asarray(program_samples, dtype=np.float64)
    n = len(y)
    best = 0.0
    for shift in range(min(max_shift, len(prog) - 1) + 1):
        idx = (np.arange(n) + shift + skip) % len(prog)
        x = prog[idx] - np.mean(prog[idx])
        xn, yn = float(np.dot(x, x)), float(np.dot(y, y))
        if xn == 0 or yn == 0:
            continue
        c = abs(float(np.dot(x, y)) / np.sqrt(xn * yn))
        if c > best:
            best = c
    return best


def plot(outdir: Path, name: str, adc: np.ndarray, nshow: int = 400) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        return
    fig, ax = plt.subplots(2, 1, figsize=(12, 6), constrained_layout=True)
    ax[0].plot(adc[:nshow], lw=0.9)
    ax[0].set_title(f"{name}: ADC0 time (first {nshow} samples)  mean={adc.mean():.1f} rms={adc.std():.1f}")
    ax[0].grid(True, alpha=0.3)
    x = adc - np.mean(adc)
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), d=1.0 / FS_MHZ)
    ax[1].semilogy(freqs, mag + 1e-6, lw=0.8)
    ax[1].set_xlabel("MHz"); ax[1].set_title("ADC0 spectrum"); ax[1].grid(True, alpha=0.3)
    fig.savefig(outdir / f"xbar_{name}.png", dpi=140)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--frames", type=int, default=2048)
    ap.add_argument("--adc-ch", default="adc_ch0", help="capture stream key for the DAC0 loopback")
    ap.add_argument("--outdir", default=r"D:\DAVIS\Research\HighSpeedDAQ\daq_captures")
    ap.add_argument("--only", default="", help="comma list to run a subset (dds,bram,tag,mon,spike)")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    run = lambda name: (not only) or (name in only)

    results: list[tuple[str, bool, str]] = []
    with serial.Serial(args.port, args.baud, timeout=120.0, write_timeout=20.0) as port:
        time.sleep(0.2)
        port.reset_input_buffer()

        # --- DDS: broadcast NCO sine -> dominant tone ---
        if run("dds"):
            print(send(port, "NSRC 0 dds"))
            adc = capture_adc0(port, args.frames, args.adc_ch)
            fpk, ratio, _ = fft_peak(adc)
            plot(outdir, "dds", adc)
            ok = ratio > 20.0 and adc.std() > 20.0
            results.append(("dds", ok, f"tone={fpk:.1f} MHz peak/med={ratio:.0f} rms={adc.std():.0f}"))

        # --- BRAM0: known sine, correlate ---
        if run("bram"):
            words = 4096
            cycles = 40
            program = cap.make_sine_program(words, cycles, 0x3000, 0, "twos")
            cap.upload_program(port, program, 0)
            print(send(port, "NSRC 0 bram0"))
            adc = capture_adc0(port, args.frames, args.adc_ch)
            prog_samples = []
            for w in program:
                prog_samples.append(cap.signed16(w & 0xFFFF))
                prog_samples.append(cap.signed16((w >> 16) & 0xFFFF))
            corr = fit_corr(adc, prog_samples)
            plot(outdir, "bram0_sine", adc)
            results.append(("bram0", corr > 0.85, f"corr={corr:.3f} rms={adc.std():.0f}"))

        # --- tag: period-4 word -> tone at fs/4 = 250 MHz ---
        if run("tag"):
            print(send(port, "NSRC 0 tag"))
            adc = capture_adc0(port, args.frames, args.adc_ch)
            fpk, ratio, _ = fft_peak(adc)
            plot(outdir, "tag", adc)
            ok = abs(fpk - 250.0) < 5.0 and ratio > 20.0
            results.append(("tag", ok, f"tone={fpk:.1f} MHz (expect 250) peak/med={ratio:.0f}"))

        # --- monitor 0: DC level must track neuron 0 input current (NEW) ---
        if run("mon"):
            print(send(port, "COUP 1 dc"))           # ADC0 = ch1; monitor is a DC value
            print(send(port, "NSRC 0 mon0"))
            means = {}
            for q in (0x00000000, 0x00140000, 0x00280000):   # i = 0.0, 20.0, 40.0 (Q16.16)
                print(send(port, f"NEUR 0 i {q}"))
                time.sleep(0.2)
                adc = capture_adc0(port, args.frames, args.adc_ch)
                means[q] = float(adc.mean())
                plot(outdir, f"mon0_i{q:08x}", adc)
            print(send(port, "NEUR 0 i 0"))
            print(send(port, "COUP 1 ac"))            # restore safe coupling
            lo, mid, hi = means[0x0], means[0x00140000], means[0x00280000]
            monotonic = (hi > mid > lo) or (hi < mid < lo)
            span = abs(hi - lo)
            results.append(("mon0", monotonic and span > 30.0,
                            f"DC means i0={lo:.0f} i20={mid:.0f} i40={hi:.0f} span={span:.0f}"))

        # --- spike 0: drive neuron 0 to spike fast, look for pulses (best-effort) ---
        if run("spike"):
            print(send(port, "NEUR 0 profile regular"))
            print(send(port, "NEUR 0 i 0x00280000"))   # strong drive
            print(send(port, "NEUR period 2"))          # update almost every neuron clk
            print(send(port, "NEUR dt 0x00008000"))     # large timestep -> fast dynamics
            print(send(port, "NSRC 0 spike0"))
            time.sleep(0.3)
            adc = capture_adc0(port, args.frames, args.adc_ch)
            plot(outdir, "spike0", adc)
            x = adc - np.mean(adc)
            thr = 5.0 * np.std(x)
            npts = int(np.sum(x > thr))
            results.append(("spike0", npts > 0, f"samples>5sigma={npts} rms={adc.std():.0f} (best-effort)"))

        # leave DAC0 on DDS as a clean default
        send(port, "NSRC 0 dds")

    print("\n==================== CROSSBAR LOOPBACK RESULTS ====================")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:7s} {detail}")
    print(f"\nplots in {outdir}")
    n_fail = sum(0 if ok else 1 for _, ok, _ in results)
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
