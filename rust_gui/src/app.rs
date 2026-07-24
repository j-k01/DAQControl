//! egui application: scope + control panels.

use std::sync::mpsc::{Receiver, Sender};
use std::time::Instant;

use eframe::egui;
use egui::{Color32, Pos2, Stroke};
use egui_plot::{Line, Plot, PlotPoints};

use crate::dsp;
use crate::proto::{self, BoardCfg, Cmd, Evt, LiveShared};

#[derive(PartialEq, Clone, Copy)]
enum Tab {
    Neuron,
    Xbar,
    Capture,
    Waveforms,
}

#[derive(serde::Serialize, serde::Deserialize, Clone)]
struct CustomProfile {
    name: String,
    a: f64,
    b: f64,
    c: f64,
    d: f64,
    iconst: f64,
}

fn ch_color(ch: usize) -> Color32 {
    let (r, g, b) = dsp::CH_COLORS[ch];
    Color32::from_rgb(r, g, b)
}

pub struct DaqApp {
    tx: Sender<Cmd>,
    rx: Receiver<Evt>,
    live: LiveShared,

    ports: Vec<String>,
    sel_port: String,
    connected: bool,
    conn_msg: String,
    conn_ok: bool,
    dark_mode: bool,

    tab: Tab,

    // latest capture (raw counts) + cached display series
    chans: [Vec<i16>; 4],
    fs: f64,
    series: [Vec<[f64; 2]>; 4],
    display_dirty: bool,
    display_live: bool,
    live_mean: [Vec<f32>; 4],
    live_sequence: u64,

    // view toggles
    fft_view: bool,
    time_y_ranges: [[f64; 2]; 4],
    fft_y_ranges: [[f64; 2]; 4],
    deinterleave: bool,
    auto_y: bool,
    plot_view_revision: u64,

    // capture controls
    collect_idx: usize,
    capt_frames_idx: usize,
    auto_sample: bool,
    last_auto: Instant,
    busy: bool,
    live_requested: bool,
    live_window: usize,
    live_reps_per_batch: usize,
    live_stats: String,

    // crossbar
    staged_src: [usize; 4],
    applied_src: [Option<usize>; 4],
    dac_status: [String; 4],

    // neuron
    neuron_prof_idx: [usize; 4],
    neuron_running: [Option<String>; 4],
    np_values: [f64; 5],
    load_prof_idx: usize,
    save_name: String,
    custom: Vec<CustomProfile>,
    dt_idx: usize,
    neuron_period: u32,
    neuron_status: String,

    // dds
    dds_freq_mhz: f64,

    // neuron current player
    current_kind: usize,
    current_amp_ma: f64,
    current_gain: f64,
    current_freq_hz: f64,
    current_duty: f64,
    current_step_zero: usize,
    current_step_high: usize,
    current_step_cps: u32,
    current_step_loop: bool,
    current_running: bool,
    current_status: String,

    // waveform builder
    wf_ch: usize, // 0..3, 4=all
    wf_kind: usize,
    wf_period: i32,
    wf_width: i32,
    wf_vlo: f64,
    wf_vhi: f64,

    status: String,

    // raw command + streaming + STAT output
    raw_cmd: String,
    stream_decim: i32,
    stream_cic: bool,
    stat_raw: String,
}

const DT_OPTIONS: [(&str, u32); 6] = [
    ("0.25x slow", 0x2000),
    ("0.5x", 0x4000),
    ("1x normal", 0x8000),
    ("2x", 0x10000),
    ("4x fast", 0x20000),
    ("8x faster", 0x40000),
];

