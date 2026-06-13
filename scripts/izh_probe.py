#!/usr/bin/env python3
"""UART-only probe of the new BRAM-based IZH neuron config bank.

Reads the neuron debug word (dac_neuron_debug_reg = {0x1A, mask, busy,
global_set, v_out[0][17:0]}) via conv_sel=7 + rw_reg3[5:4]=3, and exercises the
NEUR/NSRC commands, to confirm:
  * the reader FSM ran (marker 0x1A present),
  * neuron 0 is integrating (v_out[0] changes over time = free-running),
  * NEUR programs the bank (mask reflects the channel, profile applied),
  * NSRC sets the GT-domain source field in rw_reg4[15:8].

  python scripts/izh_probe.py
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM10"


def cmd(s, c, wait=("OK", "RW", "RO", "DAC", "ERR", "Neuron"), timeout=2.0):
    s.reset_input_buffer()
    s.write((c + "\n").encode())
    s.flush()
    end = time.time() + timeout
    lines = []
    while time.time() < end:
        ln = s.readline().decode("ascii", "replace").strip()
        if ln:
            lines.append(ln)
            if ln.startswith(wait):
                break
    return lines


def read_ro3(s):
    """Return RO_REG3 as int, or None."""
    for ln in cmd(s, "RDRO 3", ("RO3", "RO", "0x")):
        if "0x" in ln:
            try:
                return int(ln.split("0x")[1][:8], 16)
            except ValueError:
                pass
    return None


def decode_dbg(v):
    if v is None:
        return "(no reply)"
    marker = (v >> 24) & 0xFF
    mask = (v >> 20) & 0xF
    busy = (v >> 19) & 1
    gset = (v >> 18) & 1
    vout = v & 0x3FFFF
    # sign-extend 18-bit
    if vout & 0x20000:
        vout -= 0x40000
    return (f"0x{v:08X}  marker={'0x1A OK' if marker==0x1A else hex(marker)+' BAD'} "
            f"mask=0x{mask:X} busy={busy} gset={gset} v_out[0]={vout}")


def main():
    s = serial.Serial(PORT, 115200, timeout=1, write_timeout=2)
    time.sleep(0.2)

    print("STAT:", " | ".join(cmd(s, "STAT", ("RW0", "DAQ"))[:1]) or "(no reply)")

    # select the neuron debug word on RO_REG3
    cmd(s, "WRTE 1 0x7")
    cmd(s, "WRTE 3 0x30")

    print("\n[1] neuron 0 free-running with defaults (v_out[0] should change):")
    seen = []
    for i in range(6):
        v = read_ro3(s)
        seen.append(v)
        print(f"   {decode_dbg(v)}")
        time.sleep(0.05)
    vouts = [(x & 0x3FFFF) for x in seen if x is not None]
    moving = len(set(vouts)) > 1
    print(f"   -> v_out[0] {'CHANGING (neuron integrating) OK' if moving else 'STATIC (neuron NOT running) FAIL'}")

    print("\n[2] NEUR all profile bursting:")
    for ln in cmd(s, "NEUR all profile bursting", ("OK", "ERR")):
        print("  ", ln)
    cmd(s, "WRTE 1 0x7"); cmd(s, "WRTE 3 0x30")
    print("   debug after:", decode_dbg(read_ro3(s)))

    print("\n[3] NEUR 0 c 0xFFC00000 (single-neuron param to ch0):")
    for ln in cmd(s, "NEUR 0 c 0xFFC00000", ("OK", "ERR")):
        print("  ", ln)

    print("\n[4] NSRC all izh (route neuron -> DAC, rw_reg4[15:8]):")
    for ln in cmd(s, "NSRC all izh", ("DAC", "ERR")):
        print("  ", ln)
    for ln in cmd(s, "RDRW 4", ("RW4", "0x")):
        if "0x" in ln:
            r4 = int(ln.split("0x")[1][:8], 16)
            print(f"   rw_reg4=0x{r4:08X}  source[15:8]=0x{(r4>>8)&0xFF:02X} "
                  f"(expect 0xFF = all neurons), prog_toggle[0]={r4 & 1}")

    s.close()


if __name__ == "__main__":
    main()
