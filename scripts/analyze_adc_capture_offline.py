import argparse
import csv
from pathlib import Path

import numpy as np


def signed16(value):
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def bswap16(value):
    value &= 0xFFFF
    return ((value & 0xFF) << 8) | (value >> 8)


def load_capture(path):
    captures = {0: [], 1: [], 2: [], 3: []}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            source = int(row["source"])
            if source in captures:
                captures[source].append(int(row["word_hex"], 16))
    return captures


def word_samples(word, byte_swap=False):
    lo = word & 0xFFFF
    hi = (word >> 16) & 0xFFFF
    if byte_swap:
        lo = bswap16(lo)
        hi = bswap16(hi)
    return signed16(lo), signed16(hi)


def combine_converter(captures, low_source, high_source, byte_swap=False):
    count = min(len(captures[low_source]), len(captures[high_source]))
    samples = np.empty(count * 4, dtype=np.float64)
    for index in range(count):
        lo0, hi0 = word_samples(captures[low_source][index], byte_swap)
        lo1, hi1 = word_samples(captures[high_source][index], byte_swap)
        out = 4 * index
        samples[out + 0] = lo0
        samples[out + 1] = hi0
        samples[out + 2] = lo1
        samples[out + 3] = hi1
    return samples


def sine_fit_metrics(samples, window_samples):
    y = samples[:min(len(samples), window_samples)]
    y = y - np.mean(y)
    rms = float(np.sqrt(np.mean(y * y)))
    peak = float(np.max(np.abs(y)))
    if len(y) < 8 or rms == 0.0:
        return {
            "rms": rms,
            "peak": peak,
            "bin": 0,
            "amplitude": 0.0,
            "r2": 0.0,
            "power_ratio": 0.0,
        }

    window = np.hanning(len(y))
    spec = np.fft.rfft(y * window)
    power = np.abs(spec) ** 2
    power[0] = 0.0
    bin_index = int(np.argmax(power))
    total_power = float(np.sum(power))
    power_ratio = float(power[bin_index] / total_power) if total_power else 0.0

    n = np.arange(len(y), dtype=np.float64)
    cos = np.cos(2.0 * np.pi * bin_index * n / len(y))
    sin = np.sin(2.0 * np.pi * bin_index * n / len(y))
    design = np.column_stack([cos, sin, np.ones(len(y))])
    coef, *_ = np.linalg.lstsq(design, samples[:len(y)], rcond=None)
    fit = design @ coef
    ss_res = float(np.sum((samples[:len(y)] - fit) ** 2))
    centered = samples[:len(y)] - np.mean(samples[:len(y)])
    ss_tot = float(np.sum(centered * centered))
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 0.0
    amplitude = float(np.hypot(coef[0], coef[1]))

    return {
        "rms": rms,
        "peak": peak,
        "bin": bin_index,
        "amplitude": amplitude,
        "r2": r2,
        "power_ratio": power_ratio,
    }


def summarize_stream(name, samples, window_samples):
    metrics = sine_fit_metrics(samples, window_samples)
    lines = [
        name,
        f"  samples={len(samples)} min={int(np.min(samples))} max={int(np.max(samples))} mean={float(np.mean(samples)):.3f}",
        f"  rms_ac={metrics['rms']:.3f} peak_ac={metrics['peak']:.3f}",
        f"  strongest_fft_bin={metrics['bin']} sine_fit_amp={metrics['amplitude']:.3f} sine_fit_r2={metrics['r2']:.6f} power_ratio={metrics['power_ratio']:.6f}",
        "  first32=" + " ".join(str(int(v)) for v in samples[:32]),
    ]
    for phase in range(4):
        phase_samples = samples[phase::4]
        lines.append(
            f"  phase{phase}: min={int(np.min(phase_samples))} max={int(np.max(phase_samples))} "
            f"mean={float(np.mean(phase_samples)):.3f} unique_first200k={len(set(int(v) for v in phase_samples[:200000]))}"
        )
    return "\n".join(lines)


def write_plot(path, streams, plot_samples):
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(streams), 1, figsize=(14, max(3.0, 2.7 * len(streams))), constrained_layout=True)
    if len(streams) == 1:
        axes = [axes]
    for ax, (name, samples) in zip(axes, streams.items()):
        ax.plot(samples[:plot_samples], linewidth=0.8)
        ax.set_title(name)
        ax.set_ylabel("signed 16-bit")
        ax.grid(True, alpha=0.25)
    axes[-1].set_xlabel("reconstructed ADC sample index")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="scripts/captures/sine_loopback_sources_0_1_2_3.csv")
    parser.add_argument("--out-prefix", default="scripts/captures/sine_loopback_offline_analysis")
    parser.add_argument("--window-samples", type=int, default=65536)
    parser.add_argument("--plot-samples", type=int, default=4096)
    args = parser.parse_args()

    captures = load_capture(Path(args.csv))
    streams = {
        "adc1_converter0_raw_src0_1": combine_converter(captures, 0, 1, byte_swap=False),
        "adc1_converter1_raw_src2_3": combine_converter(captures, 2, 3, byte_swap=False),
        "adc1_converter0_bswap_src0_1": combine_converter(captures, 0, 1, byte_swap=True),
        "adc1_converter1_bswap_src2_3": combine_converter(captures, 2, 3, byte_swap=True),
    }

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    summary_path = out_prefix.with_suffix(".txt")
    plot_path = out_prefix.with_suffix(".png")

    summary = "\n\n".join(
        summarize_stream(name, samples, args.window_samples)
        for name, samples in streams.items()
    )
    summary_path.write_text(summary + "\n")
    write_plot(plot_path, streams, args.plot_samples)

    print(summary)
    print(f"wrote {summary_path}")
    print(f"wrote {plot_path}")


if __name__ == "__main__":
    main()
