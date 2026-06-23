#!/usr/bin/env python3
"""Print the burst->S2MM AXIS handshake sequence around the ILA trigger.
Usage: python scripts/show_ila_handshake.py <ila_burst.csv> [window]"""
import csv, sys

path = sys.argv[1] if len(sys.argv) > 1 else "ila_burst.csv"
win = int(sys.argv[2]) if len(sys.argv) > 2 else 24
rows = list(csv.reader(open(path)))
hi = next(i for i, r in enumerate(rows) if any("Sample in Buffer" in c for c in r))
hdr = [c.strip() for c in rows[hi]]
data = [r for r in rows[hi + 1:] if len(r) == len(hdr)]


def col(name):
    return next(i for i, c in enumerate(hdr) if name in c)

idx = {k: col(k) for k in ["TRIGGER", "Sample in Window",
                            "adc0_dma_axis_tvalid", "adc0_dma_axis_tready", "adc0_dma_axis_tlast",
                            "adc1_dma_axis_tvalid", "adc1_dma_axis_tready", "adc1_dma_axis_tlast"]}

# drop a possible radix row (non 0/1 values)
def is01(v):
    return v.strip() in ("0", "1")
data = [r for r in data if is01(r[idx["adc0_dma_axis_tvalid"]])]

# trigger row: TRIGGER==1, else first adc0 or adc1 tlast==1
ti = None
for i, r in enumerate(data):
    if r[idx["TRIGGER"]].strip() in ("1", "TRUE"):
        ti = i; break
if ti is None:
    for i, r in enumerate(data):
        if r[idx["adc0_dma_axis_tlast"]].strip() == "1" or r[idx["adc1_dma_axis_tlast"]].strip() == "1":
            ti = i; break
print(f"{len(data)} data samples; trigger at index {ti}")
print("idx   | a0 v r l | a1 v r l   (v=tvalid r=tready l=tlast)")
lo, hi2 = max(0, ti - win), min(len(data), ti + win)
for i in range(lo, hi2):
    r = data[i]
    a0 = (r[idx["adc0_dma_axis_tvalid"]], r[idx["adc0_dma_axis_tready"]], r[idx["adc0_dma_axis_tlast"]])
    a1 = (r[idx["adc1_dma_axis_tvalid"]], r[idx["adc1_dma_axis_tready"]], r[idx["adc1_dma_axis_tlast"]])
    mark = "  <-- TRIGGER" if i == ti else ""
    print(f"{i:5d} |  {a0[0]} {a0[1]} {a0[2]}  |  {a1[0]} {a1[1]} {a1[2]}{mark}")

# summary: count handshakes and find where tvalid goes 1->0 for the last time
def beats(ch):
    return sum(1 for r in data if r[idx[f'{ch}_dma_axis_tvalid']].strip() == '1'
               and r[idx[f'{ch}_dma_axis_tready']].strip() == '1')
def tlast_hs(ch):
    return [(i, r) for i, r in enumerate(data)
            if r[idx[f'{ch}_dma_axis_tlast']].strip() == '1'
            and r[idx[f'{ch}_dma_axis_tvalid']].strip() == '1'
            and r[idx[f'{ch}_dma_axis_tready']].strip() == '1']
for ch in ("adc0", "adc1"):
    hs = tlast_hs(ch)
    print(f"{ch}: handshaked beats in window={beats(ch)}; tlast-handshake samples={[i for i,_ in hs]}")