impl DaqApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        cc.egui_ctx.set_visuals(egui::Visuals::dark());
        let cfg = BoardCfg::default();
        let (tx, rx, live) = proto::spawn(cc.egui_ctx.clone(), cfg);
        let ports = list_ports();
        let sel_port = ports
            .iter()
            .find(|p| p.contains("COM10"))
            .cloned()
            .or_else(|| ports.first().cloned())
            .unwrap_or_else(|| "COM10".into());
        let mut app = Self {
            tx,
            rx,
            live,
            ports,
            sel_port,
            connected: false,
            conn_msg: "not connected".into(),
            conn_ok: false,
            dark_mode: true,
            tab: Tab::Xbar,
            chans: Default::default(),
            fs: 1.0e9,
            series: Default::default(),
            display_dirty: false,
            display_live: false,
            live_mean: Default::default(),
            live_sequence: 0,
            fft_view: false,
            time_y_ranges: [[-0.95, 0.95]; 4],
            fft_y_ranges: [[-90.0, 5.0]; 4],
            deinterleave: false,
            auto_y: false,
            plot_view_revision: 0,
            collect_idx: 0,
            capt_frames_idx: 2,
            auto_sample: false,
            last_auto: Instant::now(),
            busy: false,
            live_requested: false,
            live_window: 16,
            live_reps_per_batch: 4,
            live_stats: "idle".into(),
            staged_src: [1, 1, 1, 1], // DDS
            applied_src: [None; 4],
            dac_status: Default::default(),
            neuron_prof_idx: [0, 1, 2, 3],
            neuron_running: Default::default(),
            np_values: [0.02, 0.20, -65.0, 8.0, 10.0],
            load_prof_idx: 0,
            save_name: String::new(),
            custom: load_custom(),
            dt_idx: 2,
            neuron_period: 1,
            neuron_status: "—".into(),
            dds_freq_mhz: 62.5,
            current_kind: 0,
            current_amp_ma: 15.0,
            current_gain: 20.0,
            current_freq_hz: 5_000.0,
            current_duty: 50.0,
            current_step_zero: 16,
            current_step_high: 48,
            current_step_cps: 1,
            current_step_loop: false,
            current_running: false,
            current_status: "not programmed".into(),
            wf_ch: 4,
            wf_kind: 0,
            wf_period: 35,
            wf_width: 7,
            wf_vlo: 0.0,
            wf_vhi: (dsp::DAC_FULLSCALE as f64 * dsp::VOLTS_PER_COUNT),
            status: "Connect to a COM port to begin.".into(),
            raw_cmd: String::new(),
            stream_decim: 128,
            stream_cic: false,
            stat_raw: String::new(),
        };
        for s in app.dac_status.iter_mut() {
            *s = "not programmed".into();
        }
        app
    }

    fn profile_names(&self) -> Vec<String> {
        let mut v: Vec<String> = dsp::BUILTIN_PROFILES
            .iter()
            .map(|s| s.to_string())
            .collect();
        v.extend(self.custom.iter().map(|c| c.name.clone()));
        v
    }

    fn send(&self, c: Cmd) {
        let _ = self.tx.send(c);
    }

    fn drain_events(&mut self) {
        while let Ok(evt) = self.rx.try_recv() {
            match evt {
                Evt::Connected(Ok(port)) => {
                    self.connected = true;
                    self.conn_ok = true;
                    self.conn_msg = format!("connected {port} (idle)");
                    self.status =
                        "Connected: routes/profiles preserved; fast neuron timing restored.".into();
                }
                Evt::Connected(Err(e)) => {
                    self.connected = false;
                    self.conn_ok = false;
                    self.conn_msg = format!("connect failed: {e}");
                }
                Evt::Disconnected => {
                    self.connected = false;
                    self.conn_ok = false;
                    self.conn_msg = "disconnected".into();
                    self.applied_src = [None; 4];
                }
                Evt::Reply(s) => self.status = s,
                Evt::Stat { ok, health, raw } => {
                    self.conn_ok = ok;
                    self.stat_raw = raw;
                    self.conn_msg = if ok {
                        format!("DAQ board OK  [{health}]")
                    } else {
                        "no DAQ response (wrong port / board down)".into()
                    };
                }
                Evt::RouteDone {
                    ch,
                    ok,
                    src_idx,
                    detail,
                } => {
                    self.busy = false;
                    let c = ch as usize;
                    if ok {
                        self.applied_src[c] = Some(src_idx);
                        self.dac_status[c] =
                            format!("OK — {} ({detail})", dsp::source_label(src_idx));
                    } else {
                        self.applied_src[c] = None;
                        self.dac_status[c] =
                            format!("ERR — {} not set: {detail}", dsp::source_label(src_idx));
                    }
                }
                Evt::RoutesRead { routes, detail } => {
                    self.applied_src = routes;
                    for ch in 0..4 {
                        if let Some(index) = routes[ch] {
                            self.staged_src[ch] = index;
                            self.dac_status[ch] =
                                format!("LIVE — {} ({detail})", dsp::source_label(index));
                        } else {
                            self.dac_status[ch] = format!("ERR — route unreadable ({detail})");
                        }
                    }
                }
                Evt::NeuronDone {
                    target,
                    ok,
                    profile,
                } => {
                    if ok {
                        if target == "all" {
                            for n in 0..4 {
                                self.neuron_running[n] = Some(profile.clone());
                            }
                        } else if let Ok(n) = target.parse::<usize>() {
                            if n < 4 {
                                self.neuron_running[n] = Some(profile.clone());
                            }
                        }
                        self.neuron_status = format!("neuron {target}: OK — {profile}");
                    } else {
                        self.neuron_status = format!("neuron {target}: ERR");
                    }
                }
                Evt::Capture {
                    kind,
                    chans,
                    cov,
                    tries,
                    saved,
                } => {
                    self.busy = false;
                    self.chans = chans;
                    self.display_live = false;
                    self.display_dirty = true;
                    let where_ = saved.map(|s| format!("  -> {s}")).unwrap_or_default();
                    let retry = if tries > 1 {
                        format!("  ({tries} tries)")
                    } else {
                        String::new()
                    };
                    self.status = format!(
                        "{kind} capture: {} samples/ch  cov {:.0}%{retry}{where_}",
                        self.chans[0].len(),
                        100.0 * cov
                    );
                }
                Evt::CurrentDone {
                    ok,
                    running,
                    status,
                } => {
                    self.busy = false;
                    self.current_running = running;
                    self.current_status = if ok {
                        status.clone()
                    } else {
                        format!("ERR: {status}")
                    };
                    self.status = self.current_status.clone();
                }
                Evt::Status(s) => self.status = s,
                Evt::Error(e) => {
                    self.busy = false;
                    self.status = format!("⚠ {e}");
                }
            }
        }
    }

    fn poll_live_snapshot(&mut self) {
        let snapshot = match self.live.read() {
            Ok(snapshot) if snapshot.sequence != self.live_sequence => snapshot.clone(),
            _ => return,
        };
        self.live_sequence = snapshot.sequence;
        self.live_requested = snapshot.running;
        if !snapshot.average[0].is_empty() {
            self.live_mean = snapshot.average;
            self.display_live = true;
            self.display_dirty = true;
        } else if snapshot.running {
            // A board-setting change restarts the rolling accumulator. Clear
            // the old trace immediately so it is not mistaken for new data.
            self.live_mean = Default::default();
            self.display_live = true;
            self.display_dirty = true;
        }
        self.live_stats = format!(
            "{} held / {} total | {:.1} captures/s | {:.1} batches/s | {:.0}% UDP | drain {}",
            snapshot.held,
            snapshot.total,
            snapshot.capture_hz,
            snapshot.batch_hz,
            100.0 * snapshot.coverage,
            snapshot.drain_attempts,
        );
        if !snapshot.last_error.is_empty() {
            self.live_stats = format!(
                "{} | {} failures | {}",
                self.live_stats, snapshot.failures, snapshot.last_error
            );
        }
    }

    fn rebuild_display(&mut self) {
        for ch in 0..4 {
            let counts: Vec<f64> = if self.display_live {
                self.live_mean[ch]
                    .iter()
                    .map(|&value| value as f64)
                    .collect()
            } else if self.deinterleave {
                dsp::deinterleave_baseline(&self.chans[ch])
            } else {
                self.chans[ch].iter().map(|&value| value as f64).collect()
            };
            self.series[ch] = if self.fft_view {
                dsp::magnitude_db(&counts, self.fs)
            } else if self.display_live {
                dsp::peak_envelope(&counts, self.fs, 4096)
            } else {
                let count = counts.len();
                let stride = (count / 6000).max(1);
                counts
                    .iter()
                    .step_by(stride)
                    .enumerate()
                    .map(|(index, &value)| {
                        [
                            (index * stride) as f64 / self.fs,
                            value * dsp::VOLTS_PER_COUNT,
                        ]
                    })
                    .collect()
            };
        }
        self.display_dirty = false;
    }
    fn autoscale_once(&mut self) {
        let min_span = if self.fft_view { 10.0 } else { 0.04 };
        let ranges = if self.fft_view {
            &mut self.fft_y_ranges
        } else {
            &mut self.time_y_ranges
        };
        for (channel, series) in self.series.iter().enumerate() {
            let mut low = f64::INFINITY;
            let mut high = f64::NEG_INFINITY;
            for point in series {
                if point[1].is_finite() {
                    low = low.min(point[1]);
                    high = high.max(point[1]);
                }
            }
            if low.is_finite() && high.is_finite() {
                let center = 0.5 * (low + high);
                let span = (high - low).max(min_span);
                let padding = 0.10 * span;
                ranges[channel] = [center - 0.5 * span - padding, center + 0.5 * span + padding];
            }
        }
    }
    // -------------------------------------------------------- panel builders
    fn connection_bar(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.label("COM");
            egui::ComboBox::from_id_salt("port")
                .selected_text(&self.sel_port)
                .show_ui(ui, |ui| {
                    for p in &self.ports {
                        ui.selectable_value(&mut self.sel_port, p.clone(), p);
                    }
                });
            if ui.button("↻").clicked() {
                self.ports = list_ports();
            }
            let label = if self.connected {
                "Reconnect"
            } else {
                "Connect"
            };
            if ui.button(label).clicked() {
                self.conn_msg = format!("connecting {}...", self.sel_port);
                self.send(Cmd::Connect(self.sel_port.clone()));
            }
            if self.connected && ui.button("STAT").clicked() {
                self.send(Cmd::Stat);
            }
            if self.connected && ui.button("Disconnect").clicked() {
                self.send(Cmd::Disconnect);
            }
            if ui.button("Fit plots").clicked() {
                self.autoscale_once();
                self.plot_view_revision = self.plot_view_revision.wrapping_add(1);
            }
            let auto_changed = ui.checkbox(&mut self.auto_y, "Auto Y").changed();
            if auto_changed && self.auto_y {
                self.autoscale_once();
                self.plot_view_revision = self.plot_view_revision.wrapping_add(1);
            }
            let mut dark = self.dark_mode;
            if ui.checkbox(&mut dark, "Dark").changed() {
                self.dark_mode = dark;
                ui.ctx().set_visuals(if dark {
                    egui::Visuals::dark()
                } else {
                    egui::Visuals::light()
                });
            }
        });
        let col = if self.conn_ok {
            Color32::from_rgb(0x81, 0xC7, 0x84)
        } else if self.connected {
            Color32::from_rgb(0xFF, 0xB7, 0x4D)
        } else {
            Color32::from_rgb(0xE5, 0x73, 0x73)
        };
        ui.colored_label(col, &self.conn_msg);
    }

    fn capture_bar(&mut self, ui: &mut egui::Ui) {
        ui.group(|ui| {
            ui.label(egui::RichText::new("Capture (always available)").strong());
            ui.add_enabled_ui(self.connected, |ui| {
                ui.horizontal(|ui| {
                    egui::ComboBox::from_id_salt("capt_frames")
                        .selected_text(format!(
                            "{} frames",
                            dsp::CAPT_FRAME_OPTIONS[self.capt_frames_idx]
                        ))
                        .show_ui(ui, |ui| {
                            for (index, frames) in dsp::CAPT_FRAME_OPTIONS.iter().enumerate() {
                                ui.selectable_value(
                                    &mut self.capt_frames_idx,
                                    index,
                                    format!("{frames} frames"),
                                );
                            }
                        });
                    if ui
                        .add_enabled(
                            !self.busy && !self.live_requested,
                            egui::Button::new("UART Capture"),
                        )
                        .clicked()
                    {
                        self.busy = true;
                        self.status = "UART capturing...".into();
                        self.send(Cmd::UartCapture(
                            dsp::CAPT_FRAME_OPTIONS[self.capt_frames_idx],
                        ));
                    }
                });
                ui.horizontal(|ui| {
                    egui::ComboBox::from_id_salt("collect_size")
                        .selected_text(dsp::COLLECT_SIZES[self.collect_idx].1)
                        .show_ui(ui, |ui| {
                            for (index, (_, label)) in dsp::COLLECT_SIZES.iter().enumerate() {
                                ui.selectable_value(&mut self.collect_idx, index, *label);
                            }
                        });
                    if ui
                        .add_enabled(
                            !self.busy && !self.live_requested,
                            egui::Button::new("Collect Ethernet"),
                        )
                        .clicked()
                    {
                        self.busy = true;
                        self.status = "collecting burst over Ethernet...".into();
                        self.send(Cmd::CollectEth {
                            bytes: dsp::COLLECT_SIZES[self.collect_idx].0,
                            save: true,
                        });
                    }
                    let auto_label = if self.auto_sample {
                        "Stop Auto-Sample"
                    } else {
                        "Start Auto-Sample"
                    };
                    if ui
                        .add_enabled(!self.live_requested, egui::Button::new(auto_label))
                        .clicked()
                    {
                        self.auto_sample = !self.auto_sample;
                        if self.auto_sample {
                            self.last_auto = Instant::now() - std::time::Duration::from_secs(2);
                        }
                    }
                });

                ui.separator();
                ui.label(egui::RichText::new("Continuous trigger average").strong());
                ui.add_enabled_ui(!self.live_requested, |ui| {
                    ui.horizontal(|ui| {
                        ui.label("window");
                        ui.add(
                            egui::DragValue::new(&mut self.live_window)
                                .range(2..=64)
                                .speed(1),
                        );
                        ui.label("reps/batch");
                        ui.add(
                            egui::DragValue::new(&mut self.live_reps_per_batch)
                                .range(1..=8)
                                .speed(1),
                        );
                    });
                });
                let live_label = if self.live_requested {
                    "Stop Live Trigger Average"
                } else {
                    "Start Live Trigger Average"
                };
                if ui.button(live_label).clicked() {
                    if self.live_requested {
                        self.send(Cmd::StopLiveAverage);
                        self.live_requested = false;
                        self.live_stats = "stop requested".into();
                    } else {
                        self.auto_sample = false;
                        self.live_requested = true;
                        self.display_live = true;
                        self.live_stats = "starting...".into();
                        self.send(Cmd::StartLiveAverage {
                            bytes: dsp::COLLECT_SIZES[self.collect_idx].0,
                            reps_per_batch: self.live_reps_per_batch,
                            window: self.live_window,
                        });
                    }
                }
                ui.label(
                    egui::RichText::new(&self.live_stats)
                        .size(10.0)
                        .color(Color32::from_rgb(0x9f, 0xb3, 0xc8)),
                );
            });
        });
    }
    fn tab_xbar(&mut self, ui: &mut egui::Ui) {
        for ch in 0..4 {
            ui.group(|ui| {
                ui.horizontal(|ui| {
                    ui.label(egui::RichText::new(format!("DAC{ch}")).strong());
                    ui.colored_label(ch_color(ch), "●");
                });
                egui::ComboBox::from_id_salt(format!("src{ch}"))
                    .selected_text(dsp::source_label(self.staged_src[ch]))
                    .show_ui(ui, |ui| {
                        for i in 0..dsp::SOURCES.len() {
                            ui.selectable_value(&mut self.staged_src[ch], i, dsp::source_label(i));
                        }
                    });
                ui.horizontal(|ui| {
                    if ui.button("Confirm route").clicked() && self.connected {
                        self.dac_status[ch] =
                            format!("programming {}…", dsp::source_label(self.staged_src[ch]));
                        self.send(Cmd::ApplyRoute {
                            ch: ch as u8,
                            src_idx: self.staged_src[ch],
                        });
                    }
                    ui.label(&self.dac_status[ch]);
                });
            });
        }
        ui.horizontal(|ui| {
            if ui
                .add_enabled(self.connected, egui::Button::new("Read hardware routes"))
                .clicked()
            {
                self.send(Cmd::ReadRoutes);
            }
            ui.label("Route changes do not program neuron profiles.");
        });
        ui.separator();
        ui.label(egui::RichText::new("Crossbar routing (16 → 4)").strong());
        ui.label(
            egui::RichText::new("solid = live route · dashed = staged")
                .size(10.0)
                .color(Color32::GRAY),
        );
        self.draw_crossbar(ui);
    }

    fn draw_crossbar(&self, ui: &mut egui::Ui) {
        let n = dsp::SOURCES.len();
        let h = (24 * n + 24) as f32;
        let (rect, _resp) =
            ui.allocate_exact_size(egui::vec2(ui.available_width(), h), egui::Sense::hover());
        let p = ui.painter_at(rect);
        p.rect_filled(rect, 4.0, Color32::from_rgb(0x0d, 0x11, 0x16));
        let top = rect.top() + 16.0;
        let bot = rect.bottom() - 16.0;
        let src_x = rect.left() + 118.0;
        let dac_x = rect.right() - 70.0;
        let span = (bot - top).max(1.0);
        let sy = |i: usize| top + span * (i as f32 / (n as f32 - 1.0).max(1.0));
        let dy = |c: usize| top + span * ((c as f32 + 0.5) / 4.0);
        let fid = egui::FontId::proportional(11.0);

        // staged (dashed) beneath
        for c in 0..4 {
            let si = self.staged_src[c];
            if self.applied_src[c] == Some(si) {
                continue;
            }
            let shapes = egui::Shape::dashed_line(
                &[Pos2::new(src_x, sy(si)), Pos2::new(dac_x, dy(c))],
                Stroke::new(1.4, Color32::from_rgba_unmultiplied(150, 162, 176, 160)),
                6.0,
                4.0,
            );
            p.extend(shapes);
        }
        // applied (solid) lines
        for c in 0..4 {
            if let Some(si) = self.applied_src[c] {
                p.line_segment(
                    [Pos2::new(src_x, sy(si)), Pos2::new(dac_x, dy(c))],
                    Stroke::new(2.6, ch_color(c)),
                );
            }
        }
        let live: std::collections::HashSet<usize> =
            self.applied_src.iter().flatten().cloned().collect();
        for i in 0..n {
            let y = sy(i);
            let on = live.contains(&i);
            let tcol = if on {
                Color32::from_rgb(0xe6, 0xee, 0xf6)
            } else {
                Color32::from_rgb(0x5f, 0x71, 0x85)
            };
            p.text(
                Pos2::new(src_x - 8.0, y),
                egui::Align2::RIGHT_CENTER,
                dsp::source_label(i),
                fid.clone(),
                tcol,
            );
            p.circle_filled(
                Pos2::new(src_x, y),
                3.4,
                if on {
                    Color32::from_rgb(0x4F, 0xC3, 0xF7)
                } else {
                    Color32::from_rgb(0x33, 0x41, 0x4d)
                },
            );
        }
        for c in 0..4 {
            let y = dy(c);
            p.circle_filled(Pos2::new(dac_x, y), 5.0, ch_color(c));
            p.text(
                Pos2::new(dac_x + 10.0, y),
                egui::Align2::LEFT_CENTER,
                format!("DAC{c}"),
                fid.clone(),
                Color32::from_rgb(0xe6, 0xee, 0xf6),
            );
        }
    }

    fn tab_neuron(&mut self, ui: &mut egui::Ui) {
        ui.group(|ui| {
            ui.label(egui::RichText::new("Neuron timing (all)").strong());
            ui.horizontal(|ui| {
                ui.label("integration dt");
                egui::ComboBox::from_id_salt("dt")
                    .selected_text(DT_OPTIONS[self.dt_idx].0)
                    .show_ui(ui, |ui| {
                        for (i, (lbl, _)) in DT_OPTIONS.iter().enumerate() {
                            ui.selectable_value(&mut self.dt_idx, i, *lbl);
                        }
                    });
            });
            ui.horizontal(|ui| {
                ui.label("update period");
                ui.add(
                    egui::DragValue::new(&mut self.neuron_period)
                        .range(1..=0xFF_FFFF)
                        .suffix(" clocks"),
                );
                if ui.button("Apply timing").clicked() && self.connected {
                    self.send(Cmd::SetNeuronTiming {
                        dt: DT_OPTIONS[self.dt_idx].1,
                        period: self.neuron_period,
                    });
                }
            });
            ui.label(
                egui::RichText::new(
                    "Triggered spiking: period 1 is recommended. The power-on period is 256 clocks (5.12 µs), so a short current pulse can otherwise be missed.",
                )
                .small()
                .color(Color32::GRAY),
            );
        });

        ui.group(|ui| {
            ui.label(egui::RichText::new("Per-neuron profiles").strong());
            let names = self.profile_names();
            for n in 0..4 {
                ui.horizontal(|ui| {
                    ui.label(format!("neuron {n}"));
                    egui::ComboBox::from_id_salt(format!("nprof{n}"))
                        .selected_text(
                            names
                                .get(self.neuron_prof_idx[n])
                                .cloned()
                                .unwrap_or_default(),
                        )
                        .show_ui(ui, |ui| {
                            for (i, nm) in names.iter().enumerate() {
                                ui.selectable_value(&mut self.neuron_prof_idx[n], i, nm);
                            }
                        });
                    if ui.button("Program").clicked() && self.connected {
                        let prof = names
                            .get(self.neuron_prof_idx[n])
                            .cloned()
                            .unwrap_or_default();
                        self.send_profile(n.to_string(), &prof);
                    }
                    if let Some(r) = &self.neuron_running[n] {
                        ui.colored_label(Color32::from_rgb(0x81, 0xC7, 0x84), format!("▶ {r}"));
                    }
                });
            }
        });

        ui.group(|ui| {
            ui.label(egui::RichText::new("Neuron params").strong());
            ui.horizontal(|ui| {
                ui.label("load");
                let names = self.profile_names();
                let mut items = vec!["load profile…".to_string()];
                items.extend(names.clone());
                egui::ComboBox::from_id_salt("loadprof")
                    .selected_text(items.get(self.load_prof_idx).cloned().unwrap_or_default())
                    .show_ui(ui, |ui| {
                        for (i, nm) in items.iter().enumerate() {
                            if ui
                                .selectable_value(&mut self.load_prof_idx, i, nm)
                                .clicked()
                                && i > 0
                            {
                                self.load_profile_values(&items[i]);
                            }
                        }
                    });
            });
            for (i, (_, label, lo, hi, _, dec)) in dsp::NEURON_PARAMS.iter().enumerate() {
                ui.horizontal(|ui| {
                    ui.label(*label);
                    let speed = 10f64.powi(-(*dec as i32));
                    ui.add(
                        egui::DragValue::new(&mut self.np_values[i])
                            .range(*lo..=*hi)
                            .speed(speed)
                            .max_decimals(*dec),
                    );
                });
            }
            ui.horizontal(|ui| {
                ui.label("program →");
                for tgt in ["0", "1", "2", "3", "all"] {
                    if ui.button(tgt).clicked() && self.connected {
                        self.send_params(tgt);
                    }
                }
            });
            ui.horizontal(|ui| {
                ui.label("save as");
                ui.add(
                    egui::TextEdit::singleline(&mut self.save_name)
                        .desired_width(120.0)
                        .hint_text("name"),
                );
                if ui.button("Save…").clicked() {
                    self.save_current_profile();
                }
            });
            ui.label(&self.neuron_status);
        });
    }

    fn tab_waveforms(&mut self, ui: &mut egui::Ui) {
        let current_kind = dsp::CURRENT_PRESETS[self.current_kind];
        if current_kind == "Step" {
            self.current_step_zero = self.current_step_zero.min(dsp::CURRENT_WAVE_MAX - 1);
            self.current_step_high = self.current_step_high.clamp(
                1,
                dsp::CURRENT_WAVE_MAX.saturating_sub(self.current_step_zero),
            );
        }
        let preview = dsp::gen_current_wave(dsp::CurrentWaveConfig {
            kind: current_kind,
            amplitude_ma: self.current_amp_ma,
            frequency_hz: self.current_freq_hz,
            duty_percent: self.current_duty,
            step_zero: self.current_step_zero,
            step_high: self.current_step_high,
            step_cps: self.current_step_cps,
            step_loop: self.current_step_loop,
        });
        ui.group(|ui| {
            ui.horizontal(|ui| {
                ui.label(egui::RichText::new("Current player").strong());
                let state = if self.current_running {
                    "RUNNING"
                } else {
                    "STOPPED"
                };
                ui.colored_label(
                    if self.current_running {
                        Color32::LIGHT_GREEN
                    } else {
                        Color32::GRAY
                    },
                    state,
                );
            });
            egui::Grid::new("current_player_controls")
                .num_columns(2)
                .show(ui, |ui| {
                    ui.label("profile");
                    egui::ComboBox::from_id_salt("current_profile")
                        .selected_text(current_kind)
                        .show_ui(ui, |ui| {
                            for (index, name) in dsp::CURRENT_PRESETS.iter().enumerate() {
                                ui.selectable_value(&mut self.current_kind, index, *name);
                            }
                        });
                    ui.end_row();

                    ui.label("amplitude");
                    ui.add(
                        egui::DragValue::new(&mut self.current_amp_ma)
                            .range(0.0..=dsp::CURRENT_MAX_MA)
                            .speed(0.1)
                            .suffix(" mA"),
                    );
                    ui.end_row();

                    ui.label("DAC mirror gain");
                    ui.add(
                        egui::DragValue::new(&mut self.current_gain)
                            .range(0.0..=dsp::CURRENT_GAIN_MAX)
                            .speed(0.25)
                            .suffix("x"),
                    );
                    ui.end_row();

                    if matches!(current_kind, "Sine" | "Square") {
                        ui.label("frequency");
                        ui.add(
                            egui::DragValue::new(&mut self.current_freq_hz)
                                .range(1.0..=3_125_000.0)
                                .speed(100.0)
                                .suffix(" Hz"),
                        );
                        ui.end_row();
                    }
                    if current_kind == "Square" {
                        ui.label("duty");
                        ui.add(
                            egui::DragValue::new(&mut self.current_duty)
                                .range(0.1..=99.9)
                                .speed(0.5)
                                .suffix("%"),
                        );
                        ui.end_row();
                    }
                    if current_kind == "Step" {
                        ui.label("zero samples");
                        ui.add(
                            egui::DragValue::new(&mut self.current_step_zero)
                                .range(0..=dsp::CURRENT_WAVE_MAX - 1),
                        );
                        ui.end_row();
                        self.current_step_high = self.current_step_high.clamp(
                            1,
                            dsp::CURRENT_WAVE_MAX.saturating_sub(self.current_step_zero),
                        );
                        ui.label("high samples");
                        ui.add(egui::DragValue::new(&mut self.current_step_high).range(
                            1..=dsp::CURRENT_WAVE_MAX.saturating_sub(self.current_step_zero),
                        ));
                        ui.end_row();
                        ui.label("cycles/sample");
                        ui.add(
                            egui::DragValue::new(&mut self.current_step_cps)
                                .range(1..=u16::MAX as u32),
                        );
                        ui.end_row();
                        ui.label("mode");
                        ui.checkbox(&mut self.current_step_loop, "Loop");
                        ui.end_row();
                    }
                });

            let points: Vec<[f64; 2]> = preview
                .samples_ma
                .iter()
                .enumerate()
                .map(|(index, &sample)| [index as f64, sample])
                .collect();
            Plot::new("current_player_preview")
                .height(120.0)
                .allow_drag(false)
                .allow_zoom(false)
                .allow_scroll(false)
                .include_y(0.0)
                .include_y(self.current_amp_ma.max(1.0))
                .show(ui, |plot_ui| {
                    plot_ui.line(Line::new(PlotPoints::from(points)).color(Color32::LIGHT_GREEN));
                });

            ui.horizontal(|ui| {
                let enabled = self.connected && !self.busy;
                if ui
                    .add_enabled(enabled, egui::Button::new("Program / restart"))
                    .clicked()
                {
                    self.busy = true;
                    let description = if preview.actual_hz > 0.0 {
                        format!(
                            "{} {:.3} Hz, {} samples, cps {}",
                            current_kind,
                            preview.actual_hz,
                            preview.samples_ma.len(),
                            preview.cps
                        )
                    } else {
                        format!(
                            "{}: {} samples, cps {}",
                            current_kind,
                            preview.samples_ma.len(),
                            preview.cps
                        )
                    };
                    if current_kind == "Step" {
                        self.send(Cmd::ProgramCurrentStep {
                            cps: preview.cps,
                            zero_count: self.current_step_zero as u32,
                            high_count: self.current_step_high as u32,
                            amp_q16: dsp::current_ma_to_q16(self.current_amp_ma),
                            looped: self.current_step_loop,
                            gain_q8_8: dsp::current_gain_to_q8_8(self.current_gain),
                            description,
                        });
                    } else {
                        self.send(Cmd::ProgramCurrentWave {
                            samples_q16: preview
                                .samples_ma
                                .iter()
                                .map(|&sample| dsp::current_ma_to_q16(sample))
                                .collect(),
                            cps: preview.cps,
                            hold: false,
                            gain_q8_8: dsp::current_gain_to_q8_8(self.current_gain),
                            description,
                        });
                    }
                }
                if ui.add_enabled(enabled, egui::Button::new("Stop")).clicked() {
                    self.busy = true;
                    self.send(Cmd::StopCurrent);
                }
                if ui
                    .add_enabled(enabled, egui::Button::new("Read hardware state"))
                    .clicked()
                {
                    self.busy = true;
                    self.send(Cmd::ReadCurrent);
                }
            });
            if self.live_requested {
                ui.label(
                    egui::RichText::new(
                        "Programming is queued between completed trigger batches; the rolling average restarts with the new settings.",
                    )
                    .small()
                    .color(Color32::LIGHT_BLUE),
                );
            }
            ui.label(&self.current_status);
        });
        ui.group(|ui| {
            ui.label(egui::RichText::new("DDS tone").strong());
            ui.horizontal(|ui| {
                ui.add(
                    egui::DragValue::new(&mut self.dds_freq_mhz)
                        .range(0.0..=500.0)
                        .speed(0.5)
                        .suffix(" MHz"),
                );
                if ui.button("Set DDS").clicked() && self.connected {
                    let inc = dsp::dds_freq_to_inc(self.dds_freq_mhz * 1e6);
                    self.send(Cmd::SetDds(inc));
                    self.status = format!(
                        "DDS {:.3} MHz (inc 0x{inc:06X})",
                        dsp::dds_inc_to_freq(inc) / 1e6
                    );
                }
            });
        });
        ui.group(|ui| {
            ui.label(egui::RichText::new("BRAM waveform").strong());
            egui::Grid::new("wf").num_columns(2).show(ui, |ui| {
                ui.label("target");
                egui::ComboBox::from_id_salt("wfch")
                    .selected_text(if self.wf_ch == 4 {
                        "all".to_string()
                    } else {
                        format!("ch{}", self.wf_ch)
                    })
                    .show_ui(ui, |ui| {
                        for c in 0..4 {
                            ui.selectable_value(&mut self.wf_ch, c, format!("ch{c}"));
                        }
                        ui.selectable_value(&mut self.wf_ch, 4, "all");
                    });
                ui.end_row();
                ui.label("shape");
                egui::ComboBox::from_id_salt("wfkind")
                    .selected_text(dsp::WAVEFORMS[self.wf_kind])
                    .show_ui(ui, |ui| {
                        for (i, k) in dsp::WAVEFORMS.iter().enumerate() {
                            ui.selectable_value(&mut self.wf_kind, i, *k);
                        }
                    });
                ui.end_row();
                ui.label("period");
                ui.add(
                    egui::DragValue::new(&mut self.wf_period)
                        .range(2..=dsp::PROGRAM_SAMPLES as i32)
                        .suffix(" ns"),
                );
                ui.end_row();
                ui.label("width");
                ui.add(
                    egui::DragValue::new(&mut self.wf_width)
                        .range(1..=dsp::PROGRAM_SAMPLES as i32)
                        .suffix(" ns"),
                );
                ui.end_row();
                let vmax = dsp::DAC_FULLSCALE as f64 * dsp::VOLTS_PER_COUNT;
                ui.label("V min");
                ui.add(
                    egui::DragValue::new(&mut self.wf_vlo)
                        .range(-vmax..=vmax)
                        .speed(0.01)
                        .suffix(" V"),
                );
                ui.end_row();
                ui.label("V max");
                ui.add(
                    egui::DragValue::new(&mut self.wf_vhi)
                        .range(-vmax..=vmax)
                        .speed(0.01)
                        .suffix(" V"),
                );
                ui.end_row();
            });
            if ui.button("Program BRAM").clicked() && self.connected {
                let (words, frames) = dsp::gen_waveform(
                    dsp::WAVEFORMS[self.wf_kind],
                    self.wf_period as usize,
                    self.wf_width as usize,
                    self.wf_vlo,
                    self.wf_vhi,
                );
                let chans: Vec<u8> = if self.wf_ch == 4 {
                    vec![0, 1, 2, 3]
                } else {
                    vec![self.wf_ch as u8]
                };
                self.status = format!(
                    "programming {} to {:?}",
                    dsp::WAVEFORMS[self.wf_kind],
                    chans
                );
                self.send(Cmd::ProgramBram {
                    chans,
                    words,
                    loop_frames: frames,
                });
            }
        });
    }

    fn tab_display(&mut self, ui: &mut egui::Ui) {
        ui.group(|ui| {
            ui.label(egui::RichText::new("Display").strong());
            if ui
                .checkbox(&mut self.deinterleave, "Legacy mod-4 baseline removal")
                .changed()
            {
                self.display_dirty = true;
            }
            if ui.button("Autoscale once").clicked() {
                self.autoscale_once();
                self.plot_view_revision = self.plot_view_revision.wrapping_add(1);
            }
            if ui
                .checkbox(&mut self.auto_y, "Continuously autoscale new data")
                .changed()
                && self.auto_y
            {
                self.autoscale_once();
                self.plot_view_revision = self.plot_view_revision.wrapping_add(1);
            }
            ui.horizontal(|ui| {
                if ui.selectable_label(!self.fft_view, "Time").clicked() {
                    self.fft_view = false;
                    self.display_dirty = true;
                    self.plot_view_revision = self.plot_view_revision.wrapping_add(1);
                }
                if ui.selectable_label(self.fft_view, "FFT").clicked() {
                    self.fft_view = true;
                    self.display_dirty = true;
                    self.plot_view_revision = self.plot_view_revision.wrapping_add(1);
                }
            });
            let ranges = if self.fft_view {
                &mut self.fft_y_ranges
            } else {
                &mut self.time_y_ranges
            };
            let step = if self.fft_view { 5.0 } else { 0.05 };
            let mut range_changed = false;
            egui::Grid::new("fixed_y_ranges")
                .num_columns(3)
                .show(ui, |ui| {
                    ui.label("channel");
                    ui.label("Y min");
                    ui.label("Y max");
                    ui.end_row();
                    for (channel, range) in ranges.iter_mut().enumerate() {
                        ui.label(format!("ADC{channel}"));
                        range_changed |= ui
                            .add(egui::DragValue::new(&mut range[0]).speed(step))
                            .changed();
                        range_changed |= ui
                            .add(egui::DragValue::new(&mut range[1]).speed(step))
                            .changed();
                        if range[0] >= range[1] {
                            range[1] = range[0] + step;
                        }
                        ui.end_row();
                    }
                });
            if range_changed {
                self.auto_y = false;
                self.plot_view_revision = self.plot_view_revision.wrapping_add(1);
            }
            ui.horizontal(|ui| {
                let cic = ui.button("CIC on");
                if cic.clicked() && self.connected {
                    self.send(Cmd::SetCic(true));
                }
                if ui.button("CIC off").clicked() && self.connected {
                    self.send(Cmd::SetCic(false));
                }
            });
        });
        ui.group(|ui| {
            ui.label(egui::RichText::new("Live stream (UDP)").strong());
            ui.horizontal(|ui| {
                ui.label("decim");
                ui.add(
                    egui::DragValue::new(&mut self.stream_decim)
                        .range(4..=65532)
                        .speed(4),
                );
                ui.checkbox(&mut self.stream_cic, "CIC");
            });
            ui.horizontal(|ui| {
                if ui.button("Start stream").clicked() && self.connected {
                    self.send(Cmd::StartStream {
                        decim: (self.stream_decim as u32 / 4) * 4,
                        cic: self.stream_cic,
                    });
                }
                if ui.button("Stop stream").clicked() && self.connected {
                    self.send(Cmd::StopStream);
                }
            });
            ui.label(
                egui::RichText::new(
                    "(continuous stream issues STRM; use Auto-Sample for the live plot)",
                )
                .size(10.0)
                .color(Color32::GRAY),
            );
        });
        ui.group(|ui| {
            ui.label(egui::RichText::new("Raw firmware command").strong());
            ui.horizontal(|ui| {
                let resp = ui.add(
                    egui::TextEdit::singleline(&mut self.raw_cmd)
                        .desired_width(220.0)
                        .hint_text("e.g. NSRC 0 dds"),
                );
                let go = ui.button("Send").clicked()
                    || (resp.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)));
                if go && self.connected && !self.raw_cmd.trim().is_empty() {
                    self.send(Cmd::Raw(self.raw_cmd.trim().to_string()));
                }
            });
            if !self.stat_raw.is_empty() {
                egui::CollapsingHeader::new("last STAT output").show(ui, |ui| {
                    egui::ScrollArea::vertical()
                        .max_height(160.0)
                        .show(ui, |ui| {
                            ui.add(egui::Label::new(
                                egui::RichText::new(&self.stat_raw).monospace().size(10.0),
                            ));
                        });
                });
            }
        });
    }

    // --------------------------------------------------------------- helpers
    fn send_profile(&mut self, target: String, profile: &str) {
        if dsp::BUILTIN_PROFILES.contains(&profile) {
            self.send(Cmd::ProgramNeuron {
                target,
                profile: Some(profile.to_string()),
                params: vec![],
                profile_label: profile.to_string(),
            });
        } else if let Some(cp) = self.custom.iter().find(|c| c.name == profile) {
            let params = custom_params(cp);
            self.send(Cmd::ProgramNeuron {
                target,
                profile: None,
                params,
                profile_label: profile.to_string(),
            });
        }
    }

    fn send_params(&mut self, target: &str) {
        let params: Vec<(String, u32)> = dsp::NEURON_PARAMS
            .iter()
            .enumerate()
            .map(|(i, (name, ..))| (name.to_string(), dsp::izh_to_q16(self.np_values[i])))
            .collect();
        self.neuron_status = format!("programming neuron {target}…");
        self.send(Cmd::ProgramNeuron {
            target: target.to_string(),
            profile: None,
            params,
            profile_label: "custom".to_string(),
        });
    }

    fn load_profile_values(&mut self, name: &str) {
        if let Some(v) = dsp::builtin_profile_values(name) {
            for (i, (_, val)) in v.iter().enumerate() {
                self.np_values[i] = *val;
            }
        } else if let Some(cp) = self.custom.iter().find(|c| c.name == name) {
            self.np_values = [cp.a, cp.b, cp.c, cp.d, cp.iconst];
        }
        self.load_prof_idx = 0;
        self.status = format!("staged profile '{name}' — press a Program button");
    }

    fn save_current_profile(&mut self) {
        let name = self.save_name.trim().to_string();
        if name.is_empty() || dsp::BUILTIN_PROFILES.contains(&name.as_str()) {
            self.neuron_status = "pick a non-empty, non-builtin name".into();
            return;
        }
        let cp = CustomProfile {
            name: name.clone(),
            a: self.np_values[0],
            b: self.np_values[1],
            c: self.np_values[2],
            d: self.np_values[3],
            iconst: self.np_values[4],
        };
        if let Some(e) = self.custom.iter_mut().find(|c| c.name == name) {
            *e = cp;
        } else {
            self.custom.push(cp);
        }
        save_custom(&self.custom);
        self.save_name.clear();
        self.neuron_status = format!("saved profile '{name}'");
    }
}

