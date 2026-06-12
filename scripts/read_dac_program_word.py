"""Dump the live DAC BRAM-player output words (program_word0..3) over UART.

selected_count -> RO3 (top.v). RDRO 7 returns dac_program_word_debug when
RW3[6]=1 (program_enable) and RW2[31]=1; RW2[30:28] selects which 32-bit half:
 0 ch0[31:0] 1 ch0[63:32] 2 ch1[31:0] 3 ch1[63:32]
 4 ch2[31:0] 5 ch2[63:32] 6 ch3[31:0] 7 ch3[63:32]
Each 32-bit half packs two chronological 16-bit DAC samples (lo, hi).

Run after DPWR + NSRC all bram. If the player is emitting the programmed sine,
the samples vary within +-amplitude across reads; all-zero = player dead;
full-range hash = corrupt read/CDC.
"""
import sys
import time

import serial

LABELS = ["ch0[31:0]", "ch0[63:32]", "ch1[31:0]", "ch1[63:32]",
          "ch2[31:0]", "ch2[63:32]", "ch3[31:0]", "ch3[63:32]"]


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def cmd(port, c):
    port.reset_input_buffer()
    port.write((c + "\n").encode("ascii"))
    port.flush()
    time.sleep(0.12)
    return port.read_all().decode("ascii", errors="replace")


def read_ro(port, n):
    r = cmd(port, "RDRO %d" % n)
    for line in r.splitlines():
        if ("RO%d" % n) in line and "0x" in line:
            return int(line.split("0x")[-1].strip()[:8], 16)
    return None


def main():
    port_name = sys.argv[1] if len(sys.argv) > 1 else "COM10"
    reads = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    with serial.Serial(port_name, 115200, timeout=2) as port:
        time.sleep(0.2)
        print("RW3 (want 0x..60: src=bram + program_enable):",
              cmd(port, "RDRO 7").strip()[:0] or "")
        for sub in range(8):
            rw2 = 0x81000018 | (sub << 28)
            cmd(port, "WRTE 2 0x%08X" % rw2)
            samples = []
            for _ in range(reads):
                v = read_ro(port, 7)
                if v is not None:
                    samples.append((s16(v & 0xFFFF), s16((v >> 16) & 0xFFFF)))
            print("%-11s RW2=0x%08X  (lo,hi) x%d: %s"
                  % (LABELS[sub], rw2, len(samples), samples))
        # restore a sane RW2
        cmd(port, "WRTE 2 0x01000018")


if __name__ == "__main__":
    main()
