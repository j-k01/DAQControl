"""Quick loopback health check on a PS-eth chip0 capture: dominant tone, SFDR, ENOB-ish."""
import struct
import sys

import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "captures/eth/eth_first_light_chip0.bin"
fs = float(sys.argv[2]) if len(sys.argv) > 2 else 1e9


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


d = open(path, "rb").read()
w = struct.unpack("<%dI" % (len(d) // 4), d)
ch0 = []
for i in range(0, len(w), 4):
    lo, hi = w[i], w[i + 1]
    ch0 += [s16(lo & 0xFFFF), s16((lo >> 16) & 0xFFFF),
            s16(hi & 0xFFFF), s16((hi >> 16) & 0xFFFF)]

x = np.asarray(ch0, float)
x -= x.mean()
n = len(x)
sp = np.abs(np.fft.rfft(x * np.hanning(n)))
f = np.fft.rfftfreq(n, 1 / fs)
k = int(sp.argmax())
peak_mhz = f[k] / 1e6

order = np.argsort(sp)[::-1]
spur = next(idx for idx in order[1:] if abs(idx - k) > 5)
sfdr = 20 * np.log10(sp[k] / sp[spur])

print("ADC0 samples: %d  (%.1f us at %.0f MS/s)" % (n, n / fs * 1e6, fs / 1e6))
print("dominant tone: %.3f MHz" % peak_mhz)
print("SFDR (largest spur): %.1f dB at %.3f MHz" % (sfdr, f[spur] / 1e6))
print("amplitude: pkpk=%d counts (~%.3f Vpp of 1.9 Vpp FS)"
      % (int(x.max() - x.min()), (x.max() - x.min()) / 65536.0 * 1.9))
print("first 20 samples:", ch0[:20])