impl eframe::App for DaqApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.drain_events();
        self.poll_live_snapshot();

        // Legacy one-shot auto-sample cadence. Continuous trigger averaging is
        // paced entirely by the acquisition thread and never by this UI loop.
        if self.auto_sample
            && !self.live_requested
            && self.connected
            && !self.busy
            && self.last_auto.elapsed().as_millis() >= 1000
        {
            self.last_auto = Instant::now();
            self.busy = true;
            self.send(Cmd::CollectEth {
                bytes: 64 * 1024,
                save: false,
            });
        }
        if self.live_requested {
            // The acquisition thread publishes latest-only state. The viewer
            // samples it periodically, so rendering cannot throttle capture.
            ctx.request_repaint_after(std::time::Duration::from_millis(33));
        } else if self.auto_sample {
            ctx.request_repaint_after(std::time::Duration::from_millis(200));
        }

        if self.display_dirty {
            self.rebuild_display();
            if self.auto_y {
                self.autoscale_once();
                self.plot_view_revision = self.plot_view_revision.wrapping_add(1);
            }
        }

        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.add_space(4.0);
            self.connection_bar(ui);
            ui.add_space(4.0);
        });

        egui::SidePanel::right("controls")
            .resizable(false)
            .exact_width(440.0)
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.selectable_value(&mut self.tab, Tab::Neuron, "Neuron");
                    ui.selectable_value(&mut self.tab, Tab::Xbar, "XBAR");
                    ui.selectable_value(&mut self.tab, Tab::Capture, "Display");
                    ui.selectable_value(&mut self.tab, Tab::Waveforms, "Waveforms");
                });
                ui.separator();
                egui::ScrollArea::vertical()
                    .max_height(ui.available_height() - 190.0)
                    .show(ui, |ui| match self.tab {
                        Tab::Neuron => self.tab_neuron(ui),
                        Tab::Xbar => self.tab_xbar(ui),
                        Tab::Capture => self.tab_display(ui),
                        Tab::Waveforms => self.tab_waveforms(ui),
                    });
                ui.separator();
                self.capture_bar(ui);
                ui.add_space(4.0);
                ui.label(
                    egui::RichText::new(&self.status)
                        .size(11.0)
                        .color(Color32::from_rgb(0x9f, 0xb3, 0xc8)),
                );
            });

        egui::CentralPanel::default().show(ctx, |ui| {
            let n = self.series.iter().map(|s| s.len()).max().unwrap_or(0);
            if n == 0 {
                ui.centered_and_justified(|ui| {
                    ui.label("No data yet — Connect, then Collect Ethernet / UART Capture / Auto-Sample.");
                });
                return;
            }
            let mut common_x = [f64::INFINITY, f64::NEG_INFINITY];
            for point in self.series.iter().flatten() {
                if point[0].is_finite() {
                    common_x[0] = common_x[0].min(point[0]);
                    common_x[1] = common_x[1].max(point[0]);
                }
            }
            if !common_x[0].is_finite() || !common_x[1].is_finite() {
                common_x = [0.0, 1.0];
            } else if common_x[0] == common_x[1] {
                common_x[1] = common_x[0] + 1.0 / self.fs;
            }
            let h = (ui.available_height() - 8.0) / 4.0;
            for ch in 0..4 {
                let (xlabel, ylabel) = if self.fft_view { ("Hz", "dBFS") } else { ("s", "V") };
                let pts = PlotPoints::from(self.series[ch].clone());
                let line = Line::new(pts).color(ch_color(ch)).name(format!("ch{ch}"));
                let range = if self.fft_view {
                    self.fft_y_ranges[ch]
                } else {
                    self.time_y_ranges[ch]
                };
                let plot_id = ("adc_plot", ch, self.plot_view_revision, self.fft_view);
                let x_link_id =
                    egui::Id::new(("adc_plot_x", self.plot_view_revision, self.fft_view));
                let plot = Plot::new(plot_id)
                    .height(h)
                    .x_axis_label(xlabel)
                    .y_axis_label(format!("ch{ch} [{ylabel}]"))
                    .allow_scroll(false)
                    .auto_bounds(egui::Vec2b::new(true, false))
                    .include_x(common_x[0])
                    .include_x(common_x[1])
                    .include_y(range[0])
                    .include_y(range[1])
                    .link_axis(x_link_id, true, false)
                    .link_cursor(x_link_id, true, false);
                plot.show(ui, |plot_ui| plot_ui.line(line));
            }
        });
    }
}

