//! egui front-end: load a burst .npz, tune the ensemble pipeline, view the SNR gain.

use crate::burst::Burst;
use crate::dsp::{self, Method};
use egui_plot::{Legend, Line, Plot, PlotPoints};

/// Result of the pipeline for one channel (display units already applied).
struct Processed {
    t: Vec<f64>,      // sample index (or decimated index) -> ns
    raw0: Vec<f64>,   // first raw capture, for before/after comparison
    est: Vec<f64>,    // de-noised estimate
    band: Vec<f64>,   // ±sigma/sqrt(N) half-width per (decimated) sample
    snr_single: f64,
    snr_ensemble: f64,
    n_used: usize,
    off_lo: f64,      // per-capture alignment shift range (samples) -- the
    off_hi: f64,      // trigger-quality diagnostic: should be a few samples
}

/// Cached alignment (+ optional de-interleave) for one channel: by far the
/// most expensive stage, so it is NOT redone when only the estimator, filter,
/// or display settings change.
struct AlignedCh {
    stack: Vec<Vec<f64>>,
    offsets: Vec<f64>,
}

pub struct DenoiseApp {
    burst: Option<Burst>,
    bg: Option<Burst>,

    method: Method,
    trim: f64,
    outlier_z: f64,
    do_align: bool,
    subsample: bool,
    deinterleave: bool,
    bg_subtract: bool,
    volts: bool,

    lowpass_mhz: f64,
    moving: i32,
    decimate: i32,
    fs_ghz: f64, // ADC sample rate in GHz (1.0 = 1 GS/s)

    show_ch: [bool; 4],
    show_raw: bool,

    status: String,
    dirty: bool,
    /// alignment inputs changed (file / align / subsample / de-interleave):
    /// drop the per-channel caches before reprocessing.
    stack_dirty: bool,
    aligned: [Option<AlignedCh>; 4],
    bg_aligned: [Option<Vec<Vec<f64>>>; 4],
    processed: [Option<Processed>; 4],
}

impl Default for DenoiseApp {
    fn default() -> Self {
        Self {
            burst: None,
            bg: None,
            method: Method::Mean,
            trim: 0.1,
            outlier_z: 3.0,
            do_align: true,
            subsample: true,
            deinterleave: false,
            bg_subtract: false,
            volts: false,
            lowpass_mhz: 0.0,
            moving: 1,
            decimate: 1,
            fs_ghz: 1.0,
            show_ch: [true, true, false, false],
            show_raw: true,
            status: "Open a burst_*.npz capture to begin.".into(),
            dirty: false,
            stack_dirty: false,
            aligned: Default::default(),
            bg_aligned: Default::default(),
            processed: Default::default(),
        }
    }
}

impl DenoiseApp {
    fn load_burst(&mut self, path: &str) {
        match Burst::load(path) {
            Ok(b) => {
                self.status = format!("Loaded {} — {} captures x {} samples", b.file_name(), b.n, b.len);
                self.burst = Some(b);
                self.dirty = true;
                self.stack_dirty = true;
            }
            Err(e) => self.status = format!("Load failed: {e}"),
        }
    }

    fn load_background(&mut self, path: &str) {
        match Burst::load(path) {
            Ok(b) => {
                self.status = format!("Background: {} ({} captures)", b.file_name(), b.n);
                self.bg = Some(b);
                self.bg_subtract = true;
                self.dirty = true;
                self.stack_dirty = true;
            }
            Err(e) => self.status = format!("Background load failed: {e}"),
        }
    }

