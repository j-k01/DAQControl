//! Serial worker thread + command/event channels. All board UART/UDP I/O runs
//! here so the egui UI thread never blocks. Mirrors DacControl + burst flow.

use std::io::{Read, Write};
use std::sync::mpsc::{Receiver, Sender};
use std::time::{Duration, Instant};

use crate::burst::{decode_chip, decode_pcap, parse_brdo_request, Reassembler};
use crate::dsp;

#[derive(Clone)]
pub struct BoardCfg {
    pub board_ip: String,
    pub cmd_port: u16,
    pub local_ip: String,
    pub local_port: u16,
    pub capture_dir: String,
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
    /// commit a crossbar route: NSRC + (optional neuron reprogram)
    ApplyRoute {
        ch: u8,
        src_idx: usize,
        profile: String,
    },
    SetDds(u32),
    ProgramNeuron {
        target: String,
        profile: Option<String>,
        params: Vec<(String, u32)>,
        profile_label: String,
    },
    SetNeuronDt(u32),
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
    Stat { ok: bool, health: String, raw: String },
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
        profile: Option<String>,
        neuron: Option<u8>,
    },
    NeuronDone { target: String, ok: bool, profile: String },
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
        let mut me = Self { port, rx: Vec::new() };
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
                if line.starts_with("ERR")
                    || prefixes.iter().any(|p| line.starts_with(p))
                {
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
        // find sync in the raw stream
        let mut win: Vec<u8> = Vec::new();
        // seed with anything already buffered
        let mut stream = std::mem::take(&mut self.rx);
        loop {
            for &b in &stream {
                win.push(b);
                if win.len() > 4 {
                    win.remove(0);
                }
                if win == SYNC {
                    // collect remaining bytes after the sync from this chunk
                    return self.read_exact(need, deadline);
                }
            }
            stream.clear();
            if Instant::now() >= deadline {
                return None;
            }
            let mut b = [0u8; 4096];
            if let Ok(n) = self.port.read(&mut b) {
                stream.extend_from_slice(&b[..n]);
            }
        }
    }

    fn read_exact(&mut self, need: usize, deadline: Instant) -> Option<Vec<u8>> {
        let mut data = std::mem::take(&mut self.rx);
        data.truncate(need);
        while data.len() < need {
            if Instant::now() >= deadline {
                return None;
            }
            let mut b = [0u8; 8192];
            match self.port.read(&mut b) {
                Ok(n) if n > 0 => {
                    let take = (need - data.len()).min(n);
                    data.extend_from_slice(&b[..take]);
                }
                _ => {}
            }
        }
        Some(data)
    }
}

pub fn spawn(ctx: egui::Context, cfg: BoardCfg) -> (Sender<Cmd>, Receiver<Evt>) {
    let (ctx_tx, cmd_rx) = std::sync::mpsc::channel::<Cmd>();
    let (evt_tx, evt_rx) = std::sync::mpsc::channel::<Evt>();
    std::thread::spawn(move || worker(ctx, cfg, cmd_rx, evt_tx));
    (ctx_tx, evt_rx)
}

fn worker(ctx: egui::Context, cfg: BoardCfg, rx: Receiver<Cmd>, tx: Sender<Evt>) {
    let mut link: Option<Link> = None;
    let emit = |tx: &Sender<Evt>, ctx: &egui::Context, e: Evt| {
        let _ = tx.send(e);
        ctx.request_repaint();
    };

    while let Ok(cmd) = rx.recv() {
        match cmd {
            Cmd::Shutdown => break,
            Cmd::Connect(port) => match Link::open(&port) {
                Ok(mut l) => {
                    // board init (no stream) + default DDS route
                    l.cmd("WRTE 2 0x01000018", &["OK", "RW"], Duration::from_secs(2));
                    for (ch, prof) in dsp::BUILTIN_PROFILES.iter().enumerate() {
                        l.cmd(&format!("NEUR {ch} {prof}"), &["OK NEUR"], Duration::from_secs(1));
                    }
                    for ch in 0..4 {
                        l.cmd(&format!("NSRC {ch} dds"), &["DAC xbar"], Duration::from_secs(1));
                    }
                    link = Some(l);
                    emit(&tx, &ctx, Evt::Connected(Ok(port)));
                }
                Err(e) => emit(&tx, &ctx, Evt::Connected(Err(e))),
            },
            Cmd::Disconnect => {
                link = None;
                emit(&tx, &ctx, Evt::Disconnected);
            }
            other => {
                if let Some(l) = link.as_mut() {
                    handle(l, &cfg, other, &tx, &ctx);
                } else {
                    emit(&tx, &ctx, Evt::Error("not connected".into()));
                }
            }
        }
    }
}