impl Drop for DaqApp {
    fn drop(&mut self) {
        let _ = self.tx.send(Cmd::Shutdown);
    }
}

// ------------------------------------------------------------------- support
fn list_ports() -> Vec<String> {
    match serialport::available_ports() {
        Ok(mut ports) => {
            ports.sort_by_key(|port| match port.port_type {
                serialport::SerialPortType::UsbPort(_) => 0,
                serialport::SerialPortType::PciPort => 1,
                serialport::SerialPortType::Unknown => 2,
                serialport::SerialPortType::BluetoothPort => 3,
            });
            let names: Vec<String> = ports.into_iter().map(|port| port.port_name).collect();
            if names.is_empty() {
                vec!["COM10".to_string()]
            } else {
                names
            }
        }
        Err(_) => vec!["COM10".to_string()],
    }
}
fn custom_params(cp: &CustomProfile) -> Vec<(String, u32)> {
    vec![
        ("a".into(), dsp::izh_to_q16(cp.a)),
        ("b".into(), dsp::izh_to_q16(cp.b)),
        ("c".into(), dsp::izh_to_q16(cp.c)),
        ("d".into(), dsp::izh_to_q16(cp.d)),
        ("iconst".into(), dsp::izh_to_q16(cp.iconst)),
    ]
}

fn profiles_path() -> std::path::PathBuf {
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".into());
    std::path::Path::new(&home).join(".daq_neuron_profiles.json")
}

fn load_custom() -> Vec<CustomProfile> {
    std::fs::read_to_string(profiles_path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_custom(v: &[CustomProfile]) {
    if let Ok(s) = serde_json::to_string_pretty(v) {
        let _ = std::fs::write(profiles_path(), s);
    }
}
