//! Concurrent UDP burst receiver.
//!
//! The receiver thread starts before BRDO, so packets sent before the UART
//! acknowledgement are retained. Request IDs confirm those early packets
//! without clearing them when the observed tag matches.

use std::net::{SocketAddr, UdpSocket};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

const MAGIC: u32 = 0x5351_4144;
const HDR_SIZE: usize = 32;
const SLOT: usize = 1408;

#[cfg(windows)]
fn enlarge_receive_buffer(socket: &UdpSocket) {
    use std::mem::size_of;
    use std::os::windows::io::AsRawSocket;
    use windows_sys::Win32::Networking::WinSock::{setsockopt, SOL_SOCKET, SO_RCVBUF};

    let bytes: i32 = 256 << 20;
    unsafe {
        let _ = setsockopt(
            socket.as_raw_socket() as usize,
            SOL_SOCKET,
            SO_RCVBUF,
            (&bytes as *const i32).cast(),
            size_of::<i32>() as i32,
        );
    }
}

#[cfg(not(windows))]
fn enlarge_receive_buffer(_socket: &UdpSocket) {}

struct State {
    bpc: usize,
    nslot: usize,
    buf: [Vec<u8>; 2],
    cov: [Vec<bool>; 2],
    request_id: Option<u32>,
    observed_request_id: Option<u32>,
    ready: bool,
    last_rx: Instant,
}

impl State {
    fn new(bytes_per_chip: usize) -> Self {
        let nslot = bytes_per_chip.div_ceil(SLOT);
        Self {
            bpc: bytes_per_chip,
            nslot,
            buf: [vec![0; bytes_per_chip], vec![0; bytes_per_chip]],
            cov: [vec![false; nslot], vec![false; nslot]],
            request_id: None,
            observed_request_id: None,
            ready: false,
            last_rx: Instant::now(),
        }
    }

    fn clear_data(&mut self) {
        for chip in 0..2 {
            self.buf[chip].fill(0);
            self.cov[chip].fill(false);
        }
        self.last_rx = Instant::now();
    }

    fn begin_request(&mut self) {
        self.request_id = None;
        self.observed_request_id = None;
        self.clear_data();
    }

    fn set_request_id(&mut self, request_id: u32) {
        self.request_id = Some(request_id);
        if self.observed_request_id != Some(request_id) {
            self.observed_request_id = Some(request_id);
            self.clear_data();
        }
    }

    fn place(&mut self, data: &[u8]) {
        if data.len() < HDR_SIZE {
            if data.starts_with(b"BRST_READY") {
                self.ready = true;
            }
            return;
        }
        let rd = |offset: usize| {
            u32::from_le_bytes([
                data[offset],
                data[offset + 1],
                data[offset + 2],
                data[offset + 3],
            ])
        };
        let magic = rd(0);
        let header = u16::from_le_bytes([data[6], data[7]]) as usize;
        let chip = rd(12) as usize;
        let offset = rd(16) as usize;
        let nbytes = rd(20) as usize;
        let tag = rd(24);
        if magic != MAGIC || chip > 1 {
            if data.starts_with(b"BRST_READY") {
                self.ready = true;
            }
            return;
        }
        if let Some(request_id) = self.request_id {
            if tag != request_id {
                return;
            }
        } else if self.observed_request_id != Some(tag) {
            self.observed_request_id = Some(tag);
            self.clear_data();
        }
        let end = header.saturating_add(nbytes);
        if end > data.len() || offset.saturating_add(nbytes) > self.bpc {
            return;
        }
        self.buf[chip][offset..offset + nbytes].copy_from_slice(&data[header..end]);
        let slot = offset / SLOT;
        if slot < self.nslot {
            self.cov[chip][slot] = true;
        }
        self.last_rx = Instant::now();
    }

    fn coverage(&self, chip: usize) -> f32 {
        if self.cov[chip].is_empty() {
            return 1.0;
        }
        self.cov[chip].iter().filter(|&&value| value).count() as f32 / self.cov[chip].len() as f32
    }

    fn complete(&self) -> bool {
        self.cov.iter().all(|chip| chip.iter().all(|&value| value))
    }
}

