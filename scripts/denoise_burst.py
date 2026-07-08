#!/usr/bin/env python3
"""Multi-capture (ensemble) de-noising for triggered-burst captures.

Reads a ``burst_<timestamp>.npz`` saved by dac_scope_qt.py "Trig Burst Avg"
(keys: ``raw_ch0..3`` = N x L int16 stacks of the pristine phase-locked
captures, ``avg_ch0..3``, ``offsets``) and applies de-noising that exploits the
N repeated captures of the *same* stimulus:

  1. (re)align the N captures to a robust reference   -> removes residual jitter
  2. ensemble estimator (mean / median / trimmed / outlier-reject / SVD)
  3. optional background (stimulus-off) subtraction    -> kills sync artifacts
  4. optional mod-4 de-interleave (ADS54J60 core offset)
  5. optional temporal filter (Butterworth LP / Savitzky-Golay / moving avg /
     decimate)                                          -> integrates out-of-band
  6. per-sample sigma map + SNR-improvement estimate

Only NumPy is required; SciPy unlocks the temporal filters and fractional-sample
alignment (falls back gracefully). Outputs a ``*_denoised.npz`` (+ optional CSV
and plot).

Examples
--------
  python denoise_burst.py captures/burst_20260707_203115.npz --method median --plot
  python denoise_burst.py burst.npz --method outlier --lowpass-mhz 5 --csv
  python denoise_burst.py burst.npz --background captures/burst_bg.npz --volts
  python denoise_burst.py burst.npz --method svd --svd-k 2 --channels 0 1
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

VOLTS_PER_COUNT = 1.9 / 65536.0
FS_DEFAULT = 1.0e9  # ADC sample rate (Hz); full-rate UART capture

try:
    from scipy import ndimage as _ndi
    from scipy import signal as _sig
    HAVE_SCIPY = True
except Exception:                       # noqa: BLE001
    HAVE_SCIPY = False


# --------------------------------------------------------------------------- io
def load_stacks(path):
    """Return {ch: (N,L) float64 raw stack} and the saved offsets (or None)."""
    d = np.load(path)
    raw = {}
    for ch in range(4):
        key = f"raw_ch{ch}"
        if key in d:
            arr = np.asarray(d[key], dtype=np.float64)
            if arr.ndim == 1:           # a single capture -> (1, L)
                arr = arr[None, :]
            raw[ch] = arr
    if not raw:
        # fall back to avg_ch* if a raw stack wasn't saved
        for ch in range(4):
            key = f"avg_ch{ch}"
            if key in d:
                raw[ch] = np.asarray(d[key], dtype=np.float64)[None, :]
    offs = np.asarray(d["offsets"]) if "offsets" in d.files else None
    return raw, offs


# -------------------------------------------------------------------- alignment
def _shift(x, s):
    """Shift x by s samples (fractional): output[i] ~= x[i - s]."""
    if abs(s - round(s)) < 1e-9:
        return np.roll(x, int(round(s)))
    if HAVE_SCIPY:
        return _ndi.shift(x, s, order=3, mode="nearest")
    idx = np.arange(len(x)) - s
    return np.interp(idx, np.arange(len(x)), x)


def align_stack(stack, subsample=True):
    """Cross-correlate each capture to the median reference and shift to align.
    Returns (aligned_stack, offsets)."""
    n, length = stack.shape
    ref = np.median(stack, axis=0)
    ref0 = ref - ref.mean()
    if not np.any(ref0):                # flat reference -> nothing to align on
        return stack.copy(), np.zeros(n)
    aligned = np.empty_like(stack)
    offs = np.zeros(n)
    for i in range(n):
        s = stack[i] - stack[i].mean()
        xc = np.correlate(s, ref0, mode="full")
        k = int(np.argmax(xc))
        off = float(k - (length - 1))
        if subsample and 0 < k < len(xc) - 1:      # parabolic sub-sample refine
            ym1, y0, yp1 = xc[k - 1], xc[k], xc[k + 1]
            denom = ym1 - 2.0 * y0 + yp1
            if denom != 0:
                off += 0.5 * (ym1 - yp1) / denom
        if abs(off) > length // 4:      # reject wild shifts (weak/noisy anchor)
            off = 0.0
        offs[i] = off
        aligned[i] = _shift(stack[i], -off)
    return aligned, offs


# ---------------------------------------------------------------- mod-4 de-int
def deinterleave_baseline(x):
    """Subtract each mod-4 phase mean (ADS54J60 interleave core offset)."""
    y = np.asarray(x, dtype=np.float64).copy()
    for k in range(4):
        sl = slice(k, None, 4)
        if y[sl].size:
            y[sl] -= y[sl].mean()
    return y


# -------------------------------------------------------------- ensemble rules
def ensemble(stack, method="mean", trim=0.1, z=3.0, k=3):
    """Collapse an (N,L) stack to a length-L estimate. Returns (est, n_used)."""
    n = stack.shape[0]
    if n == 1 or method == "mean":
        return stack.mean(0), n
    if method == "median":
        return np.median(stack, 0), n
    if method == "trimmed":
        c = int(n * trim)
        if n - 2 * c < 1:
            return stack.mean(0), n
        srt = np.sort(stack, axis=0)
        return srt[c:n - c].mean(0), n - 2 * c
    if method == "outlier":
        mu = np.median(stack, 0)
        rms = np.sqrt(((stack - mu) ** 2).mean(axis=1))
        thr = np.median(rms) + z * (rms.std() + 1e-12)
        keep = rms <= thr
        used = stack[keep] if keep.sum() >= 2 else stack
        return used.mean(0), int(used.shape[0])
    if method == "svd":                 # keep the top-k singular components
        u, sv, vt = np.linalg.svd(stack, full_matrices=False)
        svk = sv.copy()
        svk[k:] = 0.0
        recon = (u * svk) @ vt
        return recon.mean(0), n
    raise ValueError(f"unknown method {method!r}")


# --------------------------------------------------------------- temporal filt
def temporal_filter(y, fs, lowpass_hz=None, savgol=None, moving=None):
    y = np.asarray(y, dtype=np.float64)
    if lowpass_hz:
        if HAVE_SCIPY:
            wn = min(0.99, lowpass_hz / (fs / 2.0))
            b, a = _sig.butter(4, wn, btype="low")
            y = _sig.filtfilt(b, a, y)
        else:
            sys.stderr.write("  (lowpass needs scipy; skipped)\n")
    if savgol:
        win, order = savgol
        win = int(win) | 1              # force odd
        if HAVE_SCIPY and win > order:
            y = _sig.savgol_filter(y, win, int(order))
        else:
            sys.stderr.write("  (savgol needs scipy; skipped)\n")
    if moving and moving > 1:
        ker = np.ones(int(moving)) / float(moving)
        y = np.convolve(y, ker, mode="same")
    return y


# --------------------------------------------------------------- noise / SNR
def noise_stats(stack, est):
    """Per-sample sigma across captures + effective SNR (dB) of the estimate."""
    n = stack.shape[0]
    sigma = stack.std(axis=0)
    resid = stack - est
    rms_noise = float(np.sqrt((resid ** 2).mean())) if n > 1 else 0.0
    sig_pp = float(est.max() - est.min())
    eff_noise = rms_noise / np.sqrt(max(n, 1))
    single_snr = 20 * np.log10(sig_pp / (rms_noise + 1e-12)) if rms_noise else float("inf")
    est_snr = 20 * np.log10(sig_pp / (eff_noise + 1e-12)) if eff_noise else float("inf")
    return sigma, rms_noise, single_snr, est_snr


# --------------------------------------------------------------------- driver
def process_channel(stack, bg_est, args):
    if args.align:
        stack, _ = align_stack(stack, subsample=not args.no_subsample)
    if args.deinterleave:
        stack = np.stack([deinterleave_baseline(r) for r in stack])
    est, n_used = ensemble(stack, args.method, args.trim, args.outlier_z, args.svd_k)
    sigma, rms, snr1, snrN = noise_stats(stack, est)
    if bg_est is not None:              # stimulus-off background subtraction
        m = min(len(est), len(bg_est))
        est = est[:m] - bg_est[:m]
        sigma = sigma[:m]
    est = temporal_filter(est, args.fs, args.lowpass_hz, args.savgol, args.moving)
    if args.decimate > 1:
        if HAVE_SCIPY:
            est = _sig.decimate(est, args.decimate, ftype="fir", zero_phase=True)
            sigma = _sig.decimate(sigma, args.decimate, ftype="fir", zero_phase=True)
        else:
            sys.stderr.write("  (decimate needs scipy; skipped)\n")
    return {"est": est, "sigma": sigma[:len(est)], "n_used": n_used,
            "rms": rms, "snr_single": snr1, "snr_ensemble": snrN}


def bg_channel_est(stack, args):
    if args.align:
        stack, _ = align_stack(stack, subsample=not args.no_subsample)
    if args.deinterleave:
        stack = np.stack([deinterleave_baseline(r) for r in stack])
    est, _ = ensemble(stack, args.method, args.trim, args.outlier_z, args.svd_k)
    return est


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("burst", help="burst_*.npz from Trig Burst Avg")
    ap.add_argument("--channels", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--method", default="mean",
                    choices=["mean", "median", "trimmed", "outlier", "svd"],
                    help="ensemble estimator (default: mean)")
    ap.add_argument("--trim", type=float, default=0.1, help="trimmed-mean fraction/end")
    ap.add_argument("--outlier-z", type=float, default=3.0, help="outlier reject z-thresh")
    ap.add_argument("--svd-k", type=int, default=3, help="SVD components to keep")
    ap.add_argument("--no-align", dest="align", action="store_false",
                    help="skip re-alignment (use captures as stored)")
    ap.add_argument("--no-subsample", action="store_true", help="integer-only alignment")
    ap.add_argument("--deinterleave", action="store_true", help="mod-4 baseline removal")
    ap.add_argument("--background", help="stimulus-off burst_*.npz to subtract")
    ap.add_argument("--lowpass-mhz", type=float, default=None, help="Butterworth LP cutoff")
    ap.add_argument("--savgol", nargs=2, type=int, metavar=("WIN", "ORDER"),
                    help="Savitzky-Golay window and order")
    ap.add_argument("--moving", type=int, default=None, help="moving-average length")
    ap.add_argument("--decimate", type=int, default=1, help="decimate factor (anti-aliased)")
    ap.add_argument("--fs", type=float, default=FS_DEFAULT, help="ADC sample rate Hz")
    ap.add_argument("--volts", action="store_true", help="scale outputs to volts")
    ap.add_argument("--out", default=None, help="output prefix (default: <burst>_denoised)")
    ap.add_argument("--csv", action="store_true", help="also write a CSV")
    ap.add_argument("--plot", action="store_true", help="show a comparison plot")
    args = ap.parse_args(argv)
    args.lowpass_hz = args.lowpass_mhz * 1e6 if args.lowpass_mhz else None

    raw, _ = load_stacks(args.burst)
    if not raw:
        sys.exit(f"no raw_ch*/avg_ch* arrays found in {args.burst}")
    chans = [c for c in args.channels if c in raw]
    scale = VOLTS_PER_COUNT if args.volts else 1.0
    unit = "V" if args.volts else "counts"

    bg = {}
    if args.background:
        bg_raw, _ = load_stacks(args.background)
        bg = {c: bg_channel_est(bg_raw[c], args) for c in chans if c in bg_raw}

    print(f"burst: {args.burst}")
    n0, l0 = next(iter(raw.values())).shape
    print(f"  N={n0} captures x L={l0} samples/ch | method={args.method} "
          f"align={args.align} deint={args.deinterleave} "
          f"bg={'yes' if args.background else 'no'} scipy={HAVE_SCIPY}")

    results, out_len = {}, None
    for ch in chans:
        r = process_channel(raw[ch], bg.get(ch), args)
        results[ch] = r
        out_len = len(r["est"])
        gain = r["snr_ensemble"] - r["snr_single"]
        print(f"  ch{ch}: used {r['n_used']}/{raw[ch].shape[0]} caps | "
              f"single-cap SNR {r['snr_single']:5.1f} dB -> ensemble "
              f"{r['snr_ensemble']:5.1f} dB  (+{gain:4.1f} dB)")

    # ---- save npz ----
    prefix = args.out or os.path.splitext(args.burst)[0] + "_denoised"
    npz_path = prefix + ".npz"
    t_ns = np.arange(out_len) * (args.decimate if args.decimate > 1 else 1)
    save = {"t_ns": t_ns, "unit": np.array(unit)}
    for ch, r in results.items():
        save[f"denoised_ch{ch}"] = r["est"] * scale
        save[f"sigma_ch{ch}"] = r["sigma"] * scale
    np.savez_compressed(npz_path, **save)
    print(f"  -> {npz_path}")

    # ---- CSV ----
    if args.csv:
        csv_path = prefix + ".csv"
        cols = ["t_ns"] + [f"ch{ch}_{unit}" for ch in results] \
                        + [f"ch{ch}_sigma" for ch in results]
        mat = [t_ns] + [results[ch]["est"] * scale for ch in results] \
                     + [results[ch]["sigma"] * scale for ch in results]
        np.savetxt(csv_path, np.column_stack(mat), delimiter=",",
                   header=",".join(cols), comments="")
        print(f"  -> {csv_path}")

    # ---- plot ----
    if args.plot:
        try:
            import matplotlib.pyplot as plt
        except Exception:               # noqa: BLE001
            sys.stderr.write("plot needs matplotlib; skipped\n")
            return
        fig, axes = plt.subplots(len(results), 1, sharex=True,
                                 figsize=(10, 2.2 * len(results)), squeeze=False)
        for ax, (ch, r) in zip(axes[:, 0], results.items()):
            raw0 = raw[ch][0][:len(r["est"])] * scale       # a single raw capture
            ax.plot(raw0, color="0.6", lw=0.5, label="single capture")
            e = r["est"]
            ax.plot(e, color="C3", lw=1.3, label=f"denoised ({args.method})")
            ax.fill_between(np.arange(len(e)), e - r["sigma"] / np.sqrt(max(r["n_used"], 1)),
                            e + r["sigma"] / np.sqrt(max(r["n_used"], 1)),
                            color="C3", alpha=0.2, label="+/- sigma/sqrt(N)")
            ax.set_ylabel(f"ch{ch} [{unit}]")
            ax.grid(alpha=0.3)
            ax.legend(loc="upper right", fontsize=8)
        axes[-1, 0].set_xlabel("sample")
        fig.suptitle(f"{os.path.basename(args.burst)}  -  {args.method} "
                     f"de-noise (N={n0})")
        fig.tight_layout()
        plt.show()


if __name__ == "__main__":
    main()