    /// Build the alignment caches for every displayed channel that lacks one.
    /// Alignment dominates the pipeline cost, so estimator/filter/display
    /// changes reuse these instead of re-correlating the whole stack.
    fn ensure_caches(&mut self) {
        let Some(burst) = &self.burst else { return };
        for ch in 0..4 {
            if !self.show_ch[ch] || self.aligned[ch].is_some() {
                continue;
            }
            let Some(raw) = burst.chans[ch].as_ref() else { continue };
            if raw.is_empty() {
                continue;
            }
            let (stack, offsets) = if self.do_align {
                dsp::align(raw, self.subsample)
            } else {
                (raw.clone(), vec![0.0; raw.len()])
            };
            let stack: Vec<Vec<f64>> = if self.deinterleave {
                stack.iter().map(|c| dsp::deinterleave(c)).collect()
            } else {
                stack
            };
            self.aligned[ch] = Some(AlignedCh { stack, offsets });
        }
        if self.bg_subtract {
            if let Some(bg) = &self.bg {
                for ch in 0..4 {
                    if !self.show_ch[ch] || self.bg_aligned[ch].is_some() {
                        continue;
                    }
                    let Some(bgc) = bg.chans[ch].as_ref() else { continue };
                    if bgc.is_empty() {
                        continue;
                    }
                    self.bg_aligned[ch] = Some(if self.do_align {
                        dsp::align(bgc, self.subsample).0
                    } else {
                        bgc.clone()
                    });
                }
            }
        }
    }

    /// Run the (cheap) rest of the pipeline for one cached channel.
    fn process_channel(&self, ch: usize) -> Option<Processed> {
        let cache = self.aligned[ch].as_ref()?;
        let stack = &cache.stack;
        if stack.is_empty() {
            return None;
        }
        let fs = self.fs_ghz * 1e9;

        // ensemble estimate + noise stats (on the pre-filter stack)
        let (mut est, n_used) = dsp::ensemble(stack, self.method, self.trim, self.outlier_z);
        let stats = dsp::noise_stats(stack, &est);
        let sigma = stats.sigma;

        // background (blank) subtraction
        if self.bg_subtract {
            if let Some(bgstack) = self.bg_aligned[ch].as_ref() {
                let (bgest, _) = dsp::ensemble(bgstack, self.method, self.trim, self.outlier_z);
                for j in 0..est.len().min(bgest.len()) {
                    est[j] -= bgest[j];
                }
            }
        }

        // raw0 = first capture, aligned/de-interleaved to match the estimate
        let raw0 = stack.first().cloned().unwrap_or_default();

        // 5. temporal filters (on the estimate)
        if self.lowpass_mhz > 0.0 {
            est = dsp::lowpass_fft(&est, fs, self.lowpass_mhz * 1e6);
        }
        if self.moving > 1 {
            est = dsp::moving_avg(&est, self.moving as usize);
        }

        // 6. decimation (estimate, sigma, and raw0 together, keeping alignment)
        let (est, sigma, raw0) = if self.decimate > 1 {
            let f = self.decimate as usize;
            (dsp::decimate(&est, f), dsp::decimate(&sigma, f), dsp::decimate(&raw0, f))
        } else {
            (est, sigma, raw0)
        };

        let dt_ns = 1.0 / self.fs_ghz; // ns per original sample
        let step_ns = dt_ns * self.decimate.max(1) as f64;
        let scale = if self.volts { dsp::VOLTS_PER_COUNT } else { 1.0 };
        let sqrtn = (n_used.max(1) as f64).sqrt();

        let t: Vec<f64> = (0..est.len()).map(|i| i as f64 * step_ns).collect();
        let est_s: Vec<f64> = est.iter().map(|v| v * scale).collect();
        let raw_s: Vec<f64> = raw0.iter().map(|v| v * scale).collect();
        let band: Vec<f64> = sigma.iter().map(|s| s * scale / sqrtn).collect();

        let (off_lo, off_hi) = cache
            .offsets
            .iter()
            .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &v| {
                (lo.min(v), hi.max(v))
            });

