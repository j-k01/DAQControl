#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
//! Native Rust (egui) ensemble de-noising for triggered-burst captures.
//! Rust front-end for the `scripts/denoise_burst.py` pipeline: load a burst
//! `.npz`, coherently average N phase-locked ADC captures, and inspect the SNR gain.

mod app;
mod burst;
mod dsp;

fn main() -> eframe::Result<()> {
    // Headless self-check: `denoise_gui --probe file.npz` loads and prints, no window.
    let argv: Vec<String> = std::env::args().collect();
    if argv.iter().any(|a| a == "--probe") {
        let path = argv.last().unwrap();
        match burst::Burst::load(path) {
            Ok(b) => {
                println!("OK {} n={} len={}", b.file_name(), b.n, b.len);
                for ch in 0..4 {
                    if let Some(c) = &b.chans[ch] {
                        let (s, offs) = dsp::align(c, true);
                        let (est, used) = dsp::ensemble(&s, dsp::Method::Mean, 0.1, 3.0);
                        let (_, kept) = dsp::ensemble(&s, dsp::Method::Outlier, 0.1, 3.0);
                        let st = dsp::noise_stats(&s, &est);
                        let (lo, hi) = offs.iter().fold((f64::INFINITY, f64::NEG_INFINITY),
                            |(lo, hi), &v| (lo.min(v), hi.max(v)));
                        println!(
                            "  ch{ch}: used={used} outlier_kept={kept} shifts=[{lo:+.2}..{hi:+.2}] single={:.2}dB ens={:.2}dB gain={:.2}dB est[0..3]={:.1},{:.1},{:.1}",
                            st.snr_single, st.snr_ensemble, st.snr_ensemble - st.snr_single,
                            est[0], est.get(1).copied().unwrap_or(0.0), est.get(2).copied().unwrap_or(0.0)
                        );
                    }
                }
            }
            Err(e) => {
                eprintln!("FAIL: {e}");
                std::process::exit(1);
            }
        }
        std::process::exit(0);
    }

    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1240.0, 840.0])
            .with_min_inner_size([900.0, 560.0])
            .with_title("Burst De-noise"),
        ..Default::default()
    };
    eframe::run_native(
        "denoise_gui",
        options,
        Box::new(|_cc| Ok(Box::new(app::DenoiseApp::default()))),
    )
}
