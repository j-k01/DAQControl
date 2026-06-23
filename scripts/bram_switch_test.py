#!/usr/bin/env python3
"""User-requested verification: program 4 DISTINCT per-channel BRAM tones, then
rotate the XBAR routing (DAC i -> BRAM (i+rot)%4) and resample over Ethernet,
checking EVERY quarter of EVERY channel matches the routed tone. Any mismatch =
stale or wrong data. Proves fresh, correct, per-channel data on every switch."""
import sys
import time
import serial
import numpy as np

sys.path.insert(0, "scripts")
import dac_bram_u32_uart as bram          # noqa: E402
from program_dac_dds_equivalent_bram import build_dds_words  # noqa: E402
from burst_capture import (Reassembler, decode_chip,         # noqa: E402
                           parse_brdo_request, uart_cmd)

PORT, BOARD, CMD, LOCAL, LPORT = "COM10", "192.168.2.10", 5006, "192.168.2.1", 5005
KB = 64
BPC = KB * 1024
FRAMES = 4095
FS = 1e9
TONES_MHZ = [40, 80, 120, 160]            # BRAM0..3, distinct


def step_for(mhz):
    return int(round(mhz * 1e6 / FS * (1 << 24))) & 0xFFFFFF


def cfg(s, c):
    s.reset_input_buffer()
    s.write((c + "\n").encode())
    s.flush()
    time.sleep(0.25)
    s.read_all()


def capture(s):
    asm = Reassembler(BOARD, CMD, LOCAL, LPORT, BPC)
    asm.register(timeout=2.0)
    uart_cmd(s, f"BCAP {KB}k", ("OK BCAP", "ERR"), timeout=10)
    brdo = uart_cmd(s, "BRDO", ("OK BRDO", "ERR"), timeout=10)
    asm.set_request_id(parse_brdo_request(brdo))
    dl = time.time() + 8
    while time.time() < dl and not asm.complete():
        time.sleep(0.02)
    ch = {}
    ch.update(decode_chip(asm.buf[0], 0))
    ch.update(decode_chip(asm.buf[1], 2))
    asm.close()
    return np.stack([ch[c] for c in range(4)])


def qfreq(x):
    x = x.astype(np.float64)
    x = x - x.mean()
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1.0 / FS)
    lo = np.searchsorted(f, 5e6)
    return f[lo + int(np.argmax(X[lo:]))] / 1e6


def main():
    s = serial.Serial(PORT, 115200, timeout=5, write_timeout=5)
    time.sleep(0.2)
    cfg(s, "STRM STOP")
    cfg(s, "WRTE 2 0x01000018")
    for ch in range(4):
        bram.write_words(s, ch, 0, build_dds_words(FRAMES, step_for(TONES_MHZ[ch])), False)
    cfg(s, f"WRTE 3 0x{((FRAMES << 8) & 0xFFFFFF00) | 0x68:08X}")
    cfg(s, f"WRTE 3 0x{((FRAMES << 8) & 0xFFFFFF00) | 0x60:08X}")
    print(f"BRAM tones (MHz): {TONES_MHZ}")
    ok = True
    for rot in range(6):
        for ch in range(4):
            cfg(s, f"NSRC {ch} bram{(ch + rot) % 4}")
        arr = capture(s)
        np.save(f"captures/bramx_{rot}.npy", arr)
        n = arr.shape[1]
        print(f"--- rotation {rot}: ch_i should show BRAM(i+{rot})%4 ---")
        for ch in range(4):
            exp = TONES_MHZ[(ch + rot) % 4]
            qf = [qfreq(arr[ch][q * n // 4:(q + 1) * n // 4]) for q in range(4)]
            good = all(abs(q - exp) < 12 for q in qf)
            ok = ok and good
            print(f"  ch{ch}: expect {exp:3d} MHz  quarters={[f'{q:5.1f}' for q in qf]}  "
                  f"{'OK' if good else '*** MISMATCH/STALE ***'}")
    cfg(s, "NSRC all dds")
    cfg(s, "DDSI 0")
    s.close()
    print("\nRESULT:", "ALL CORRECT (fresh, per-channel, no stale)"
          if ok else "*** MISMATCH -- stale or wrong routing ***")


if __name__ == "__main__":
    main()
