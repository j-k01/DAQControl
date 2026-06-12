"""Passive capture of PS UART0 (COM9) boot/debug output to a file.

Usage: python com9_capture.py <outfile> <duration_s> [port]
"""
import serial
import sys
import time

out = sys.argv[1] if len(sys.argv) > 1 else "com9_boot.txt"
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 14.0
port = sys.argv[3] if len(sys.argv) > 3 else "COM9"

try:
    s = serial.Serial(port, 115200, timeout=0.5)
except Exception as e:  # noqa: BLE001
    with open(out, "w") as f:
        f.write("%s open error: %s\n" % (port, e))
    print("open error:", e)
    sys.exit(1)

buf = b""
t0 = time.time()
while time.time() - t0 < dur:
    buf += s.read(4096)
s.close()
with open(out, "wb") as f:
    f.write(buf)
print("captured %d bytes from %s" % (len(buf), port))
