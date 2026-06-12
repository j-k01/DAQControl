#!/usr/bin/env python3
"""Decisive board-vs-host test: capture raw packets fast (NO per-packet work),
switch source mid-capture, then REASSEMBLE the seq-ordered sample stream and
track the dominant frequency over a continuous timeline. Reassembly avoids the
small-catch-up-packet artifact that per-packet FFT classification suffers."""
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
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 << 20)
    sock.bind(("192.168.2.1", 5005)); sock.settimeout(0.5)
    sock.sendto(b"STRM", ("192.168.2.10", 5006))
    time.sleep(0.6)

    raw = []; stop = {"f": False}
    def rx():
        while not stop["f"]:
            try: data,_ = sock.recvfrom(2048)
            except socket.timeout: continue
            except OSError: break
            if len(data) >= 32 and data[12] == 0:   # chip0
                raw.append(data)
    th = threading.Thread(target=rx, daemon=True); th.start()

    time.sleep(0.5)
    dac.set_source(0, "DDS")
    sw_idx = len(raw)
    time.sleep(2.0)
    stop["f"] = True; th.join(timeout=1)
    sock.sendto(b"STOP", ("192.168.2.10", 5006)); sock.close(); dac.close()

    # reassemble ch0 samples in seq order
    recs = []
    for data in raw:
        magic,_v,hdr,seq,chip,_o,count,drp,dec = d.HDR.unpack_from(data)
        if magic != d.MAGIC: continue
        payload = data[hdr:hdr+count]; payload = payload[:len(payload)-(len(payload)%16)]
        sm = np.frombuffer(payload, dtype="<i2").reshape(-1,8)
        recs.append((seq, drp, sm[:,:4].ravel()))
    recs.sort(key=lambda r: r[0])
    seqs=[r[0] for r in recs]
    missing=(seqs[-1]-seqs[0]+1-len(seqs)) if seqs else 0
    samples=np.concatenate([r[2] for r in recs]).astype(float)
    board_drops=recs[-1][1]
    print(f"chip0 pkts={len(recs)} host_missing={missing} ({100*missing/max(1,seqs[-1]-seqs[0]):.2f}%) board_drops={board_drops} total_samples={len(samples)}")

    # sliding-window dominant freq across the reassembled timeline
    win=4096; hop=2048
    tl=[]
    for i in range(0, len(samples)-win, hop):
        v=samples[i:i+win]-samples[i:i+win].mean()
        if v.std()<5: tl.append("_"); continue
        w=np.hanning(win); Y=np.abs(np.fft.rfft(v*w)); f=np.fft.rfftfreq(win,1/FS)
        fm=f[1+np.argmax(Y[1:])]/1e6
        tl.append("B" if abs(fm-BRAM_F)<0.12 else ("D" if abs(fm-DDS_F)<0.12 else "?"))
    s="".join(tl)
    rle=[]
    for c in s:
        if rle and rle[-1][0]==c: rle[-1][1]+=1
        else: rle.append([c,1])
    print("reassembled dominant-freq timeline (each cell ~%.0f us):" % (hop/FS*1e6))
    print("  "+" ".join(f"{c}x{n}" for c,n in rle))
    flips=sum(1 for i in range(1,len(s)) if s[i]!=s[i-1] and s[i] in "BD" and s[i-1] in "BD")
    # find clean transition: last B index, first D index (ignoring _ and ?)
    bd=[c for c in s if c in "BD"]
    print(f"B<->D flips (true content alternation)={flips}")
    if flips<=1:
        print("=> CLEAN single transition. Switch flicker resolved.")
    else:
        print("=> still alternating in the reassembled stream.")

if __name__ == "__main__":
    main()
