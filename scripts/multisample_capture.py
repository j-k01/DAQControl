#!/usr/bin/env python3
"""Multisample trigger-synchronized burst capture + coherent denoising.

Drives the firmware's BCPT command: the MicroBlaze repeats N deep DMA captures,
each fired in HARDWARE by the current player's sample-0 pulse (RW3[7] arm mode),
so every repetition starts at the same current-injection phase.  All reps land
strided in DDR and are drained by a single BRDO/UDP pass, then this script
slices, aligns, and coherently averages them.

The current-player cycle marker now crosses in the same FIFO packet as the
DAC-visible sample-zero value, so a correct hardware run should report zero
per-repetition offsets.  Integer cross-correlation remains as a diagnostic and
safety net: any nonzero shift is evidence of a trigger/data-path regression,
while coherent averaging gains sqrt(N) SNR on white noise.

Typical use (step stimulus on the current source, monitor on DAC0 -> ADC ch0):

    python multisample_capture.py --port COM10 --reps 64 --kb 64 \
        --step-ma 0.5 --step-samples 512 --anchor 0

Outputs a .npz (rep stack + aligned average + shifts) under captures/.
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import burst_capture  # noqa: E402

# full-rate burst path: 16 B beat = 4 samples/ch at the ~250 MHz beat clock
ADC_FS_HZ = 1.0e9


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def parse_bcpt_reply(line):
    """'OK BCPT reps=16 bytes_per_rep=65536 stride=131072 total_per_chip=...'"""
    out = {}
    for tok in line.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                out[k] = int(v, 0)
            except ValueError:
                pass
    need = ("reps", "bytes_per_rep", "stride", "total_per_chip")
    if not all(k in out for k in need):
        raise RuntimeError(f"unparseable BCPT reply: {line!r}")
    return out


def capture_multisample(ser, kb, reps, board_ip, cmd_port, local_ip, local_port,
                        drain_timeout=None):
    """Run BCPT + BRDO + UDP drain.  Returns (stacks, meta) where stacks is
    {ch: int16[reps, samples_per_rep]} for ch 0..3."""
    burst_capture.uart_cmd(ser, "STRM STOP", ("OK STRM", "ERR"), timeout=5.0)

    # uart_cmd skips non-matching lines, so a "WARN BCPT overflow" line is
    # passed over and the terminal OK/ERR line is returned.
    bcpt = burst_capture.uart_cmd(ser, f"BCPT {kb}k {reps}",
                                  ("OK BCPT", "ERR"), timeout=120.0)
    if not bcpt.startswith("OK BCPT"):
        raise RuntimeError(f"BCPT failed: {bcpt or '(no UART reply)'}")
    meta = parse_bcpt_reply(bcpt)
    total = meta["total_per_chip"]

    if drain_timeout is None:
        drain_timeout = max(10.0, (2.0 * total / 70.0e6) + 5.0)

    asm = burst_capture.Reassembler(board_ip, cmd_port, local_ip, local_port, total)
    try:
        if not asm.register(timeout=2.0):
            raise RuntimeError("BRST registration timed out (no BRST_READY)")
        brdo = burst_capture.uart_cmd(ser, "BRDO", ("OK BRDO", "ERR"), timeout=10.0)
        req = burst_capture.parse_brdo_request(brdo)
        if not brdo.startswith("OK BRDO") or req is None:
            raise RuntimeError(f"BRDO failed: {brdo or '(no UART reply)'}")
        asm.set_request_id(req)
        deadline = time.time() + drain_timeout
        while time.time() < deadline and not asm.complete():
            time.sleep(0.05)
        meta["coverage"] = (asm.coverage(0), asm.coverage(1))
        if not asm.complete():
            print(f"  WARN: UDP coverage {meta['coverage']} < 100%")
        chans = {}
        chans.update(burst_capture.decode_chip(asm.buf[0], 0))
        chans.update(burst_capture.decode_chip(asm.buf[1], 2))
    finally:
        asm.close()

    # slice the strided DDR layout into a rep stack (per channel: 4 B/sample)
    spr = meta["bytes_per_rep"] // 4      # wanted samples per rep
    sps = meta["stride"] // 4             # rep-to-rep stride in samples
    nrep = meta["reps"]
    stacks = {ch: np.stack([x[r * sps: r * sps + spr] for r in range(nrep)])
              for ch, x in chans.items()}
    return stacks, meta


# --------------------------------------------------------------------------
# alignment + denoising
# --------------------------------------------------------------------------

def _xcorr_shift(a, ref, max_lag):
    """Integer lag of `a` relative to `ref` (positive = a lags ref), via
    FFT cross-correlation restricted to +/-max_lag."""
    n = len(ref)
    fa = np.fft.rfft(a - a.mean(), n=2 * n)
    fr = np.fft.rfft(ref - ref.mean(), n=2 * n)
    cc = np.fft.irfft(fr * np.conj(fa), n=2 * n)
    lags = np.concatenate([np.arange(0, max_lag + 1), np.arange(-max_lag, 0)])
    idx = np.concatenate([np.arange(0, max_lag + 1),
                          np.arange(2 * n - max_lag, 2 * n)])
    k = int(np.argmax(cc[idx]))
    return int(lags[k])


def align_stack(stack, max_lag=64, passes=2):
    """Integer-sample align each rep to the evolving ensemble average.

    Returns (aligned float64 stack, shifts).  Shifts should now be zero because
    the trigger marker travels with the DAC-visible sample-zero packet.  Any
    nonzero shift indicates a stimulus/trigger problem.
    """
    aligned = stack.astype(np.float64)
    shifts = np.zeros(len(stack), dtype=int)
    ref = np.median(aligned, axis=0)      # robust starting reference
    for _ in range(passes):
        for i in range(len(aligned)):
            s = _xcorr_shift(aligned[i], ref, max_lag)
            if s:
                aligned[i] = np.roll(aligned[i], s)
                shifts[i] += s
        ref = aligned.mean(axis=0)
    return aligned, shifts


def coherent_average(aligned, reject_sigma=4.0, trim_frac=0.0):
    """Average an aligned rep stack with outlier-rep rejection.

    1. Rejects whole reps whose correlation to the ensemble mean is an outlier
       (guards against the rare corrupted/glitched rep, e.g. a player restart
       landing mid-capture or a DMA hiccup).
    2. Optionally trims the top/bottom `trim_frac` of reps PER SAMPLE
       (robust to impulsive noise) before averaging.

    Returns dict with avg, std (per-sample), kept mask, and noise stats.
    """
    n = len(aligned)
    mean0 = aligned.mean(axis=0)
    dev = aligned - mean0
    corr = np.array([np.dot(a - a.mean(), mean0 - mean0.mean()) /
                     (np.std(a) * np.std(mean0) * len(mean0) + 1e-12)
                     for a in aligned])
    rms = np.sqrt((dev ** 2).mean(axis=1))
    med, mad = np.median(rms), np.median(np.abs(rms - np.median(rms))) + 1e-12
    keep = np.abs(rms - med) < reject_sigma * 1.4826 * mad
    kept = aligned[keep]

    if trim_frac > 0 and len(kept) > 4:
        k = int(len(kept) * trim_frac)
        if k:
            srt = np.sort(kept, axis=0)
            kept_avg = srt[k:len(kept) - k].mean(axis=0)
        else:
            kept_avg = kept.mean(axis=0)
    else:
        kept_avg = kept.mean(axis=0)

    resid = kept - kept.mean(axis=0)
    noise_per_rep = float(resid.std())
    return {
        "avg": kept_avg,
        "std": kept.std(axis=0),
        "keep": keep,
        "n_kept": int(keep.sum()),
        "n_rejected": int(n - keep.sum()),
        "rep_corr": corr,
        "noise_rms_single": noise_per_rep,
        "noise_rms_avg_est": noise_per_rep / np.sqrt(max(keep.sum(), 1)),
    }


def denoise_stacks(stacks, anchor=0, max_lag=64, reject_sigma=4.0,
                   trim_frac=0.0):
    """Full pipeline: estimate shifts once on the high-SNR anchor channel,
    apply the SAME shifts to all channels (they share the capture window),
    then coherently average each channel."""
    _, shifts = align_stack(stacks[anchor], max_lag=max_lag)
    out = {}
    for ch, stack in stacks.items():
        a = stack.astype(np.float64)
        for i, s in enumerate(shifts):
            if s:
                a[i] = np.roll(a[i], s)
        out[ch] = coherent_average(a, reject_sigma=reject_sigma,
                                   trim_frac=trim_frac)
        out[ch]["aligned"] = a
    return out, shifts


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM10")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--board-ip", default="192.168.2.10")
    ap.add_argument("--cmd-port", type=int, default=5006)
    ap.add_argument("--local-ip", default="192.168.2.1")
    ap.add_argument("--local-port", type=int, default=5005)
    ap.add_argument("--kb", type=int, default=64, help="KB/chip per rep")
    ap.add_argument("--reps", type=int, default=32)
    ap.add_argument("--anchor", type=int, default=0,
                    help="channel used to estimate per-rep shifts")
    ap.add_argument("--max-lag", type=int, default=64)
    ap.add_argument("--trim", type=float, default=0.0,
                    help="per-sample trimmed-mean fraction (e.g. 0.1)")
    ap.add_argument("--step-ma", type=float, default=None,
                    help="if set, program a one-shot current step of this "
                         "amplitude (CURW hold) before capturing")
    ap.add_argument("--step-samples", type=int, default=512,
                    help="player samples in the step's high phase")
    ap.add_argument("--label", default="multisample")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    import serial
    ser = serial.Serial(args.port, args.baud, timeout=5, write_timeout=5)
    time.sleep(0.2)
    try:
        if args.step_ma is not None:
            # 0 -> amp one-shot step via firmware CURS (1 mA = 1.0 Q16.16);
            # 'hold' mode so each BCPT rep replays it from sample 0 via the
            # player restart.  A short zero preamble puts the step edge a
            # little way into every capture window.
            amp_q16 = max(0, min(0x7FFFFFFF, int(round(args.step_ma * 65536))))
            zeros = 32
            print(f"programming {args.step_ma} mA one-shot step "
                  f"({zeros} zero + {args.step_samples} high player samples)")
            rep = burst_capture.uart_cmd(
                ser, f"CURS 1 {zeros} {args.step_samples} 0x{amp_q16:08X} hold",
                ("OK CURS", "ERR"), timeout=10.0)
            if not rep.startswith("OK CURS"):
                raise RuntimeError(f"CURS failed: {rep or '(no UART reply)'}")

        print(f"BCPT {args.kb}k x {args.reps} reps ...")
        stacks, meta = capture_multisample(
            ser, args.kb, args.reps, args.board_ip, args.cmd_port,
            args.local_ip, args.local_port)
    finally:
        ser.close()

    print(f"  got reps={meta['reps']} bytes_per_rep={meta['bytes_per_rep']} "
          f"stride={meta['stride']} coverage={meta.get('coverage')}")

    results, shifts = denoise_stacks(stacks, anchor=args.anchor,
                                     max_lag=args.max_lag,
                                     trim_frac=args.trim)
    print(f"  per-rep shifts (samples): min={shifts.min()} max={shifts.max()} "
          f"median={int(np.median(shifts))}")
    for ch in sorted(results):
        r = results[ch]
        gain = r["noise_rms_single"] / (r["noise_rms_avg_est"] + 1e-12)
        print(f"  ch{ch}: kept {r['n_kept']}/{meta['reps']} reps, "
              f"single-shot noise {r['noise_rms_single']:.2f} cts rms -> "
              f"averaged ~{r['noise_rms_avg_est']:.2f} cts "
              f"({20 * np.log10(gain):.1f} dB SNR gain)")

    # Save in the same layout as the GUI's "Trig Burst Avg" npz (raw_ch* +
    # avg_ch* + offsets) so scripts/denoise_burst.py works on it unchanged:
    #   python denoise_burst.py <out.npz> --method outlier --plot
    out_dir = Path(args.out_dir) if args.out_dir else \
        Path(__file__).resolve().parent.parent.parent / "captures"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"burst_{time.strftime('%Y%m%d_%H%M%S')}_{args.label}"
    path = out_dir / (stem + ".npz")
    np.savez_compressed(
        path, fs_hz=np.float64(ADC_FS_HZ), offsets=shifts,
        **{f"raw_ch{c}": stacks[c].astype(np.int16) for c in stacks},
        **{f"avg_ch{c}": results[c]["avg"] for c in results},
        **{f"std_ch{c}": results[c]["std"] for c in results},
    )
    print(f"  saved {path}")
    print(f"  next: python denoise_burst.py {path} --method outlier --plot")


if __name__ == "__main__":
    main()