pub struct Reassembler {
    sock: UdpSocket,
    board: SocketAddr,
    state: Arc<Mutex<State>>,
    running: Arc<AtomicBool>,
    receiver: Option<JoinHandle<()>>,
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
        enlarge_receive_buffer(&sock);
        sock.set_read_timeout(Some(Duration::from_millis(100)))?;
        let rx_sock = sock.try_clone()?;
        let board: SocketAddr = format!("{board_ip}:{cmd_port}").parse().map_err(|_| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, "bad board address")
        })?;
        let state = Arc::new(Mutex::new(State::new(bytes_per_chip)));
        let running = Arc::new(AtomicBool::new(true));
        let rx_state = Arc::clone(&state);
        let rx_running = Arc::clone(&running);
        let receiver = std::thread::spawn(move || {
            let mut packet = [0u8; 2048];
            while rx_running.load(Ordering::Relaxed) {
                match rx_sock.recv_from(&mut packet) {
                    Ok((size, _)) => {
                        if let Ok(mut state) = rx_state.lock() {
                            state.place(&packet[..size]);
                        }
                    }
                    Err(error)
                        if matches!(
                            error.kind(),
                            std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                        ) => {}
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            sock,
            board,
            state,
            running,
            receiver: Some(receiver),
        })
    }

    pub fn begin_request(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.begin_request();
        }
    }

    pub fn register(&self, timeout: Duration) -> bool {
        if let Ok(mut state) = self.state.lock() {
            state.ready = false;
        }
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            let _ = self.sock.send_to(b"BRST", self.board);
            let wait_until = (Instant::now() + Duration::from_millis(100)).min(deadline);
            while Instant::now() < wait_until {
                if self.state.lock().map(|state| state.ready).unwrap_or(false) {
                    return true;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        }
        false
    }

    pub fn set_request_id(&self, request_id: u32) {
        if let Ok(mut state) = self.state.lock() {
            state.set_request_id(request_id);
        }
    }

    pub fn coverage(&self, chip: usize) -> f32 {
        self.state
            .lock()
            .map(|state| state.coverage(chip))
            .unwrap_or(0.0)
    }

    pub fn complete(&self) -> bool {
        self.state
            .lock()
            .map(|state| state.complete())
            .unwrap_or(false)
    }

    pub fn idle(&self, duration: Duration) -> bool {
        self.state
            .lock()
            .map(|state| state.last_rx.elapsed() > duration)
            .unwrap_or(true)
    }

    pub fn buffers(&self) -> [Vec<u8>; 2] {
        self.state
            .lock()
            .map(|state| state.buf.clone())
            .unwrap_or_default()
    }
}

impl Drop for Reassembler {
    fn drop(&mut self) {
        self.running.store(false, Ordering::Relaxed);
        if let Ok(local) = self.sock.local_addr() {
            let _ = self.sock.send_to(b"STOP", local);
        }
        if let Some(receiver) = self.receiver.take() {
            let _ = receiver.join();
        }
    }
}

pub fn decode_chip(raw: &[u8]) -> (Vec<i16>, Vec<i16>) {
    let n = raw.len() - (raw.len() % 16);
    let frames = n / 16;
    let mut even = Vec::with_capacity(frames * 4);
    let mut odd = Vec::with_capacity(frames * 4);
    for frame in 0..frames {
        let base = frame * 16;
        for sample in 0..4 {
            let offset = base + sample * 2;
            even.push(i16::from_le_bytes([raw[offset], raw[offset + 1]]));
        }
        for sample in 0..4 {
            let offset = base + 8 + sample * 2;
            odd.push(i16::from_le_bytes([raw[offset], raw[offset + 1]]));
        }
    }
    (even, odd)
}

pub fn parse_brdo_request(line: &str) -> Option<u32> {
    for token in line.replace(',', " ").split_whitespace() {
        if let Some(value) = token.strip_prefix("request=") {
            return if let Some(hex) = value
                .strip_prefix("0x")
                .or_else(|| value.strip_prefix("0X"))
            {
                u32::from_str_radix(hex, 16).ok()
            } else {
                value.parse::<u32>().ok()
            };
        }
    }
    None
}

pub fn decode_pcap(data: &[u8], frames: usize) -> [Vec<i16>; 4] {
    let mut channels: [Vec<i16>; 4] = std::array::from_fn(|_| Vec::with_capacity(frames * 4));
    for frame in 0..frames {
        let base = frame * 32;
        let word = |index: usize| {
            let offset = base + index * 4;
            u32::from_le_bytes([
                data[offset],
                data[offset + 1],
                data[offset + 2],
                data[offset + 3],
            ])
        };
        for (channel, samples) in channels.iter_mut().enumerate() {
            let word0 = word(2 * channel);
            let word1 = word(2 * channel + 1);
            samples.push((word0 & 0xffff) as i16);
            samples.push((word0 >> 16) as i16);
            samples.push((word1 & 0xffff) as i16);
            samples.push((word1 >> 16) as i16);
        }
    }
    channels
}

#[cfg(test)]
mod tests {
    use super::*;

    fn packet(tag: u32, chip: u32, offset: u32, payload: &[u8]) -> Vec<u8> {
        let mut data = vec![0u8; HDR_SIZE + payload.len()];
        data[0..4].copy_from_slice(&MAGIC.to_le_bytes());
        data[4..6].copy_from_slice(&1u16.to_le_bytes());
        data[6..8].copy_from_slice(&(HDR_SIZE as u16).to_le_bytes());
        data[12..16].copy_from_slice(&chip.to_le_bytes());
        data[16..20].copy_from_slice(&offset.to_le_bytes());
        data[20..24].copy_from_slice(&(payload.len() as u32).to_le_bytes());
        data[24..28].copy_from_slice(&tag.to_le_bytes());
        data[HDR_SIZE..].copy_from_slice(payload);
        data
    }

    #[test]
    fn matching_early_packets_survive_uart_request_confirmation() {
        let mut state = State::new(16);
        state.begin_request();
        state.place(&packet(7, 0, 0, &[3; 16]));
        state.set_request_id(7);
        assert_eq!(state.buf[0], vec![3; 16]);
        assert_eq!(state.coverage(0), 1.0);
    }

    #[test]
    fn mismatched_early_packets_are_cleared() {
        let mut state = State::new(16);
        state.begin_request();
        state.place(&packet(6, 0, 0, &[3; 16]));
        state.set_request_id(7);
        assert_eq!(state.buf[0], vec![0; 16]);
        assert_eq!(state.coverage(0), 0.0);
    }
}
