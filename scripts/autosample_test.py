#!/usr/bin/env python3
"""Reproduce the GUI auto-sample: a tight loop of 64 KB BCAP+BRDO bursts with a
live source switch mid-loop, saving each capture so we can check for a stale
(non-refreshed) second half. Mirrors dac_scope_qt.py's _burst_collect."""
import sys
import time
import serial
import numpy as np

sys.path.insert(0, "scripts")
from burst_capture import Reassembler, decode_chip, parse_brdo_request, uart_cmd

PORT, BOARD, CMD, LOCAL, LPORT = "COM10", "192.168.2.10", 5006, "192.168.2.1", 5005
KB = 64
BPC = KB * 1024
N = 12
SWITCH_AT = 6


def cfg(s, c):
    s.reset_input_buffer()
    s.write((c + "\n").encode())
    s.flush()
    time.sleep(0.3)
    s.read_all()


def main():
    s = serial.Serial(PORT, 115200, timeout=5, write_timeout=5)
    time.sleep(0.2)
    cfg(s, "STRM STOP")
    cfg(s, "NSRC all dds")
    cfg(s, "DDSI 0x100000")
    for i in range(N):
        if i == SWITCH_AT:
            cfg(s, "DDSI 0x200000")          # live source change mid-loop
        asm = Reassembler(BOARD, CMD, LOCAL, LPORT, BPC)
        asm.register(timeout=2.0)
        bcap = uart_cmd(s, f"BCAP {KB}k", ("OK BCAP", "ERR"), timeout=10)
        brdo = uart_cmd(s, "BRDO", ("OK BRDO", "ERR"), timeout=10)
        req = parse_brdo_request(brdo)
        if req is not None:
            asm.set_request_id(req)
        dl = time.time() + 8
        while time.time() < dl and not asm.complete():
            time.sleep(0.02)
        comp = asm.complete()
        cov = (asm.coverage(0), asm.coverage(1))
        ch = {}
        ch.update(decode_chip(asm.buf[0], 0))
        ch.update(decode_chip(asm.buf[1], 2))
        np.save(f"captures/auto_{i:02d}.npy", np.stack([ch[c] for c in range(4)]))
        asm.close()
        tag = "OK" if bcap.startswith("OK") else bcap[:24]
        print(f"iter {i:2d}: cov={cov[0]*100:5.1f}/{cov[1]*100:5.1f}% complete={comp} bcap={tag}")
        time.sleep(0.2)
    cfg(s, "DDSI 0")
    s.close()


if __name__ == "__main__":
    main()
