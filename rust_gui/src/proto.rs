//! Serial worker thread + command/event channels. All board UART/UDP I/O runs
//! here so the egui UI thread never blocks. Mirrors DacControl + burst flow.

use std::io::{Read, Write};
use std::sync::{
    mpsc::{Receiver, Sender, TryRecvError},
    Arc, RwLock,
};
use std::time::{Duration, Instant};

use crate::burst_async::{decode_chip, decode_pcap, parse_brdo_request, Reassembler};
use crate::dsp;
use crate::rolling::{Capture, RollingAverage};

const NEURON_RUNTIME_DT: u32 = 0x0000_8000; // 0.5 Q16.16
const NEURON_RUNTIME_PERIOD: u32 = 1;

#[derive(Clone)]
pub struct BoardCfg {
    pub board_ip: String,
    pub cmd_port: u16,
    pub local_ip: String,
    pub local_port: u16,
    pub capture_dir: String,
}

#[derive(Clone, Default)]
pub struct LiveSnapshot {
    pub sequence: u64,
    pub running: bool,
    pub average: [Vec<f32>; 4],
    pub held: usize,
    pub total: u64,
    pub samples_per_rep: usize,
    pub capture_hz: f64,
    pub batch_hz: f64,
    pub coverage: f32,
    pub drain_attempts: u32,
    pub failures: u64,
    pub last_error: String,
}

pub type LiveShared = Arc<RwLock<LiveSnapshot>>;

#[derive(Clone, Copy)]
struct LiveConfig {
    bytes: usize,
    reps_per_batch: usize,
    window: usize,
}

impl Default for BoardCfg {
    fn default() -> Self {
        Self {
            board_ip: "192.168.2.10".into(),
            cmd_port: 5006,
            local_ip: "192.168.2.1".into(),
            local_port: 5005,
            capture_dir: "captures".into(),
        }
    }
}

/// UI -> worker.
pub enum Cmd {
    Connect(String),
    Disconnect,
    Raw(String),
    Stat,
    /// Commit only a crossbar route. Neuron programming is intentionally
    /// independent and lives in the Neuron tab.
    ApplyRoute {
        ch: u8,
        src_idx: usize,
    },
    ReadRoutes,
    SetDds(u32),
    ProgramNeuron {
        target: String,
        profile: Option<String>,
        params: Vec<(String, u32)>,
        profile_label: String,
    },
    SetNeuronTiming {
        dt: u32,
        period: u32,
    },
    SetCic(bool),
    StartStream {
        decim: u32,
        cic: bool,
    },
    StopStream,
    UartCapture(u32),
    CollectEth {
        bytes: usize,
        save: bool,
    },
    StartLiveAverage {
        bytes: usize,
        reps_per_batch: usize,
        window: usize,
    },
    StopLiveAverage,
    ProgramCurrentWave {
        samples_q16: Vec<u32>,
        cps: u32,
        hold: bool,
        gain_q8_8: u16,
        description: String,
    },
    ProgramCurrentStep {
        cps: u32,
        zero_count: u32,
        high_count: u32,
        amp_q16: u32,
        looped: bool,
        gain_q8_8: u16,
        description: String,
    },
    StopCurrent,
    ReadCurrent,
    ProgramBram {
        chans: Vec<u8>,
        words: Vec<u32>,
        loop_frames: u32,
    },
    Shutdown,
}

/// worker -> UI.
pub enum Evt {
    Connected(Result<String, String>),
    Disconnected,
    Reply(String),
    Stat {
        ok: bool,
        health: String,
        raw: String,
    },
    Capture {
        kind: String,
        chans: [Vec<i16>; 4],
        cov: f32,
        tries: u32,
        saved: Option<String>,
    },
    RouteDone {
        ch: u8,
        ok: bool,
        src_idx: usize,
        detail: String,
    },
    RoutesRead {
        routes: [Option<usize>; 4],
        detail: String,
    },
    NeuronDone {
        target: String,
        ok: bool,
        profile: String,
    },
    CurrentDone {
        ok: bool,
        running: bool,
        status: String,
    },
    Status(String),
    Error(String),
}

/// A serial port plus a line-oriented read buffer.
struct Link {
    port: Box<dyn serialport::SerialPort>,
    rx: Vec<u8>,
}

impl Link {
    fn open(name: &str) -> Result<Self, String> {
        let port = serialport::new(name, 115200)
            .timeout(Duration::from_millis(120))
            .open()
            .map_err(|e| e.to_string())?;
        std::thread::sleep(Duration::from_millis(200));
        let mut me = Self {
            port,
            rx: Vec::new(),
        };
        me.flush_input();
        Ok(me)
    }

    fn flush_input(&mut self) {
        let _ = self.port.clear(serialport::ClearBuffer::Input);
        self.rx.clear();
    }

    fn send_line(&mut self, cmd: &str) {
        self.flush_input();
        let _ = self.port.write_all(cmd.as_bytes());
        let _ = self.port.write_all(b"\n");
        let _ = self.port.flush();
    }

    /// Pull whatever bytes are available (non-fatal on timeout) into `rx`.
    fn pump(&mut self) {
        let mut b = [0u8; 4096];
        match self.port.read(&mut b) {
            Ok(n) if n > 0 => self.rx.extend_from_slice(&b[..n]),
            _ => {}
        }
    }

    fn take_line(&mut self) -> Option<String> {
        if let Some(pos) = self.rx.iter().position(|&c| c == b'\n') {
            let line: Vec<u8> = self.rx.drain(..=pos).collect();
            let s = String::from_utf8_lossy(&line);
            Some(s.trim_end_matches(['\r', '\n']).to_string())
        } else {
            None
        }
    }

