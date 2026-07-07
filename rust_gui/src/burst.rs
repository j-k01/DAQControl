//! Full-rate burst readout over UDP (BCAP + BRDO), ported from
//! scripts/burst_capture.py Reassembler + decode_chip.

use std::net::{SocketAddr, UdpSocket};
use std::time::{Duration, Instant};

const MAGIC: u32 = 0x5351_4144;
const HDR_SIZE: usize = 32; // <IHHIIIIII
const SLOT: usize = 1408; // coverage slot = payload size

/// Reassembles the two per-chip DDR regions from the A53's UDP readout.
pub struct Reassembler {
    sock: UdpSocket,
    board: SocketAddr,
    bpc: usize,
    nslot: usize,
    pub buf: [Vec<u8>; 2],
    cov: [Vec<bool>; 2],
    req: Option<u32>,
    last_rx: Instant,
}

impl Reassembler {
    pub fn new(
        local_ip: &str,
        local_port: u16,
        board_ip: &str,
        cmd_port: u16,
        bytes_per_chip: usize,
    ) -> std::io::Result<Self> {
        let sock = UdpSocket::bind((local_ip, local_port))?;
        sock.set_read_timeout(Some(Duration::from_millis(300)))?;
        let nslot = (bytes_per_chip + SLOT - 1) / SLOT;
        let board: SocketAddr = format!("{board_ip}:{cmd_port}").parse().map_err(|_| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, "bad board addr")
        })?;
        Ok(Self {
            sock,
            board,
            bpc: bytes_per_chip,
            nslot,
            buf: [vec![0u8; bytes_per_chip], vec![0u8; bytes_per_chip]],
            cov: [vec![false; nslot], vec![false; nslot]],
            req: None,
            last_rx: Instant::now(),
        })
    }

    /// Register this socket as the burst destination; wait for BRST_READY.
    pub fn register(&self, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        let mut b = [0u8; 2048];
        while Instant::now() < deadline {
            let _ = self.sock.send_to(b"BRST", self.board);
            if let Ok((n, _)) = self.sock.recv_from(&mut b) {
                if n >= 10 && &b[..10] == b"BRST_READY" {
                    return true;
                }
            }
        }
        false
    }

    pub fn set_request(&mut self, req: u32) {
        self.req = Some(req);
        for c in 0..2 {
            self.buf[c].iter_mut().for_each(|x| *x = 0);
            self.cov[c].iter_mut().for_each(|x| *x = false);
        }
        self.last_rx = Instant::now();
    }

    pub fn coverage(&self, chip: usize) -> f32 {
        let c = &self.cov[chip];
        if c.is_empty() {
            return 1.0;
        }
        c.iter().filter(|&&v| v).count() as f32 / c.len() as f32
    }

    pub fn complete(&self) -> bool {
        self.cov.iter().all(|c| c.iter().all(|&v| v))
    }

    fn place(&mut self, data: &[u8]) {
        if data.len() < HDR_SIZE {
            return;
        }
        let rd = |o: usize| u32::from_le_bytes([data[o], data[o + 1], data[o + 2], data[o + 3]]);
        let magic = rd(0);
        let hdr = u16::from_le_bytes([data[6], data[7]]) as usize;
        let chip = rd(12) as usize;
        let off = rd(16) as usize;
        let nbytes = rd(20) as usize;
        let tag = rd(24);
        if magic != MAGIC || chip > 1 {
            return;
        }
        if let Some(r) = self.req {
            if tag != r {
                return;
            }
        }
        let end = hdr + nbytes;
        if end > data.len() || off + nbytes > self.bpc {
            return;
        }
        self.buf[chip][off..off + nbytes].copy_from_slice(&data[hdr..end]);
        let slot = off / SLOT;
        if slot < self.nslot {
            self.cov[chip][slot] = true;
        }
        self.last_rx = Instant::now();
    }

    /// Drain until complete, or `deadline`, or an idle stall past `idle` once
    /// data has started. Returns true when complete.
    pub fn drain(&mut self, deadline: Instant, idle: Duration) -> bool {
        let mut b = [0u8; 2048];
        while Instant::now() < deadline && !self.complete() {
            match self.sock.recv_from(&mut b) {
                Ok((n, _)) => self.place(&b[..n]),
                Err(_) => {}
            }
            let started = self.coverage(0) > 0.0 || self.coverage(1) > 0.0;
            if started && self.last_rx.elapsed() > idle {
                break;
            }
        }
        self.complete()
    }
}

/// One chip region (2 interleaved channels) -> (even_ch, odd_ch) i16 samples.
/// Frame = 8 int16: first 4 -> even channel, last 4 -> odd channel.
pub fn decode_chip(raw: &[u8]) -> (Vec<i16>, Vec<i16>) {
    let n = raw.len() - (raw.len() % 16);
    let frames = n / 16;
    let mut even = Vec::with_capacity(frames * 4);
    let mut odd = Vec::with_capacity(frames * 4);
    for f in 0..frames {
        let base = f * 16;
        for s in 0..4 {
            let o = base + s * 2;
            even.push(i16::from_le_bytes([raw[o], raw[o + 1]]));
        }
        for s in 0..4 {
            let o = base + 8 + s * 2;
            odd.push(i16::from_le_bytes([raw[o], raw[o + 1]]));
        }
    }
    (even, odd)
}

/// Parse `request=<id>` out of a BRDO reply line.
pub fn parse_brdo_request(line: &str) -> Option<u32> {
    for tok in line.replace(',', " ").split_whitespace() {
        if let Some(v) = tok.strip_prefix("request=") {
            let v = v.trim();
            return if let Some(h) = v.strip_prefix("0x").or_else(|| v.strip_prefix("0X")) {
                u32::from_str_radix(h, 16).ok()
            } else {
                v.parse::<u32>().ok()
            };
        }
    }
    None
}

/// Decode a UART PCAP payload (frames*8 u32) into 4 channels.
pub fn decode_pcap(data: &[u8], frames: usize) -> [Vec<i16>; 4] {
    let mut chans: [Vec<i16>; 4] = Default::default();
    for ch in 0..4 {
        chans[ch] = Vec::with_capacity(frames * 4);
    }
    for f in 0..frames {
        let base = f * 32; // 8 u32
        let word = |i: usize| {
            let o = base + i * 4;
            u32::from_le_bytes([data[o], data[o + 1], data[o + 2], data[o + 3]])
        };
        for ch in 0..4 {
            let w0 = word(2 * ch);
            let w1 = word(2 * ch + 1);
            chans[ch].push((w0 & 0xFFFF) as i16);
            chans[ch].push(((w0 >> 16) & 0xFFFF) as i16);
            chans[ch].push((w1 & 0xFFFF) as i16);
            chans[ch].push(((w1 >> 16) & 0xFFFF) as i16);
        }
    }
    chans
}
