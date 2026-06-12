#!/usr/bin/env python3
"""Decisive board-vs-host test: capture raw packets fast (NO per-packet work),
switch source mid-capture, then classify OFFLINE in seq order. If the seq-ordered
board stream is a clean BRAM->DDS transition, any flicker is host-side."""
import socket, struct, time, sys, threading
import numpy as np
sys.path.insert(0, "scripts")
import dac_scope_qt as d

BRAM_F, DDS_F, FS = 0.916, 0.244, 1e9/128.0

def main():
    dac = d.DacControl("COM10")
    dac.cmd("WRTE 2 0x01000018")
    dac.cmd("WRTE 3 0x%08X" % ((4096 & 0xFFFFFF) << 8))
    dac.prog(0, d.sine_words(BRAM_F, 0x5000))
    print(dac.cmd("STRM 128", ok=("OK STRM", "ERR")))
    dac.set_source(0, "BRAM")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 << 20)  # big: avoid host drop
    sock.bind(("192.168.2.1", 5005)); sock.settimeout(0.5)
    sock.sendto(b"STRM", ("192.168.2.10", 5006))
    time.sleep(0.6)

    raw = []                       # (seq, bytes) for chip0 only; store fast
    stop = {"f": False}
    def rx():
        while not stop["f"]:
            try: data,_ = sock.recvfrom(2048)
            except socket.timeout: continue
            except OSError: break
            if len(data) < 32: continue
            if data[12] == 0:      # chip field byte (offset 12), chip0
                raw.append(data)
    th = threading.Thread(target=rx, daemon=True); th.start()

    time.sleep(0.5)
    sw_time = time.time()
    dac.set_source(0, "DDS")
    sw_seq_marker = len(raw)
    time.sleep(2.5)
    stop["f"] = True; th.join(timeout=1)
    sock.sendto(b"STOP", ("192.168.2.10", 5006)); sock.close(); dac.close()

    # offline classify in seq order
    recs = []
    for data in raw:
        magic,_v,hdr,seq,chip,_o,count,drp,dec = d.HDR.unpack_from(data)
        if magic != d.MAGIC: continue
        payload = data[hdr:hdr+count]; payload = payload[:len(payload)-(len(payload)%16)]
        sm = np.frombuffer(payload, dtype="<i2").reshape(-1,8)
        v = sm[:,:4].ravel().astype(float); v -= v.mean()
        if v.std() < 5: cls="_"
        else:
            w=np.hanning(len(v)); Y=np.abs(np.fft.rfft(v*w)); f=np.fft.rfftfreq(len(v),1/FS)
            fm=f[1+np.argmax(Y[1:])]/1e6
            cls = "B" if abs(fm-BRAM_F)<0.18 else ("D" if abs(fm-DDS_F)<0.18 else "?")
        recs.append((seq, drp, cls))
    recs.sort()
    seqs=[r[0] for r in recs]
    gaps = sum(1 for i in range(1,len(seqs)) if seqs[i]!=seqs[i-1]+1)
    missing = (seqs[-1]-seqs[0]+1-len(seqs)) if seqs else 0
    cls="".join(r[2] for r in recs)
    rle=[]
    for c in cls:
        if rle and rle[-1][0]==c: rle[-1][1]+=1
        else: rle.append([c,1])
    print(f"chip0 pkts={len(recs)} seqspan={seqs[-1]-seqs[0]} host_missing={missing} ({100*missing/max(1,seqs[-1]-seqs[0]):.1f}%) board_drops={recs[-1][1]}")
    print("seq-ordered class RLE:")
    print("  "+" ".join(f"{c}x{n}" for c,n in rle))
    nflip=sum(1 for i in range(1,len(cls)) if cls[i]!=cls[i-1] and cls[i] in "BD" and cls[i-1] in "BD")
    last_b=next((i for i,c in enumerate(reversed(cls)) if c=="B"), None)
    print(f"B<->D flips={nflip}")

if __name__ == "__main__":
    main()