    /// Read lines until one starts with any prefix (or "ERR"), or timeout.
    fn read_until(&mut self, prefixes: &[&str], timeout: Duration) -> Option<String> {
        let deadline = Instant::now() + timeout;
        loop {
            while let Some(line) = self.take_line() {
                if line.is_empty() {
                    continue;
                }
                if line.starts_with("ERR") || prefixes.iter().any(|p| line.starts_with(p)) {
                    return Some(line);
                }
            }
            if Instant::now() >= deadline {
                return None;
            }
            self.pump();
        }
    }

    fn cmd(&mut self, cmd: &str, prefixes: &[&str], timeout: Duration) -> String {
        self.send_line(cmd);
        self.read_until(prefixes, timeout).unwrap_or_default()
    }

    /// Send a binary capture command, sync on 0xFE10CAFE, read `need` bytes.
    fn capture_bin(&mut self, cmd: &str, need: usize, timeout: Duration) -> Option<Vec<u8>> {
        const SYNC: [u8; 4] = [0xFE, 0x10, 0xCA, 0xFE];
        self.send_line(cmd);
        let deadline = Instant::now() + timeout;
        // Seed with anything already buffered. Keep the bytes following the
        // sync in this same chunk: on Windows the sync and much of the payload
        // commonly arrive in one ReadFile completion.
        let mut stream = std::mem::take(&mut self.rx);
        loop {
            if let Some(payload_start) = capture_payload_start(&stream, &SYNC) {
                let initial = stream.split_off(payload_start);
                return self.read_exact(initial, need, deadline);
            }
            if Instant::now() >= deadline {
                return None;
            }
            // Retain the last three bytes so a sync split across serial reads
            // remains detectable, while discarding arbitrary textual chatter.
            if stream.len() > SYNC.len() - 1 {
                let keep_from = stream.len() - (SYNC.len() - 1);
                stream.drain(..keep_from);
            }
            let mut b = [0u8; 4096];
            if let Ok(n) = self.port.read(&mut b) {
                stream.extend_from_slice(&b[..n]);
            }
        }
    }

    fn read_exact(&mut self, mut data: Vec<u8>, need: usize, deadline: Instant) -> Option<Vec<u8>> {
        if data.len() >= need {
            let trailing = data.split_off(need);
            self.rx.extend_from_slice(&trailing);
            return Some(data);
        }
        while data.len() < need {
            if Instant::now() >= deadline {
                return None;
            }
            let mut b = [0u8; 8192];
            match self.port.read(&mut b) {
                Ok(n) if n > 0 => {
                    let take = (need - data.len()).min(n);
                    data.extend_from_slice(&b[..take]);
                    if take < n {
                        self.rx.extend_from_slice(&b[take..n]);
                    }
                }
                _ => {}
            }
        }
        Some(data)
    }
}

fn capture_payload_start(stream: &[u8], sync: &[u8; 4]) -> Option<usize> {
    stream
        .windows(sync.len())
        .position(|window| window == sync)
        .map(|offset| offset + sync.len())
}

pub fn spawn(ctx: egui::Context, cfg: BoardCfg) -> (Sender<Cmd>, Receiver<Evt>, LiveShared) {
    let (ctx_tx, cmd_rx) = std::sync::mpsc::channel::<Cmd>();
    let (evt_tx, evt_rx) = std::sync::mpsc::channel::<Evt>();
    let live = Arc::new(RwLock::new(LiveSnapshot::default()));
    let worker_live = Arc::clone(&live);
    std::thread::spawn(move || worker(ctx, cfg, cmd_rx, evt_tx, worker_live));
    (ctx_tx, evt_rx, live)
}

