#!/usr/bin/env python3
"""Live oscilloscope view of the continuous ADC Ethernet stream.

Prerequisite: arm the stream on the MicroBlaze first (UART):
    STRM 256

Then run:
    python scripts/live_stream_scope.py            # 4-channel time view
    python scripts/live_stream_scope.py --fft      # + live spectrum per chip

Closing the window sends STOP to the board.

Packets are 'DAQS' (see receive_ps_eth_stream_continuous.py). Each 16-byte
frame carries 4 samples of the even channel + 4 of the odd channel; sample
period is decim ns (1 GS/s / decim per channel).
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
    """Receives the UDP stream and keeps the latest N samples per channel."""

    def __init__(self, args, window):
        self.window = window
        self.chans = {i: np.zeros(window, dtype=np.int16) for i in range(4)}
        self.lock = threading.Lock()
        self.decim = None
        self.pkts = 0
        self.bytes = 0
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
            magic, _v, hdr, _seq, chip, _off, count, _drops, dec = \
                HDR.unpack_from(data)
            if magic != MAGIC or chip > 1:
                continue
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
            self.bytes += len(payload)

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-ip", default="192.168.2.10")
    parser.add_argument("--cmd-port", type=int, default=5006)
    parser.add_argument("--local-ip", default="192.168.2.1")
    parser.add_argument("--local-port", type=int, default=5005)
    parser.add_argument("--window", type=int, default=4096,
                        help="samples per channel shown")
    parser.add_argument("--fft", action="store_true",
                        help="add a live spectrum panel per chip")
    parser.add_argument("--fps", type=float, default=15.0)
    args = parser.parse_args()

    tap = StreamTap(args, args.window)

    rows = 6 if args.fft else 4
    fig, axes = plt.subplots(rows, 1, figsize=(11, 2.0 * rows))
    fig.canvas.manager.set_window_title("DAQ live stream")
    time_axes, fft_axes = axes[:4], axes[4:]

    lines = []
    for ch, ax in enumerate(time_axes):
        (ln,) = ax.plot(np.zeros(args.window), lw=0.8)
        ax.set_ylabel(f"ch{ch} [V]")
        ax.set_ylim(-0.95, 0.95)
        ax.grid(True, alpha=0.3)
        lines.append(ln)

    fft_lines = []
    for chip, ax in enumerate(fft_axes):
        (ln,) = ax.plot([], [], lw=0.8)
        ax.set_ylabel(f"chip{chip} [dB]")
        ax.set_xlabel("frequency [MHz]")
        ax.grid(True, alpha=0.3)
        fft_lines.append(ln)

    closed = {"flag": False}
    fig.canvas.mpl_connect("close_event", lambda _e: closed.update(flag=True))

    try:
        while not closed["flag"]:
            snap = tap.snapshot()
            decim = tap.decim or 256
            dt_us = decim / 1000.0
            t = np.arange(args.window) * dt_us
            for ch, ln in enumerate(lines):
                ln.set_data(t, snap[ch] * VOLTS_PER_COUNT)
                time_axes[ch].set_xlim(0, t[-1])
            time_axes[0].set_title(
                f"decim={decim} ({1000.0/decim:.3f} MS/s/ch)  "
                f"window={t[-1]:.0f} us  pkts={tap.pkts}")
            time_axes[-1].set_xlabel("time [us]")

            if args.fft:
                fs = 1e9 / decim
                freqs = np.fft.rfftfreq(args.window, d=1.0 / fs) / 1e6
                for chip, ln in enumerate(fft_lines):
                    x = snap[chip * 2].astype(np.float64)
                    x -= x.mean()
                    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
                    spec = 20 * np.log10(spec / (spec.max() or 1.0) + 1e-9)
                    ln.set_data(freqs, spec)
                    fft_axes[chip].set_xlim(0, freqs[-1])
                    fft_axes[chip].set_ylim(-90, 3)

            fig.tight_layout()
            plt.pause(1.0 / args.fps)
    except KeyboardInterrupt:
        pass
    finally:
        tap.close()


if __name__ == "__main__":
    main()
