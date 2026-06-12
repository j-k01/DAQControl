#!/usr/bin/env python3
"""Live oscilloscope view of the continuous ADC Ethernet stream.

Prerequisite: arm the stream on the MicroBlaze first (UART):
    STRM 256

Then run:
    python scripts/live_stream_scope.py            # 4-channel time view
    python scripts/live_stream_scope.py --fft      # + spectrum per CHANNEL

Closing the window sends STOP to the board.

Display stability: packets arriving out of order are discarded (this is a
monitor, not a recorder) so the rolling buffers stay phase-continuous, and
spectra are Welch-averaged within each frame plus exponentially smoothed
across frames on a fixed dBFS scale. For lossless capture use
receive_ps_eth_stream_continuous.py instead.
"""

from __future__ import annotations

import argparse
import socket
import struct
import threading

import matplotlib.pyplot as plt
import numpy as np

HDR = struct.Struct("<IHHIIIIII")
MAGIC = 0x53514144
VOLTS_PER_COUNT = 1.9 / 65536.0


class StreamTap:
    """Receives the UDP stream and keeps the latest N samples per channel.

    Only in-order packets are appended (late ones are dropped) so the
    window never contains phase discontinuities from reordering; a lost
    packet just shortens history by one packet's worth of samples.
    """

    def __init__(self, args, window):
        self.window = window
        self.chans = {i: np.zeros(window, dtype=np.int16) for i in range(4)}
        self.expected = {0: None, 1: None}
        self.lock = threading.Lock()
        self.decim = None
        self.pkts = 0
        self.discarded = 0
        self.running = True

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024 * 1024)
        self.sock.bind((args.local_ip, args.local_port))
        self.sock.settimeout(1.0)
        self.board = (args.board_ip, args.cmd_port)
        self.sock.sendto(b"STRM", self.board)

        self.thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.thread.start()

    def _rx_loop(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < HDR.size:
                continue
            magic, _v, hdr, seq, chip, _off, count, _drops, dec = \
                HDR.unpack_from(data)
            if magic != MAGIC or chip > 1:
                continue

            exp = self.expected[chip]
            if exp is not None and seq < exp:
                self.discarded += 1   # late/reordered: keep buffers continuous
                continue
            self.expected[chip] = seq + 1

            self.decim = dec
            payload = data[hdr:hdr + count]
            if len(payload) % 16:
                payload = payload[: len(payload) - (len(payload) % 16)]
            samples = np.frombuffer(payload, dtype="<i2").reshape(-1, 8)
            even = samples[:, :4].ravel()
            odd = samples[:, 4:].ravel()
            base = chip * 2
            with self.lock:
                for ch, new in ((base, even), (base + 1, odd)):
                    buf = self.chans[ch]
                    n = len(new)
                    if n >= self.window:
                        buf[:] = new[-self.window:]
                    else:
                        buf[:-n] = buf[n:]
                        buf[-n:] = new
            self.pkts += 1

    def snapshot(self):
        with self.lock:
            return {ch: buf.copy() for ch, buf in self.chans.items()}

    def close(self):
        self.running = False
        try:
            self.sock.sendto(b"STOP", self.board)
        except OSError:
            pass
        self.sock.close()


def welch_db(x, nseg):
    """Welch periodogram in dBFS (0 dB = full-scale sine), Hann segments."""
    seg = len(x) // nseg
    win = np.hanning(seg)
    # full-scale sine amplitude 32768 -> coherent-gain-corrected peak
    fs_peak = 32768.0 * win.sum() / 2.0
    acc = None
    for k in range(nseg):
        s = x[k * seg:(k + 1) * seg].astype(np.float64)
        s -= s.mean()
        p = np.abs(np.fft.rfft(s * win)) ** 2
        acc = p if acc is None else acc + p
    acc /= nseg
    return 10.0 * np.log10(acc / (fs_peak ** 2) + 1e-20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-ip", default="192.168.2.10")
    parser.add_argument("--cmd-port", type=int, default=5006)
    parser.add_argument("--local-ip", default="192.168.2.1")
    parser.add_argument("--local-port", type=int, default=5005)
    parser.add_argument("--window", type=int, default=8192,
                        help="samples per channel kept/shown")
    parser.add_argument("--fft", action="store_true",
                        help="add a spectrum column, one per channel")
    parser.add_argument("--fft-segments", type=int, default=4,
                        help="Welch segments per frame (more = smoother)")
    parser.add_argument("--fft-smooth", type=float, default=0.35,
                        help="EMA weight of the new frame (0..1, lower = smoother)")
    parser.add_argument("--time-span", type=int, default=2048,
                        help="samples shown in the time panels")
    parser.add_argument("--fps", type=float, default=15.0)
    args = parser.parse_args()

    tap = StreamTap(args, args.window)

    ncols = 2 if args.fft else 1
    fig, axes = plt.subplots(4, ncols, figsize=(7 * ncols + 4, 9), squeeze=False)
    fig.canvas.manager.set_window_title("DAQ live stream")

    time_lines = []
    for ch in range(4):
        ax = axes[ch][0]
        (ln,) = ax.plot(np.zeros(args.time_span), lw=0.8)
        ax.set_ylabel(f"ch{ch} [V]")
        ax.set_ylim(-0.95, 0.95)
        ax.grid(True, alpha=0.3)
        time_lines.append(ln)
    axes[3][0].set_xlabel("time [us]")

    fft_lines, fft_ema = [], [None] * 4
    if args.fft:
        for ch in range(4):
            ax = axes[ch][1]
            (ln,) = ax.plot([], [], lw=0.8)
            ax.set_ylabel(f"ch{ch} [dBFS]")
            ax.set_ylim(-100, 3)
            ax.grid(True, alpha=0.3)
            fft_lines.append(ln)
        axes[3][1].set_xlabel("frequency [MHz]")

    closed = {"flag": False}
    fig.canvas.mpl_connect("close_event", lambda _e: closed.update(flag=True))

    try:
        while not closed["flag"]:
            snap = tap.snapshot()
            decim = tap.decim or 256
            dt_us = decim / 1000.0
            t = np.arange(args.time_span) * dt_us
            for ch, ln in enumerate(time_lines):
                ln.set_data(t, snap[ch][-args.time_span:] * VOLTS_PER_COUNT)
                axes[ch][0].set_xlim(0, t[-1])
            axes[0][0].set_title(
                f"decim={decim} ({1000.0/decim:.3f} MS/s/ch)  "
                f"pkts={tap.pkts}  dropped-for-order={tap.discarded}")

            if args.fft:
                fs = 1e9 / decim
                seg = args.window // args.fft_segments
                freqs = np.fft.rfftfreq(seg, d=1.0 / fs) / 1e6
                for ch, ln in enumerate(fft_lines):
                    db = welch_db(snap[ch], args.fft_segments)
                    if fft_ema[ch] is None or len(fft_ema[ch]) != len(db):
                        fft_ema[ch] = db
                    else:
                        a = args.fft_smooth
                        fft_ema[ch] = a * db + (1.0 - a) * fft_ema[ch]
                    ln.set_data(freqs, fft_ema[ch])
                    axes[ch][1].set_xlim(0, freqs[-1])

            fig.tight_layout()
            plt.pause(1.0 / args.fps)
    except KeyboardInterrupt:
        pass
    finally:
        tap.close()


if __name__ == "__main__":
    main()
