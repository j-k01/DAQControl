//! Native Rust (egui) control + scope interface for the ZCU102 high-speed DAQ.
//! Mirrors scripts/dac_scope_qt.py: 4-channel scope, DAC crossbar routing,
//! neuron control, DDS, BRAM waveforms, and UART/Ethernet capture.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app;
mod burst;
mod dsp;
mod proto;

fn main() -> eframe::Result<()> {
    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1360.0, 880.0])
            .with_min_inner_size([1000.0, 640.0])
            .with_title("DAQ scope + control (Rust / egui)"),
        ..Default::default()
    };
    eframe::run_native(
        "daq_scope",
        native_options,
        Box::new(|cc| Ok(Box::new(app::DaqApp::new(cc)))),
    )
}
