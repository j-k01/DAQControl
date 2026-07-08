# denoise_gui

Native Rust (egui/eframe) front-end for the multi-sample ensemble de-noising in
`scripts/denoise_burst.py`. Loads a triggered-burst `.npz` (as written by the Qt
GUI's *Trig Burst Avg* / `_save_burst`), coherently averages the N phase-locked ADC
captures, and shows the SNR gain interactively.

## Build & run

```
cd denoise_gui
cargo run --release
```

Then **Open burst .npz…** (or drag a `burst_*.npz` onto the window).

Headless self-check (no window): `denoise_gui --probe path/to/burst.npz`.

## Pipeline (mirrors denoise_burst.py)

- **Align** — cross-correlate each capture to the median reference, optional
  parabolic sub-sample shift.
- **Estimator** — coherent mean / median / trimmed mean / outlier-reject mean.
- **De-interleave** — optional mod-4 JESD lane-DC removal.
- **Background** — optional blank-capture subtraction (load a second `.npz`).
- **Temporal filter** — FFT brick-wall low-pass (MHz), moving average,
  anti-aliased decimation.
- **Readout** — per-channel single vs ensemble SNR (dB) and the √N gain, with the
  ±σ/√N band drawn around the de-noised trace.

Export the de-noised traces + per-sample σ to CSV via **Export CSV…**.

## Input format

`.npz` members: `raw_ch0..3` = N (captures) × L (samples) `int16`. `avg_ch*` and
`offsets` are ignored (recomputed here).
