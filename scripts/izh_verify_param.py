#!/usr/bin/env python3
"""Decisive check that NEUR params actually reach the neuron core.

Reads the neuron-0 debug word (v_out[0]) via conv_sel=7 + rw_reg3[5:4]=3 while
toggling the drive parameter `iconst`:

  * iconst = 0   -> with regular-spiking a/b/c/d the neuron sits at REST,
                    so v_out is (nearly) CONSTANT across samples.
  * iconst = 10  -> the neuron fires tonically, so v_out sweeps c..+30 and the
                    sampled spread is LARGE.

If the spread tracks iconst (small -> large -> small), the param is landing in
the right register (reader address-decode fix works). If v_out always varies
regardless of iconst, the param is NOT reaching neuron 0 (the old bug, where
iconst decoded into the wrong slot and neuron 0 kept its default drive).

  python scripts/izh_verify_param.py
"""
from __future__ import annotations
import sys, time
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM10"


def cmd(s, c, wait, timeout=2.0):
    s.reset_input_buffer()
    s.write((c + "\n").encode())
    s.flush()
    end = time.time() + timeout
    while time.time() < end:
        ln = s.readline().decode("ascii", "replace").strip()
        if ln.startswith(wait):
            return ln
    return ""


def read_vout(s):
    """RO_REG3 -> v_out[0] as a signed value (low 18 bits, sign-extended)."""
    ln = cmd(s, "RDRO 3", ("RO", "0x"))
    if "0x" not in ln:
        return None
    v = int(ln.split("0x")[1][:8], 16)
    if (v >> 24) != 0x1A:           # marker check
        return None
    vout = v & 0x3FFFF
    return vout - 0x40000 if (vout & 0x20000) else vout


def sample_spread(s, n=30):
    vals = []
    for _ in range(n):
        x = read_vout(s)
        if x is not None:
            vals.append(x)
        time.sleep(0.01)
    if not vals:
        return None
    return min(vals), max(vals), max(vals) - min(vals), len(set(vals))


def main():
    s = serial.Serial(PORT, 115200, timeout=1, write_timeout=2)
    time.sleep(0.2)
    cmd(s, "WRTE 1 0x7", ("OK", "RW"))      # conv_sel = 7 -> neuron debug
    cmd(s, "WRTE 3 0x30", ("OK", "RW"))     # rw_reg3[5:4]=3 selects v_out word

    print("Toggling NEUR all iconst and watching neuron-0 v_out spread:")
    print("(rest = small spread; spiking = large spread)\n")
    for label, q in [("iconst=0  (rest)",   0x00000000),
                     ("iconst=10 (spiking)", 0x000A0000),
                     ("iconst=0  (rest)",   0x00000000),
                     ("iconst=15 (spiking)", 0x000F0000)]:
        r = cmd(s, f"NEUR all iconst 0x{q:08X}", ("OK", "ERR"))
        time.sleep(0.2)
        res = sample_spread(s)
        if res is None:
            print(f"  {label:22s}: no v_out (marker/comm fail)")
            continue
        lo, hi, spread, uniq = res
        verdict = "VARIES (spiking)" if spread > 2000 else "~constant (rest)"
        print(f"  {label:22s}: v_out {lo:+7d}..{hi:+7d}  spread={spread:6d} "
              f"uniq={uniq:2d}  -> {verdict}   [{r}]")

    print("\nIf spread is small for iconst=0 and large for iconst=10/15, the "
          "param reaches neuron 0 -> reader decode fix CONFIRMED.")
    s.close()


if __name__ == "__main__":
    main()