fn worker(
    ctx: egui::Context,
    cfg: BoardCfg,
    rx: Receiver<Cmd>,
    tx: Sender<Evt>,
    shared: LiveShared,
) {
    let mut link: Option<Link> = None;
    let mut live_cfg: Option<LiveConfig> = None;
    let mut rolling: Option<RollingAverage> = None;
    let mut live_started = Instant::now();
    let mut batches = 0u64;
    let emit = |tx: &Sender<Evt>, ctx: &egui::Context, event: Evt| {
        let _ = tx.send(event);
        ctx.request_repaint();
    };

    loop {
        let command = if live_cfg.is_some() && link.is_some() {
            match rx.try_recv() {
                Ok(command) => Some(command),
                Err(TryRecvError::Empty) => None,
                Err(TryRecvError::Disconnected) => break,
            }
        } else {
            match rx.recv() {
                Ok(command) => Some(command),
                Err(_) => break,
            }
        };

        if let Some(command) = command {
            match command {
                Cmd::Shutdown => break,
                Cmd::Connect(port) => {
                    live_cfg = None;
                    rolling = None;
                    if let Ok(mut snapshot) = shared.write() {
                        snapshot.running = false;
                        snapshot.sequence += 1;
                    }
                    match Link::open(&port) {
                        Ok(mut opened) => {
                            let routes = read_routes(&mut opened);
                            let timing = apply_neuron_timing(
                                &mut opened,
                                NEURON_RUNTIME_DT,
                                NEURON_RUNTIME_PERIOD,
                            );
                            let current = read_current_state(&mut opened);
                            link = Some(opened);
                            emit(&tx, &ctx, Evt::Connected(Ok(port)));
                            match routes {
                                Ok((routes, value)) => emit(
                                    &tx,
                                    &ctx,
                                    Evt::RoutesRead {
                                        routes,
                                        detail: format!("reg17=0x{value:04X}"),
                                    },
                                ),
                                Err(detail) => emit(
                                    &tx,
                                    &ctx,
                                    Evt::RoutesRead {
                                        routes: [None; 4],
                                        detail,
                                    },
                                ),
                            }
                            match current {
                                Ok(state) => emit(
                                    &tx,
                                    &ctx,
                                    Evt::CurrentDone {
                                        ok: true,
                                        running: state.running,
                                        status: state.describe(),
                                    },
                                ),
                                Err(detail) => emit(
                                    &tx,
                                    &ctx,
                                    Evt::CurrentDone {
                                        ok: false,
                                        running: false,
                                        status: detail,
                                    },
                                ),
                            }
                            match timing {
                                Ok(detail) => emit(&tx, &ctx, Evt::Status(detail)),
                                Err(detail) => emit(&tx, &ctx, Evt::Error(detail)),
                            }
                        }
                        Err(error) => emit(&tx, &ctx, Evt::Connected(Err(error))),
                    }
                }
                Cmd::Disconnect => {
                    live_cfg = None;
                    rolling = None;
                    if let Ok(mut snapshot) = shared.write() {
                        snapshot.running = false;
                        snapshot.sequence += 1;
                    }
                    link = None;
                    emit(&tx, &ctx, Evt::Disconnected);
                }
                Cmd::StartLiveAverage {
                    bytes,
                    reps_per_batch,
                    window,
                } => {
                    if let Some(opened) = link.as_mut() {
                        opened.cmd("STRM STOP", &["OK STRM", "ERR"], Duration::from_secs(2));
                        let config = LiveConfig {
                            bytes: bytes.max(1024),
                            reps_per_batch: reps_per_batch.clamp(1, 16),
                            window: window.clamp(2, 256),
                        };
                        live_cfg = Some(config);
                        rolling = Some(RollingAverage::new(config.window));
                        live_started = Instant::now();
                        batches = 0;
                        if let Ok(mut snapshot) = shared.write() {
                            *snapshot = LiveSnapshot {
                                sequence: snapshot.sequence + 1,
                                running: true,
                                ..LiveSnapshot::default()
                            };
                        }
                        emit(
                            &tx,
                            &ctx,
                            Evt::Status(format!(
                                "live trigger average started: window {}, {} reps/batch",
                                config.window, config.reps_per_batch
                            )),
                        );
                    } else {
                        emit(&tx, &ctx, Evt::Error("not connected".into()));
                    }
                }
                Cmd::StopLiveAverage => {
                    live_cfg = None;
                    rolling = None;
                    if let Ok(mut snapshot) = shared.write() {
                        snapshot.running = false;
                        snapshot.sequence += 1;
                    }
                    emit(
                        &tx,
                        &ctx,
                        Evt::Status("live trigger average stopped".into()),
                    );
                }
                other => {
                    let live_effect = live_command_effect(&other);
                    if live_cfg.is_some() && live_effect == LiveCommandEffect::Blocked {
                        emit(
                            &tx,
                            &ctx,
                            Evt::Error(
                                "that acquisition command cannot share the board transport with live trigger averaging"
                                    .into(),
                            ),
                        );
                    } else if let Some(opened) = link.as_mut() {
                        if live_cfg.is_some() && live_effect == LiveCommandEffect::ResetAverage {
                            let config = live_cfg.expect("live config");
                            rolling = Some(RollingAverage::new(config.window));
                            live_started = Instant::now();
                            batches = 0;
                            if let Ok(mut snapshot) = shared.write() {
                                *snapshot = LiveSnapshot {
                                    sequence: snapshot.sequence + 1,
                                    running: true,
                                    ..LiveSnapshot::default()
                                };
                            }
                        }
                        handle(opened, &cfg, other, &tx, &ctx);
                    } else {
                        emit(&tx, &ctx, Evt::Error("not connected".into()));
                    }
                }
            }
            continue;
        }

        let config = match live_cfg {
            Some(config) => config,
            None => continue,
        };
        let result = collect_bcpt_batch(
            link.as_mut().expect("live capture requires an open link"),
            &cfg,
            config.bytes,
            config.reps_per_batch,
        );
        match result {
            Ok((captures, coverage, drain_attempts)) => {
                let rolling = rolling
                    .as_mut()
                    .expect("live capture requires a rolling accumulator");
                let mut push_error = None;
                for capture in captures {
                    if let Err(error) = rolling.push(capture) {
                        push_error = Some(error);
                        break;
                    }
                }
                if let Some(error) = push_error {
                    if let Ok(mut snapshot) = shared.write() {
                        snapshot.sequence += 1;
                        snapshot.failures += 1;
                        snapshot.last_error = error;
                    }
                    continue;
                }
                batches += 1;
                let elapsed = live_started.elapsed().as_secs_f64().max(1.0e-9);
                if let Ok(mut snapshot) = shared.write() {
                    snapshot.sequence += 1;
                    snapshot.running = true;
                    snapshot.average = rolling.mean();
                    snapshot.held = rolling.len();
                    snapshot.total = rolling.total_seen();
                    snapshot.samples_per_rep = snapshot.average[0].len();
                    snapshot.capture_hz = snapshot.total as f64 / elapsed;
                    snapshot.batch_hz = batches as f64 / elapsed;
                    snapshot.coverage = coverage;
                    snapshot.drain_attempts = drain_attempts;
                    snapshot.last_error.clear();
                }
            }
            Err(error) => {
                if let Ok(mut snapshot) = shared.write() {
                    snapshot.sequence += 1;
                    snapshot.failures += 1;
                    snapshot.last_error = error;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum LiveCommandEffect {
    Blocked,
    ReadOnly,
    ResetAverage,
}

/// Board-control writes are serialized between complete BCPT batches. They do
/// not contend with UART or UDP capture, and the rolling window is restarted so
/// captures acquired under the old and new settings are never averaged together.
fn live_command_effect(command: &Cmd) -> LiveCommandEffect {
    match command {
        Cmd::ReadRoutes | Cmd::ReadCurrent | Cmd::Stat => LiveCommandEffect::ReadOnly,
        Cmd::ApplyRoute { .. }
        | Cmd::SetDds(_)
        | Cmd::ProgramNeuron { .. }
        | Cmd::SetNeuronTiming { .. }
        | Cmd::SetCic(_)
        | Cmd::ProgramCurrentWave { .. }
        | Cmd::ProgramCurrentStep { .. }
        | Cmd::StopCurrent
        | Cmd::ProgramBram { .. } => LiveCommandEffect::ResetAverage,
        _ => LiveCommandEffect::Blocked,
    }
}

fn handle(l: &mut Link, cfg: &BoardCfg, cmd: Cmd, tx: &Sender<Evt>, ctx: &egui::Context) {
    let emit = |e: Evt| {
        let _ = tx.send(e);
        ctx.request_repaint();
    };
    match cmd {
        Cmd::Raw(s) => {
            let r = l.cmd(
                &s,
                &["OK", "ERR", "DAC xbar", "STRM", "DDS", "RO", "RW", "UART:"],
                Duration::from_secs(3),
            );
            emit(Evt::Reply(if r.is_empty() {
                "(no reply)".into()
            } else {
                r
            }));
        }
        Cmd::Stat => {
            l.send_line("STAT");
            let mut lines = Vec::new();
            let deadline = Instant::now() + Duration::from_millis(1500);
            while Instant::now() < deadline {
                while let Some(ln) = l.take_line() {
                    if !ln.is_empty() {
                        lines.push(ln);
                    }
                }
                l.pump();
            }
            let blob = lines.join("\n");
            let ok = lines.iter().any(|x| x.starts_with("RW0")) && blob.contains("decoded:");
            let health: Vec<&str> = ["qpll_locked", "tx_ready", "rx_ready"]
                .iter()
                .filter(|k| blob.contains(**k))
                .cloned()
                .collect();
            emit(Evt::Stat {
                ok,
                health: if health.is_empty() {
                    "link not ready".into()
                } else {
                    health.join(", ")
                },
                raw: blob,
            });
        }
        Cmd::ApplyRoute { ch, src_idx } => {
            let token = dsp::source_token(src_idx);
            let expected = dsp::source_code(src_idx);
            let r = l.cmd(
                &format!("NSRC {ch} {token}"),
                &["DAC xbar"],
                Duration::from_secs(2),
            );
            let (ok, detail) = if r.is_empty() {
                (false, "no UART response to NSRC".into())
            } else if r.starts_with("ERR") {
                (false, r)
            } else {
                match read_routes(l) {
                    Ok((_routes, value)) => {
                        let actual = ((value >> (4 * ch)) & 0xF) as u8;
                        if actual == expected {
                            (true, format!("reg17=0x{value:04X}, code {actual}"))
                        } else {
                            (
                                false,
                                format!(
                                    "reg17 mismatch: requested code {expected}, read {actual} \
                                     (reg17=0x{value:04X})"
                                ),
                            )
                        }
                    }
                    Err(error) => (false, error),
                }
            };
            emit(Evt::RouteDone {
                ch,
                ok,
                src_idx,
                detail,
            });
        }
        Cmd::ReadRoutes => match read_routes(l) {
            Ok((routes, value)) => emit(Evt::RoutesRead {
                routes,
                detail: format!("reg17=0x{value:04X}"),
            }),
            Err(detail) => emit(Evt::RoutesRead {
                routes: [None; 4],
                detail,
            }),
        },
        Cmd::SetDds(inc) => {
            let r = l.cmd(
                &format!("DDSI 0x{inc:06X}"),
                &["DDS inc="],
                Duration::from_secs(2),
            );
            emit(Evt::Reply(if r.is_empty() {
                "(no reply)".into()
            } else {
                r
            }));
        }
        Cmd::ProgramNeuron {
            target,
            profile,
            params,
            profile_label,
        } => {
            let mut ok = true;
            if let Some(p) = &profile {
                ok &= apply_profile(l, &target, p);
            }
            for (name, q16) in &params {
                let r = l.cmd(
                    &format!("NEUR {target} {name} 0x{q16:08X}"),
                    &["OK NEUR"],
                    Duration::from_secs(1),
                );
                ok &= !r.is_empty() && !r.starts_with("ERR");
            }
            emit(Evt::NeuronDone {
                target,
                ok,
                profile: profile_label,
            });
        }
        Cmd::SetNeuronTiming { dt, period } => match apply_neuron_timing(l, dt, period) {
            Ok(detail) => emit(Evt::Status(detail)),
            Err(detail) => emit(Evt::Error(detail)),
        },
        Cmd::SetCic(on) => {
            let r = l.cmd(
                &format!("STRM CIC {}", if on { "on" } else { "off" }),
                &["OK STRM"],
                Duration::from_secs(1),
            );
            emit(Evt::Status(format!(
                "CIC {}: {}",
                if on { "on" } else { "off" },
                r
            )));
        }
        Cmd::StartStream { decim, cic } => {
            let c = if cic { " cic" } else { "" };
            let r = l.cmd(
                &format!("STRM {decim}{c}"),
                &["OK STRM"],
                Duration::from_secs(2),
            );
            emit(Evt::Status(format!("stream: {r}")));
        }
        Cmd::StopStream => {
            let _ = l.cmd("STRM STOP", &["OK STRM"], Duration::from_secs(2));
            emit(Evt::Status("stream stopped".into()));
        }
        Cmd::UartCapture(frames) => {
            let need = frames as usize * 8 * 4;
            match l.capture_bin(&format!("PCAP {frames}"), need, Duration::from_secs(15)) {
                Some(data) if data.len() >= need => {
                    let chans = decode_pcap(&data, frames as usize);
                    let saved = save_capture(cfg, "uart", &chans);
                    emit(Evt::Capture {
                        kind: "uart".into(),
                        chans,
                        cov: 1.0,
                        tries: 1,
                        saved,
                    });
                }
                _ => emit(Evt::Error("UART capture: no sync / short read".into())),
            }
        }
        Cmd::CollectEth { bytes, save } => match collect_eth(l, cfg, bytes) {
            Ok((chans, cov, tries)) => {
                let saved = if save {
                    save_capture(cfg, "eth", &chans)
                } else {
                    None
                };
                emit(Evt::Capture {
                    kind: "eth".into(),
                    chans,
                    cov,
                    tries,
                    saved,
                });
            }
            Err(e) => emit(Evt::Error(format!("Collect Ethernet: {e}"))),
        },
        Cmd::ProgramCurrentWave {
            samples_q16,
            cps,
            hold,
            gain_q8_8,
            description,
        } => {
            let mut ok = set_current_gain(l, gain_q8_8);
            let mut reply = if ok {
                String::new()
            } else {
                "CURG failed or timed out".into()
            };
            if samples_q16.is_empty() || samples_q16.len() > dsp::CURRENT_WAVE_MAX {
                ok = false;
                reply = "current waveform must contain 1..1024 samples".into();
            } else if ok {
                let mode = if hold { " hold" } else { "" };
                l.send_line(&format!(
                    "CURW {} {}{}",
                    cps.clamp(1, u16::MAX as u32),
                    samples_q16.len(),
                    mode
                ));
                let ready = l
                    .read_until(&["CWRD"], Duration::from_secs(2))
                    .unwrap_or_default();
                if !ready.starts_with("CWRD") {
                    ok = false;
                    reply = if ready.is_empty() {
                        "CURW ready timeout".into()
                    } else {
                        ready
                    };
                } else {
                    let mut bytes = Vec::with_capacity(samples_q16.len() * 4);
                    for word in &samples_q16 {
                        bytes.extend_from_slice(&word.to_le_bytes());
                    }
                    ok = l.port.write_all(&bytes).is_ok() && l.port.flush().is_ok();
                    if ok {
                        reply = l
                            .read_until(&["OK CURW"], Duration::from_secs(4))
                            .unwrap_or_default();
                        ok = reply.starts_with("OK CURW");
                        if ok {
                            match verify_current_state(
                                l,
                                cps,
                                samples_q16.len().saturating_sub(1) as u32,
                                hold,
                                gain_q8_8,
                            ) {
                                Ok(detail) => reply = format!("{reply}; {detail}"),
                                Err(error) => {
                                    ok = false;
                                    reply = error;
                                }
                            }
                        }
                    } else {
                        reply = "CURW binary transfer failed".into();
                    }
                }
            }
            emit(Evt::CurrentDone {
                ok,
                running: ok,
                status: if ok {
                    format!("{description}: {reply}")
                } else {
                    reply
                },
            });
        }
        Cmd::ProgramCurrentStep {
            cps,
            zero_count,
            high_count,
            amp_q16,
            looped,
            gain_q8_8,
            description,
        } => {
            let mut ok = set_current_gain(l, gain_q8_8);
            let mut reply = if ok {
                String::new()
            } else {
                "CURG failed or timed out".into()
            };
            if ok {
                reply = l.cmd(
                    &format!(
                        "CURS {} {} {} 0x{:08X} {}",
                        cps.clamp(1, u16::MAX as u32),
                        zero_count,
                        high_count,
                        amp_q16,
                        if looped { "loop" } else { "hold" }
                    ),
                    &["OK CURS"],
                    Duration::from_secs(3),
                );
                ok = reply.starts_with("OK CURS");
                if ok {
                    match verify_current_state(
                        l,
                        cps,
                        zero_count.saturating_add(high_count).saturating_sub(1),
                        !looped,
                        gain_q8_8,
                    ) {
                        Ok(detail) => reply = format!("{reply}; {detail}"),
                        Err(error) => {
                            ok = false;
                            reply = error;
                        }
                    }
                }
            }
            emit(Evt::CurrentDone {
                ok,
                running: ok,
                status: if ok {
                    format!("{description}: {reply}")
                } else {
                    reply
                },
            });
        }
        Cmd::StopCurrent => {
            let reply = l.cmd("CURP off", &["CURP off"], Duration::from_secs(2));
            let mut ok = reply.starts_with("CURP off");
            let mut status = if ok {
                "current player stopped".into()
            } else {
                reply
            };
            if ok {
                match read_current_state(l) {
                    Ok(state) if !state.running => status = state.describe(),
                    Ok(state) => {
                        ok = false;
                        status = format!(
                            "stop acknowledged but run bit remains set: reg16=0x{:08X}",
                            state.control
                        );
                    }
                    Err(error) => {
                        ok = false;
                        status = error;
                    }
                }
            }
            emit(Evt::CurrentDone {
                ok,
                running: false,
                status,
            });
        }
        Cmd::ReadCurrent => match read_current_state(l) {
            Ok(state) => emit(Evt::CurrentDone {
                ok: true,
                running: state.running,
                status: state.describe(),
            }),
            Err(error) => emit(Evt::CurrentDone {
                ok: false,
                running: false,
                status: error,
            }),
        },
        Cmd::ProgramBram {
            chans,
            words,
            loop_frames,
        } => {
            for ch in chans {
                program_bram_channel(l, ch, &words, loop_frames);
            }
            emit(Evt::Status("BRAM programmed".into()));
        }
        _ => {}
    }
}

fn parse_register_value(line: &str, register: usize) -> Option<u32> {
    let (name, value) = line.split_once('=')?;
    if name.trim() != format!("REG{register}") {
        return None;
    }
    let value = value.trim();
    if let Some(hex) = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
    {
        u32::from_str_radix(hex, 16).ok()
    } else {
        value.parse().ok()
    }
}

/// Read the CPU-visible crossbar register and translate its four hardware
/// nibbles into the viewer's display indices. This never changes board state.
fn read_routes(link: &mut Link) -> Result<([Option<usize>; 4], u32), String> {
    let reply = link.cmd("RDRW 17", &["REG17"], Duration::from_secs(2));
    if reply.is_empty() {
        return Err("no UART response to RDRW 17".into());
    }
    if reply.starts_with("ERR") {
        return Err(format!("crossbar readback failed: {reply}"));
    }
    let value = parse_register_value(&reply, 17)
        .ok_or_else(|| format!("malformed crossbar readback: {reply}"))?;
    let routes =
        std::array::from_fn(|ch| dsp::source_idx_from_code(((value >> (4 * ch)) & 0xF) as u8));
    Ok((routes, value))
}

fn set_current_gain(l: &mut Link, gain_q8_8: u16) -> bool {
    let reply = l.cmd(
        &format!("CURG 0x{gain_q8_8:04X}"),
        &["OK CURG"],
        Duration::from_secs(2),
    );
    reply.starts_with("OK CURG")
}

fn apply_neuron_timing(link: &mut Link, dt: u32, period: u32) -> Result<String, String> {
    let period = period.clamp(1, 0xFF_FFFF);
    let period_reply = link.cmd(
        &format!("NEUR all period {period}"),
        &["OK NEUR"],
        Duration::from_secs(1),
    );
    let dt_reply = link.cmd(
        &format!("NEUR all dt 0x{dt:X}"),
        &["OK NEUR"],
        Duration::from_secs(1),
    );
    if period_reply.starts_with("OK NEUR") && dt_reply.starts_with("OK NEUR") {
        Ok(format!(
            "neuron timing applied: period={period} clocks, dt=0x{dt:X}; routes/profiles preserved"
        ))
    } else {
        Err(format!(
            "neuron timing failed: period=[{period_reply}], dt=[{dt_reply}]"
        ))
    }
}

#[derive(Clone, Copy)]
struct CurrentState {
    control: u32,
    gain_q8_8: u16,
    cps: u16,
    last_index: u16,
    hold: bool,
    running: bool,
}

impl CurrentState {
    fn describe(self) -> String {
        format!(
            "{}: reg16=0x{:08X}, cps={}, samples={}, mode={}, reg20 gain={:.3}x",
            if self.running {
                "current player RUNNING"
            } else {
                "current player STOPPED"
            },
            self.control,
            self.cps,
            u32::from(self.last_index) + 1,
            if self.hold { "hold" } else { "loop" },
            f64::from(self.gain_q8_8) / 256.0,
        )
    }
}

fn read_register(link: &mut Link, register: usize) -> Result<u32, String> {
    let reply = link.cmd(
        &format!("RDRW {register}"),
        &[&format!("REG{register}")],
        Duration::from_secs(2),
    );
    if reply.is_empty() {
        return Err(format!("no UART response to RDRW {register}"));
    }
    parse_register_value(&reply, register)
        .ok_or_else(|| format!("malformed register {register} readback: {reply}"))
}

fn read_current_state(link: &mut Link) -> Result<CurrentState, String> {
    let control = read_register(link, 16)?;
    let gain_q8_8 = (read_register(link, 20)? & 0xFFFF) as u16;
    Ok(CurrentState {
        control,
        gain_q8_8,
        cps: (control & 0xFFFF) as u16,
        last_index: ((control >> 16) & 0x3FF) as u16,
        hold: control & (1 << 26) != 0,
        running: control & (1 << 30) != 0,
    })
}

fn verify_current_state(
    link: &mut Link,
    cps: u32,
    last_index: u32,
    hold: bool,
    gain_q8_8: u16,
) -> Result<String, String> {
    let state = read_current_state(link)?;
    let expected_control = (cps.clamp(1, u16::MAX as u32) & 0xFFFF)
        | ((last_index & 0x3FF) << 16)
        | if hold { 1 << 26 } else { 0 }
        | (1 << 30);
    // Bit 31 is a restart toggle, so it intentionally is not compared.
    if state.control & 0x7FFF_FFFF != expected_control || state.gain_q8_8 != gain_q8_8 {
        return Err(format!(
            "current-player readback mismatch: reg16=0x{:08X} expected 0x{:08X} (ignoring restart bit), reg20=0x{:04X} expected 0x{:04X}",
            state.control, expected_control, state.gain_q8_8, gain_q8_8
        ));
    }
    Ok(format!(
        "hardware verified reg16=0x{:08X}, reg20=0x{:04X}",
        state.control, state.gain_q8_8
    ))
}
fn apply_profile(l: &mut Link, target: &str, profile: &str) -> bool {
    // built-in name -> NEUR <t> <profile>; else assume caller sent params.
    if dsp::BUILTIN_PROFILES.contains(&profile) {
        let r = l.cmd(
            &format!("NEUR {target} {profile}"),
            &["OK NEUR"],
            Duration::from_secs(1),
        );
        !r.is_empty() && !r.starts_with("ERR")
    } else if let Some(vals) = dsp::builtin_profile_values(profile) {
        let mut ok = true;
        for (name, v) in vals {
            let q = dsp::izh_to_q16(v);
            let r = l.cmd(
                &format!("NEUR {target} {name} 0x{q:08X}"),
                &["OK NEUR"],
                Duration::from_secs(1),
            );
            ok &= !r.is_empty() && !r.starts_with("ERR");
        }
        ok
    } else {
        true
    }
}

fn program_bram_channel(l: &mut Link, ch: u8, words: &[u32], loop_frames: u32) {
    l.send_line(&format!("PROG {ch} {}", words.len()));
    let _ = l.read_until(&["PGRD"], Duration::from_secs(2));
    let mut bytes = Vec::with_capacity(words.len() * 4);
    for w in words {
        bytes.extend_from_slice(&w.to_le_bytes());
    }
    let _ = l.port.write_all(&bytes);
    let _ = l.port.flush();
    let _ = l.read_until(&[&format!("OK PROG ch={ch}")], Duration::from_secs(3));
    let rw3 = ((loop_frames & 0xFFFFFF) << 8) | 0x60;
    l.cmd(
        &format!("WRTE 3 0x{rw3:08X}"),
        &["OK", "RW"],
        Duration::from_secs(1),
    );
}

/// BCAP captures once; failed UDP drains re-read the same DDR contents.
fn collect_eth(
    link: &mut Link,
    cfg: &BoardCfg,
    bytes: usize,
) -> Result<([Vec<i16>; 4], f32, u32), String> {
    let kb = bytes.max(1024) / 1024;
    link.cmd("STRM STOP", &["OK STRM", "ERR"], Duration::from_secs(2));
    let reply = link.cmd(
        &format!("BCAP {kb}k"),
        &["OK BCAP", "ERR"],
        Duration::from_secs(30),
    );
    if !reply.starts_with("OK BCAP") {
        return Err(format!("BCAP failed: {reply}"));
    }
    let (buffers, coverage, attempts) = drain_ddr(link, cfg, bytes, 3)?;
    let (ch0, ch1) = decode_chip(&buffers[0]);
    let (ch2, ch3) = decode_chip(&buffers[1]);
    Ok(([ch0, ch1, ch2, ch3], coverage, attempts))
}

#[derive(Clone, Copy)]
struct BcptMeta {
    reps: usize,
    bytes_per_rep: usize,
    stride: usize,
    total_per_chip: usize,
}

fn parse_bcpt_reply(line: &str) -> Result<BcptMeta, String> {
    let mut reps = None;
    let mut bytes_per_rep = None;
    let mut stride = None;
    let mut total_per_chip = None;
    for token in line.split_whitespace() {
        let Some((key, value)) = token.split_once('=') else {
            continue;
        };
        let parsed = if let Some(hex) = value
            .strip_prefix("0x")
            .or_else(|| value.strip_prefix("0X"))
        {
            usize::from_str_radix(hex, 16).ok()
        } else {
            value.parse::<usize>().ok()
        };
        match key {
            "reps" => reps = parsed,
            "bytes_per_rep" => bytes_per_rep = parsed,
            "stride" => stride = parsed,
            "total_per_chip" => total_per_chip = parsed,
            _ => {}
        }
    }
    match (reps, bytes_per_rep, stride, total_per_chip) {
        (Some(reps), Some(bytes_per_rep), Some(stride), Some(total_per_chip)) => Ok(BcptMeta {
            reps,
            bytes_per_rep,
            stride,
            total_per_chip,
        }),
        _ => Err(format!("unparseable BCPT reply: {line}")),
    }
}

fn collect_bcpt_batch(
    link: &mut Link,
    cfg: &BoardCfg,
    bytes: usize,
    reps: usize,
) -> Result<(Vec<Capture>, f32, u32), String> {
    let kb = bytes.max(1024) / 1024;
    let reply = link.cmd(
        &format!("BCPT {kb}k {reps}"),
        &["OK BCPT", "ERR"],
        Duration::from_secs(180),
    );
    if !reply.starts_with("OK BCPT") {
        return Err(format!(
            "BCPT failed: {reply}; the current player must be configured and running"
        ));
    }
    let meta = parse_bcpt_reply(&reply)?;
    let (buffers, coverage, attempts) = drain_ddr(link, cfg, meta.total_per_chip, 3)?;
    let (ch0, ch1) = decode_chip(&buffers[0]);
    let (ch2, ch3) = decode_chip(&buffers[1]);
    let channels = [ch0, ch1, ch2, ch3];

    let captures = slice_bcpt_channels(channels, meta)?;
    Ok((captures, coverage, attempts))
}

fn slice_bcpt_channels(channels: [Vec<i16>; 4], meta: BcptMeta) -> Result<Vec<Capture>, String> {
    let samples_per_rep = meta.bytes_per_rep / 4;
    let sample_stride = meta.stride / 4;
    if samples_per_rep == 0 || sample_stride < samples_per_rep {
        return Err(format!(
            "invalid BCPT layout: bytes_per_rep={} stride={}",
            meta.bytes_per_rep, meta.stride
        ));
    }
    let mut captures = Vec::with_capacity(meta.reps);
    for rep in 0..meta.reps {
        let start = rep * sample_stride;
        let end = start + samples_per_rep;
        if channels.iter().any(|channel| end > channel.len()) {
            return Err(format!(
                "BCPT layout exceeds decoded data at repetition {rep}: end={end}"
            ));
        }
        captures.push(std::array::from_fn(|channel| {
            channels[channel][start..end].to_vec()
        }));
    }
    Ok(captures)
}

fn drain_ddr(
    link: &mut Link,
    cfg: &BoardCfg,
    total_per_chip: usize,
    max_attempts: u32,
) -> Result<([Vec<u8>; 2], f32, u32), String> {
    let assembler = Reassembler::new(
        &cfg.local_ip,
        cfg.local_port,
        &cfg.board_ip,
        cfg.cmd_port,
        total_per_chip,
    )
    .map_err(|error| format!("UDP bind: {error}"))?;

    let mut last = String::from("no drain attempt");
    for attempt in 0..max_attempts {
        if attempt > 0 {
            std::thread::sleep(Duration::from_millis(400));
        }
        assembler.begin_request();
        if !assembler.register(Duration::from_secs(2)) {
            last = "BRST registration timed out".into();
            continue;
        }
        let reply = link.cmd("BRDO", &["OK BRDO", "ERR"], Duration::from_secs(10));
        let Some(request_id) = parse_brdo_request(&reply) else {
            last = format!("BRDO failed: {reply}");
            continue;
        };
        if !reply.starts_with("OK BRDO") {
            last = format!("BRDO failed: {reply}");
            continue;
        }
        assembler.set_request_id(request_id);

        let drain_seconds = (2.0 * total_per_chip as f64 / 70.0e6 + 4.0).max(10.0);
        let deadline = Instant::now() + Duration::from_secs_f64(drain_seconds);
        while Instant::now() < deadline && !assembler.complete() {
            let started = assembler.coverage(0) > 0.0 || assembler.coverage(1) > 0.0;
            if started && assembler.idle(Duration::from_millis(800)) {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        if assembler.complete() {
            return Ok((assembler.buffers(), 1.0, attempt + 1));
        }
        last = format!(
            "UDP drain incomplete: chip0 {:.1}%, chip1 {:.1}%",
            100.0 * assembler.coverage(0),
            100.0 * assembler.coverage(1)
        );
    }
    Err(format!("{last} after {max_attempts} attempts"))
}

fn save_capture(cfg: &BoardCfg, kind: &str, chans: &[Vec<i16>; 4]) -> Option<String> {
    let dir = std::path::Path::new(&cfg.capture_dir);
    std::fs::create_dir_all(dir).ok()?;
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let path = dir.join(format!("cap_{secs}_{kind}.csv"));
    let n = chans.iter().map(|c| c.len()).max().unwrap_or(0);
    let mut out = String::with_capacity(n * 24);
    out.push_str("ch0,ch1,ch2,ch3\n");
    for i in 0..n {
        for ch in 0..4 {
            if ch > 0 {
                out.push(',');
            }
            if i < chans[ch].len() {
                out.push_str(&chans[ch][i].to_string());
            }
        }
        out.push('\n');
    }
    std::fs::write(&path, out).ok()?;
    Some(path.to_string_lossy().into_owned())
}

#[cfg(test)]
mod link_tests {
    use super::capture_payload_start;

    #[test]
    fn capture_sync_returns_payload_in_the_same_serial_chunk() {
        let sync = [0xFE, 0x10, 0xCA, 0xFE];
        let wire = [b'O', b'K', b'\r', b'\n', 0xFE, 0x10, 0xCA, 0xFE, 1, 2, 3, 4];
        let start = capture_payload_start(&wire, &sync).expect("sync");
        assert_eq!(&wire[start..], &[1, 2, 3, 4]);
    }

    #[test]
    fn capture_sync_can_be_found_after_a_split_read_is_joined() {
        let sync = [0xFE, 0x10, 0xCA, 0xFE];
        let mut wire = vec![b'\n', 0xFE, 0x10];
        wire.extend_from_slice(&[0xCA, 0xFE, 9, 8]);
        let start = capture_payload_start(&wire, &sync).expect("sync");
        assert_eq!(&wire[start..], &[9, 8]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_crossbar_register_readback() {
        assert_eq!(parse_register_value("REG17 = 0x00006F61", 17), Some(0x6F61));
        assert_eq!(parse_register_value("REG17 = 4369", 17), Some(4369));
        assert_eq!(
            parse_register_value("REG16 = 0xC42F0001", 16),
            Some(0xC42F0001)
        );
        assert_eq!(parse_register_value("REG16 = 0x1111", 17), None);
        assert_eq!(parse_register_value("ERR unknown", 17), None);
    }

    #[test]
    fn live_mode_allows_controls_but_blocks_competing_acquisitions() {
        assert_eq!(
            live_command_effect(&Cmd::SetNeuronTiming {
                dt: 0x8000,
                period: 1
            }),
            LiveCommandEffect::ResetAverage
        );
        assert_eq!(
            live_command_effect(&Cmd::ReadCurrent),
            LiveCommandEffect::ReadOnly
        );
        assert_eq!(
            live_command_effect(&Cmd::UartCapture(1024)),
            LiveCommandEffect::Blocked
        );
    }

    #[test]
    fn parses_and_slices_strided_bcpt_layout_without_using_padding() {
        let meta = parse_bcpt_reply("OK BCPT reps=3 bytes_per_rep=16 stride=32 total_per_chip=96")
            .unwrap();
        let channels: [Vec<i16>; 4] = std::array::from_fn(|channel| {
            (0..24)
                .map(|sample| (channel * 100 + sample) as i16)
                .collect()
        });
        let captures = slice_bcpt_channels(channels, meta).unwrap();
        assert_eq!(captures.len(), 3);
        assert_eq!(captures[0][0], vec![0, 1, 2, 3]);
        assert_eq!(captures[1][0], vec![8, 9, 10, 11]);
        assert_eq!(captures[2][3], vec![316, 317, 318, 319]);
    }
}