fn handle(l: &mut Link, cfg: &BoardCfg, cmd: Cmd, tx: &Sender<Evt>, ctx: &egui::Context) {
    let emit = |e: Evt| {
        let _ = tx.send(e);
        ctx.request_repaint();
    };
    match cmd {
        Cmd::Raw(s) => {
            let r = l.cmd(&s, &["OK", "ERR", "DAC xbar", "STRM", "DDS", "RO", "RW", "UART:"], Duration::from_secs(3));
            emit(Evt::Reply(if r.is_empty() { "(no reply)".into() } else { r }));
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
                health: if health.is_empty() { "link not ready".into() } else { health.join(", ") },
                raw: blob,
            });
        }
        Cmd::ApplyRoute { ch, src_idx, profile } => {
            let token = dsp::source_token(src_idx);
            let neuron = dsp::source_neuron_idx(src_idx);
            let r = l.cmd(&format!("NSRC {ch} {token}"), &["DAC xbar"], Duration::from_secs(2));
            let mut ok = !r.is_empty() && !r.starts_with("ERR");
            if let Some(n) = neuron {
                let r2 = apply_profile(l, &n.to_string(), &profile);
                ok = ok && r2;
            }
            emit(Evt::RouteDone {
                ch,
                ok,
                src_idx,
                profile: neuron.map(|_| profile.clone()),
                neuron,
            });
        }
        Cmd::SetDds(inc) => {
            let r = l.cmd(&format!("DDSI 0x{inc:06X}"), &["DDS inc="], Duration::from_secs(2));
            emit(Evt::Reply(if r.is_empty() { "(no reply)".into() } else { r }));
        }
        Cmd::ProgramNeuron { target, profile, params, profile_label } => {
            let mut ok = true;
            if let Some(p) = &profile {
                ok &= apply_profile(l, &target, p);
            }
            for (name, q16) in &params {
                let r = l.cmd(&format!("NEUR {target} {name} 0x{q16:08X}"), &["OK NEUR"], Duration::from_secs(1));
                ok &= !r.is_empty() && !r.starts_with("ERR");
            }
            emit(Evt::NeuronDone { target, ok, profile: profile_label });
        }
        Cmd::SetNeuronDt(dt) => {
            let _ = l.cmd(&format!("NEUR all dt 0x{dt:X}"), &["OK NEUR"], Duration::from_secs(1));
        }
        Cmd::SetCic(on) => {
            let r = l.cmd(&format!("STRM CIC {}", if on { "on" } else { "off" }), &["OK STRM"], Duration::from_secs(1));
            emit(Evt::Status(format!("CIC {}: {}", if on { "on" } else { "off" }, r)));
        }
        Cmd::StartStream { decim, cic } => {
            let c = if cic { " cic" } else { "" };
            let r = l.cmd(&format!("STRM {decim}{c}"), &["OK STRM"], Duration::from_secs(2));
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
                    emit(Evt::Capture { kind: "uart".into(), chans, cov: 1.0, tries: 1, saved });
                }
                _ => emit(Evt::Error("UART capture: no sync / short read".into())),
            }
        }
        Cmd::CollectEth { bytes, save } => match collect_eth(l, cfg, bytes) {
            Ok((chans, cov, tries)) => {
                let saved = if save { save_capture(cfg, "eth", &chans) } else { None };
                emit(Evt::Capture { kind: "eth".into(), chans, cov, tries, saved });
            }
            Err(e) => emit(Evt::Error(format!("Collect Ethernet: {e}"))),
        },
        Cmd::ProgramBram { chans, words, loop_frames } => {
            for ch in chans {
                program_bram_channel(l, ch, &words, loop_frames);
            }
            emit(Evt::Status("BRAM programmed".into()));
        }
        _ => {}
    }
}

fn apply_profile(l: &mut Link, target: &str, profile: &str) -> bool {
    // built-in name -> NEUR <t> <profile>; else assume caller sent params.
    if dsp::BUILTIN_PROFILES.contains(&profile) {
        let r = l.cmd(&format!("NEUR {target} {profile}"), &["OK NEUR"], Duration::from_secs(1));
        !r.is_empty() && !r.starts_with("ERR")
    } else if let Some(vals) = dsp::builtin_profile_values(profile) {
        let mut ok = true;
        for (name, v) in vals {
            let q = dsp::izh_to_q16(v);
            let r = l.cmd(&format!("NEUR {target} {name} 0x{q:08X}"), &["OK NEUR"], Duration::from_secs(1));
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
    l.cmd(&format!("WRTE 3 0x{rw3:08X}"), &["OK", "RW"], Duration::from_secs(1));
}

/// BCAP + BRDO + UDP drain, retrying the whole fresh cycle on incomplete drain.
fn collect_eth(l: &mut Link, cfg: &BoardCfg, bytes: usize) -> Result<([Vec<i16>; 4], f32, u32), String> {
    let kb = bytes / 1024;
    let attempts = 4;
    let mut last = String::from("no attempts");
    for attempt in 0..attempts {
        if attempt > 0 {
            std::thread::sleep(Duration::from_millis(400));
        }
        let mut asm = Reassembler::new(&cfg.local_ip, cfg.local_port, &cfg.board_ip, cfg.cmd_port, bytes)
            .map_err(|e| format!("UDP bind: {e}"))?;
        l.cmd("STRM STOP", &["OK STRM"], Duration::from_secs(2));
        if !asm.register(Duration::from_secs(2)) {
            last = "BRST registration timed out".into();
            continue;
        }
        let bcap = l.cmd(&format!("BCAP {kb}k"), &["OK BCAP"], Duration::from_secs(30));
        if !bcap.starts_with("OK BCAP") {
            last = format!("BCAP failed: {bcap}");
            continue;
        }
        let brdo = l.cmd("BRDO", &["OK BRDO"], Duration::from_secs(10));
        let req = parse_brdo_request(&brdo);
        match req {
            Some(r) if brdo.starts_with("OK BRDO") => {
                asm.set_request(r);
                let drain_secs = (2.0 * bytes as f64 / 70.0e6 + 2.0).max(8.0);
                let deadline = Instant::now() + Duration::from_secs_f64(drain_secs);
                asm.drain(deadline, Duration::from_millis(600));
                if asm.complete() {
                    let (c0, c1) = decode_chip(&asm.buf[0]);
                    let (c2, c3) = decode_chip(&asm.buf[1]);
                    return Ok(([c0, c1, c2, c3], 1.0, attempt + 1));
                }
                last = format!(
                    "drain incomplete chip0 {:.1}% chip1 {:.1}%",
                    100.0 * asm.coverage(0),
                    100.0 * asm.coverage(1)
                );
            }
            _ => last = format!("BRDO failed: {brdo}"),
        }
    }
    Err(format!("{last} (after {attempts} attempts)"))
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
