#!/usr/bin/env python3
"""Measure board-side stream throughput + drops at a given decimation."""
import socket, struct, time, sys
sys.path.insert(0, "scripts")
import dac_scope_qt as d

def run(decim):
    dac = d.DacControl("COM10")
    dac.cmd("WRTE 2 0x01000018")
    dac.prog(0, d.sine_words(0.916, 0x5000))
    dac.set_source(0, "BRAM")
    print(dac.cmd(f"STRM {decim}", ok=("OK STRM", "ERR")))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    sock.bind(("192.168.2.1", 5005)); sock.settimeout(0.5)
    sock.sendto(b"STRM", ("192.168.2.10", 5006))
    time.sleep(0.5)
    bytes_ch = [0, 0]; pk = [0, 0]; drops0 = [None, None]; dropsN = [0, 0]; seqlo=[None,None]; seqhi=[0,0]
    t0 = time.time()
    while time.time() - t0 < 3.0:
        try:
            data, _ = sock.recvfrom(8192)
        except socket.timeout:
            continue
        if len(data) < d.HDR.size: continue
        magic,_v,hdr,seq,chip,_o,count,drp,dec = d.HDR.unpack_from(data)
        if magic != d.MAGIC or chip > 1: continue
        bytes_ch[chip] += count; pk[chip] += 1
        if drops0[chip] is None: drops0[chip]=drp; seqlo[chip]=seq
        dropsN[chip]=drp; seqhi[chip]=seq
    sock.sendto(b"STOP", ("192.168.2.10", 5006)); sock.close(); dac.close()
    dt = time.time() - t0
    prod = 1e9/decim*2*2/1e6   # MB/s per chip produced (2 ch, 2 B)
    print(f"decim={decim}  produced={prod:.1f} MB/s/chip")
    for c in (0,1):
        got = bytes_ch[c]/dt/1e6
        bd = (dropsN[c]-drops0[c]) if drops0[c] is not None else 0
        nseq = (seqhi[c]-seqlo[c]) if seqlo[c] is not None else 0
        print(f"  chip{c}: recv={got:5.1f} MB/s  pkts={pk[c]}  board_drops_delta={bd}  seq_span={nseq}")

if __name__ == "__main__":
    for dec in (256, 128):
        run(dec); print()