        Some(Processed {
            t,
            raw0: raw_s,
            est: est_s,
            band,
            snr_single: stats.snr_single,
            snr_ensemble: stats.snr_ensemble,
            n_used,
            off_lo,
            off_hi,
        })
    }

    fn recompute(&mut self) {
        self.processed = Default::default();
        if self.stack_dirty {
            self.aligned = Default::default();
            self.bg_aligned = Default::default();
            self.stack_dirty = false;
        }
        if self.burst.is_none() {
            self.dirty = false;
            return;
        }
        self.ensure_caches();
        for ch in 0..4 {
            if self.show_ch[ch] {
                self.processed[ch] = self.process_channel(ch);
            }
        }
        self.dirty = false;
    }

    fn export_csv(&mut self) {
        let Some(path) = rfd::FileDialog::new()
            .add_filter("csv", &["csv"])
            .set_file_name("denoised.csv")
            .save_file()
        else {
            return;
        };
        let unit = if self.volts { "v" } else { "counts" };
        let mut hdr = String::from("t_ns");
        let mut cols: Vec<&Processed> = Vec::new();
        for ch in 0..4 {
            if let Some(p) = &self.processed[ch] {
                hdr.push_str(&format!(",ch{ch}_{unit},ch{ch}_sigma"));
                cols.push(p);
            }
        }
        let Some(first) = cols.first() else {
            self.status = "Nothing to export.".into();
            return;
        };
        let n = first.est.len();
        let mut out = String::with_capacity(n * 32);
        out.push_str(&hdr);
        out.push('\n');
        for i in 0..n {
            out.push_str(&format!("{:.3}", first.t[i]));
            for p in &cols {
                let e = p.est.get(i).copied().unwrap_or(f64::NAN);
                let s = p.band.get(i).copied().unwrap_or(f64::NAN)
                    * (p.n_used.max(1) as f64).sqrt(); // back out to per-sample sigma
                out.push_str(&format!(",{e:.6},{s:.6}"));
            }
            out.push('\n');
        }
        match std::fs::write(&path, out) {
            Ok(_) => self.status = format!("Wrote {}", path.display()),
            Err(e) => self.status = format!("CSV write failed: {e}"),
        }
    }

    fn controls(&mut self, ui: &mut egui::Ui) {
        let mut dirty = false;

        ui.heading("Files");
        if ui.button("📂  Open burst .npz…").clicked() {
            if let Some(p) = rfd::FileDialog::new().add_filter("npz", &["npz"]).pick_file() {
                if let Some(s) = p.to_str() {
                    self.load_burst(s);
                }
            }
        }
        ui.horizontal(|ui| {
            if ui.button("Background…").clicked() {
                if let Some(p) = rfd::FileDialog::new().add_filter("npz", &["npz"]).pick_file() {
                    if let Some(s) = p.to_str() {
                        self.load_background(s);
                    }
                }
            }
            if self.bg.is_some() && ui.button("clear").clicked() {
                self.bg = None;
                self.bg_subtract = false;
                self.bg_aligned = Default::default();
                dirty = true;
            }
        });
        if let Some(bg) = &self.bg {
            ui.label(egui::RichText::new(format!("bg: {}", bg.file_name())).weak());
        }

        ui.separator();
        ui.heading("Estimator");
        for m in Method::ALL {
            dirty |= ui.radio_value(&mut self.method, m, m.label()).changed();
        }
        if self.method == Method::Trimmed {
            dirty |= ui
                .add(egui::Slider::new(&mut self.trim, 0.0..=0.45).text("trim frac"))
                .changed();
        }
        if self.method == Method::Outlier {
            dirty |= ui
                .add(egui::Slider::new(&mut self.outlier_z, 1.0..=6.0).text("reject z"))
                .changed();
        }

        ui.separator();
        ui.heading("Alignment");
        let mut stack_dirty = false;
        stack_dirty |= ui.checkbox(&mut self.do_align, "cross-correlate align").changed();
        ui.add_enabled_ui(self.do_align, |ui| {
            stack_dirty |= ui.checkbox(&mut self.subsample, "sub-sample (parabolic)").changed();
        });
        stack_dirty |= ui.checkbox(&mut self.deinterleave, "de-interleave (mod-4 DC)").changed();
        if stack_dirty {
            self.stack_dirty = true;
            dirty = true;
        }
        if self.bg.is_some() {
            dirty |= ui.checkbox(&mut self.bg_subtract, "subtract background").changed();
        }

        ui.separator();
        ui.heading("Temporal filter");
        dirty |= ui
            .add(egui::Slider::new(&mut self.lowpass_mhz, 0.0..=500.0).text("low-pass MHz (0=off)"))
            .changed();
        dirty |= ui
            .add(egui::Slider::new(&mut self.moving, 1..=101).text("moving avg N"))
            .changed();
        dirty |= ui
            .add(egui::Slider::new(&mut self.decimate, 1..=64).text("decimate"))
            .changed();
        dirty |= ui
            .add(egui::Slider::new(&mut self.fs_ghz, 0.1..=2.0).text("fs (GS/s)"))
            .changed();

        ui.separator();
        ui.heading("Display");
        dirty |= ui.checkbox(&mut self.volts, "volts (else ADC counts)").changed();
        dirty |= ui.checkbox(&mut self.show_raw, "overlay first raw capture").changed();
        ui.horizontal(|ui| {
            ui.label("channels:");
            for ch in 0..4 {
                dirty |= ui.checkbox(&mut self.show_ch[ch], format!("{ch}")).changed();
            }
        });

        ui.separator();
        if ui.button("💾  Export CSV…").clicked() {
            self.export_csv();
        }

        if dirty {
            self.dirty = true;
        }
    }
}

