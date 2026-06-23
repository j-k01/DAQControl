#!/usr/bin/env python3
"""Diagnose source-switch flicker: classify each raw UDP packet's content.

Loads ch0 BRAM at a distinct tone, DDS at a different tone, starts streaming,
then switches ch0 BRAM->DDS and records every chip-0 packet's (seq, dominant
freq) so we can see whether the *raw stream* alternates old/new (a board issue)
or transitions cleanly (host/display). No GUI, no StreamTap.
"""
import socket, struct, time, sys
import numpy as np
sys.path.insert(0, "scripts")
import dac_scope_qt as d

BRAM_F = 0.916   # MHz, ch0 BRAM tone
DDS_STEP = 4096  # ~0.244 MHz DDS tone
DDS_F = 0.244
FS = 1e9 / 128.0

def classify(ch0):
    v = ch0.astype(float)
    v = v - v.mean()
    if v.std() < 5:
        return "_", 0.0
    w = np.hanning(len(v))
    Y = np.abs(np.fft.rfft(v * w))
    f = np.fft.rfftfreq(len(v), 1.0 / FS)
    fm = f[1 + np.argmax(Y[1:])] / 1e6
    if abs(fm - BRAM_F) < 0.18:
        return "B", fm
    if abs(fm - DDS_F) < 0.18:
        return "D", fm
    return "?", fm

def main():
    dac = d.DacControl("COM10")
    dac.cmd("WRTE 2 0x01000018")
    dac.cmd("DDSI 0x%06X" % (DDS_STEP & 0xFFFFFF))
    dac.prog(0, d.sine_words(BRAM_F, 0x5000))
    print("STRM:", dac.cmd("STRM 128", ok=("OK STRM", "ERR")))
    dac.set_source(0, "BRAM")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind(("192.168.2.1", 5005))
    sock.settimeout(0.5)
    sock.sendto(b"STRM", ("192.168.2.10", 5006))

    def grab(duration, tag):
        recs = []
        t0 = time.time()
        while time.time() - t0 < duration:
            try:
                data, _ = sock.recvfrom(8192)
            except socket.timeout:
                continue
            if len(data) < d.HDR.size:
                continue
            magic, _v, hdr, seq, chip, _o, count, _dr, dec = d.HDR.unpack_from(data)
            if magic != d.MAGIC or chip != 0:
                continue
            payload = data[hdr:hdr + count]
            payload = payload[: len(payload) - (len(payload) % 16)]
            sm = np.frombuffer(payload, dtype="<i2").reshape(-1, 8)
            cls, fm = classify(sm[:, :4].ravel())
            recs.append((seq, round(time.time() - t0, 3), cls, round(fm, 3)))
        return recs

    print("settling on BRAM...")
    time.sleep(0.8)
    base = grab(0.6, "base")
    bc = "".join(r[2] for r in sorted(base))
    print(f"baseline ({len(base)} pkts): {bc[:80]}  -> {'all BRAM' if set(bc)<=set('B') else 'MIXED:'+bc[:40]}")

    print(">>> switching ch0 BRAM -> DDS")
    dac.set_source(0, "DDS")
    recs = grab(4.0, "switch")
    sock.sendto(b"STOP", ("192.168.2.10", 5006))
    sock.close()
    dac.close()

    recs.sort()
    seq_first = recs[0][0] if recs else 0
    cls = "".join(r[2] for r in recs)
    # run-length encode
    rle = []
    for c in cls:
        if rle and rle[-1][0] == c:
            rle[-1][1] += 1
        else:
            rle.append([c, 1])
    print(f"\npost-switch: {len(recs)} chip0 pkts, seq {seq_first}..{recs[-1][0]}")
    print("class stream (seq order), run-length encoded:")
    print("  " + " ".join(f"{c}x{n}" for c, n in rle))
    # time of last B (old data) after switch
    last_b = max((r[1] for r in recs if r[2] == "B"), default=None)
    first_d = min((r[1] for r in recs if r[2] == "D"), default=None)
    nflip = sum(1 for i in range(1, len(cls)) if cls[i] != cls[i-1] and cls[i] in "BD" and cls[i-1] in "BD")
    print(f"\nfirst DDS at t={first_d}s, last BRAM at t={last_b}s, B<->D flips={nflip}")
    if nflip > 3:
        print("=> ALTERNATION confirmed in the raw stream (board-side).")
    elif last_b is not None and first_d is not None and last_b > first_d + 0.05:
        print("=> old data persists AFTER new appears (board ring lapping).")
    else:
        print("=> clean single transition (latency only, no flicker in data).")

if __name__ == "__main__":
    main()