impl eframe::App for DenoiseApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Accept a file dropped onto the window.
        let dropped: Option<String> = ctx.input(|i| {
            i.raw
                .dropped_files
                .first()
                .and_then(|f| f.path.as_ref())
                .and_then(|p| p.to_str())
                .map(|s| s.to_string())
        });
        if let Some(p) = dropped {
            self.load_burst(&p);
        }

        if self.dirty {
            self.recompute();
        }

        egui::SidePanel::left("controls")
            .resizable(true)
            .default_width(268.0)
            .show(ctx, |ui| {
                egui::ScrollArea::vertical().show(ui, |ui| self.controls(ui));
            });

        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new(&self.status).monospace());
            });
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            let shown: Vec<usize> = (0..4).filter(|&c| self.show_ch[c] && self.processed[c].is_some()).collect();
            if shown.is_empty() {
                ui.centered_and_justified(|ui| {
                    ui.label("No channel to display — open a burst .npz and select a channel.");
                });
                return;
            }
            let rows = shown.len();
            let avail_h = ui.available_height();
            let plot_h = (avail_h / rows as f32) - 26.0;
            let unit = if self.volts { "V" } else { "counts" };

            for &ch in &shown {
                let p = self.processed[ch].as_ref().unwrap();
                let gain = p.snr_ensemble - p.snr_single;
                ui.label(egui::RichText::new(format!(
                    "ch{ch}:  N={}  single {:.1} dB → ensemble {:.1} dB  (+{:.1} dB)   shifts [{:+.1}..{:+.1}] smp",
                    p.n_used, p.snr_single, p.snr_ensemble, gain, p.off_lo, p.off_hi
                )));

                let est: PlotPoints = p.t.iter().zip(&p.est).map(|(&t, &v)| [t, v]).collect();
                let up: PlotPoints = p.t.iter().zip(p.est.iter().zip(&p.band)).map(|(&t, (&e, &b))| [t, e + b]).collect();
                let dn: PlotPoints = p.t.iter().zip(p.est.iter().zip(&p.band)).map(|(&t, (&e, &b))| [t, e - b]).collect();

                Plot::new(format!("plot_ch{ch}"))
                    .height(plot_h)
                    .legend(Legend::default())
                    .x_axis_label("t (ns)")
                    .y_axis_label(unit)
                    .show(ui, |pu| {
                        if self.show_raw {
                            let raw: PlotPoints =
                                p.t.iter().zip(&p.raw0).map(|(&t, &v)| [t, v]).collect();
                            pu.line(
                                Line::new(raw)
                                    .name("single capture")
                                    .color(egui::Color32::from_gray(130))
                                    .width(0.8),
                            );
                        }
                        pu.line(
                            Line::new(up)
                                .name("±σ/√N")
                                .color(egui::Color32::from_rgb(90, 140, 90))
                                .width(0.6),
                        );
                        pu.line(
                            Line::new(dn)
                                .color(egui::Color32::from_rgb(90, 140, 90))
                                .width(0.6),
                        );
                        pu.line(
                            Line::new(est)
                                .name("de-noised")
                                .color(egui::Color32::from_rgb(70, 160, 240))
                                .width(1.8),
                        );
                    });
            }
        });
    }
}
