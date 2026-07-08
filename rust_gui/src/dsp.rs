//! Pure DSP + protocol constants, ported from scripts/dac_scope_qt.py.
//! No I/O here so it stays trivially testable.

pub const VOLTS_PER_COUNT: f64 = 1.9 / 65536.0;
pub const DAC_FULLSCALE: i32 = 32767;
pub const PROGRAM_SAMPLES: usize = 16384;
pub const BRAM_FRAME_SAMPLES: usize = 4;

pub const DDS_SAMPLE_RATE_HZ: f64 = 1.0e9;
pub const DDS_PHASE_BITS: u32 = 24;

/// 16:4 DAC crossbar sources, in the fixed display order. `.1` is the firmware
/// NSRC token, `.0` is the human label.
pub const SOURCES: [(&str, &str); 20] = [
    ("Off", "off"),
    ("DDS", "dds"),
    ("BRAM 0", "bram0"),
    ("BRAM 1", "bram1"),
    ("BRAM 2", "bram2"),
    ("BRAM 3", "bram3"),
    ("Spike 0", "spike0"),
    ("Spike 1", "spike1"),
    ("Spike 2", "spike2"),
    ("Spike 3", "spike3"),
    ("Monitor 0", "mon0"),
    ("Monitor 1", "mon1"),
    ("Monitor 2", "mon2"),
    ("Monitor 3", "mon3"),
    ("Current source", "current"),
    ("Tag", "tag"),
    // conductance-neuron spikes (crossbar codes 16-19)
    ("Cond 0", "cspike0"),
    ("Cond 1", "cspike1"),
    ("Cond 2", "cspike2"),
    ("Cond 3", "cspike3"),
];

pub fn source_label(idx: usize) -> &'static str {
    SOURCES.get(idx).map(|s| s.0).unwrap_or("?")
}
pub fn source_token(idx: usize) -> &'static str {
    SOURCES.get(idx).map(|s| s.1).unwrap_or("off")
}
pub fn source_is_neuron(idx: usize) -> bool {
    let l = source_label(idx);
    l.starts_with("Spike ") || l.starts_with("Monitor ") || l.starts_with("Cond ")
}
/// neuron index named by a Spike/Monitor source, if any.
pub fn source_neuron_idx(idx: usize) -> Option<u8> {
    if source_is_neuron(idx) {
        source_label(idx)
            .rsplit(' ')
            .next()
            .and_then(|t| t.parse::<u8>().ok())
    } else {
        None
    }
}

pub const BUILTIN_PROFILES: [&str; 4] = ["regular", "bursting", "chattering", "fast"];

/// (param, label, lo, hi, default, decimals)
pub const NEURON_PARAMS: [(&str, &str, f64, f64, f64, usize); 5] = [
    ("a", "a  recovery rate", 0.0, 0.5, 0.02, 3),
    ("b", "b  sensitivity", 0.0, 0.5, 0.20, 2),
    ("c", "c  reset v (mV)", -90.0, -40.0, -65.0, 1),
    ("d", "d  reset u", 0.0, 15.0, 8.0, 2),
    ("iconst", "I  drive", 0.0, 40.0, 10.0, 1),
];

pub fn builtin_profile_values(name: &str) -> Option<[(&'static str, f64); 5]> {
    let v = match name {
        "regular" => [0.02, 0.20, -65.0, 8.0, 10.0],
        "bursting" => [0.02, 0.20, -55.0, 4.0, 10.0],
        "chattering" => [0.02, 0.20, -50.0, 2.0, 10.0],
        "fast" => [0.10, 0.20, -65.0, 2.0, 10.0],
        _ => return None,
    };
    Some([
        ("a", v[0]),
        ("b", v[1]),
        ("c", v[2]),
        ("d", v[3]),
        ("iconst", v[4]),
    ])
}

pub const WAVEFORMS: [&str; 5] = ["Sine", "Triangle", "Trapezoid", "Square", "Sawtooth"];

pub const CAPT_FRAME_OPTIONS: [u32; 6] = [128, 256, 512, 1024, 2048, 4096];

/// "Collect Ethernet" burst sizes (bytes/chip, label).
pub const COLLECT_SIZES: [(usize, &str); 7] = [
    (64 * 1024, "64 KB (16k/ch)"),
    (128 * 1024, "128 KB (32k/ch)"),
    (256 * 1024, "256 KB (64k/ch)"),
    (512 * 1024, "512 KB (128k/ch)"),
    (1 << 20, "1 MB (256k/ch)"),
    (4 << 20, "4 MB (1M/ch)"),
    (16 << 20, "16 MB (4M/ch)"),
];

pub const CH_COLORS: [(u8, u8, u8); 4] = [
    (0x4F, 0xC3, 0xF7),
    (0x81, 0xC7, 0x84),
    (0xFF, 0xB7, 0x4D),
    (0xE5, 0x73, 0x73),
];

// ---------------------------------------------------------------- conversions
/// Physical Izhikevich value -> signed Q16.16 packed as a 32-bit word.
pub fn izh_to_q16(v: f64) -> u32 {
    (v * 65536.0).round() as i64 as u32
}

/// DDS frequency (Hz) -> 24-bit phase increment (0..0xFFFFFF).
pub fn dds_freq_to_inc(freq_hz: f64) -> u32 {
    let inc = (freq_hz / DDS_SAMPLE_RATE_HZ * (1u64 << DDS_PHASE_BITS) as f64).round() as i64;
    inc.clamp(0, 0x00FF_FFFF) as u32
}

pub fn dds_inc_to_freq(inc: u32) -> f64 {
    (inc & 0x00FF_FFFF) as f64 / (1u64 << DDS_PHASE_BITS) as f64 * DDS_SAMPLE_RATE_HZ
}

pub fn clamp_s16(v: f64) -> i32 {
    (v.round() as i32).clamp(-DAC_FULLSCALE, DAC_FULLSCALE)
}

pub fn volts_to_counts(v: f64) -> i32 {
    clamp_s16(v / VOLTS_PER_COUNT)
}

// ------------------------------------------------------------- interleave fix
/// Remove the ADS54J60 mod-4 interleave baseline: subtract each phase's mean.
/// Full-rate data only. Returns counts as f64 (display-time; input untouched).
pub fn deinterleave_baseline(x: &[i16]) -> Vec<f64> {
    let mut y: Vec<f64> = x.iter().map(|&v| v as f64).collect();
    for k in 0..4 {
        let idx: Vec<usize> = (k..y.len()).step_by(4).collect();
        if idx.is_empty() {
            continue;
        }
        let mean: f64 = idx.iter().map(|&i| y[i]).sum::<f64>() / idx.len() as f64;
        for &i in &idx {
            y[i] -= mean;
        }
    }
    y
}

// ------------------------------------------------------------- BRAM waveforms
fn pack_pair(s0: i32, s1: i32) -> u32 {
    (((s1 as u32) & 0xFFFF) << 16) | ((s0 as u32) & 0xFFFF)
}

/// Build a seamless BRAM loop and its RW3[31:8] frame count for one shape.
/// Returns (packed u32 words, loop_frames).
pub fn gen_waveform(kind: &str, period_ns: usize, width_ns: usize, vlo: f64, vhi: f64) -> (Vec<u32>, u32) {
    let period = period_ns.clamp(2, PROGRAM_SAMPLES);
    let width = width_ns.clamp(1, period);
    let mut shape = vec![0.0f64; period];
    for i in 0..period {
        let fi = i as f64;
        let fp = period as f64;
        shape[i] = match kind {
            "Sine" => 0.5 * (1.0 + (2.0 * std::f64::consts::PI * fi / fp).sin()),
            "Triangle" => 1.0 - (2.0 * fi / fp - 1.0).abs(),
            "Sawtooth" => fi / fp,
            "Square" => {
                if i < width {
                    1.0
                } else {
                    0.0
                }
            }
            "Trapezoid" => {
                let rise = ((period - width) / 2).max(1);
                if i < rise {
                    i as f64 / rise as f64
                } else if i < rise + width {
                    1.0
                } else {
                    let fe = (rise + width + rise).min(period);
                    if i < fe {
                        1.0 - (i - (rise + width)) as f64 / rise as f64
                    } else {
                        0.0
                    }
                }
            }
            _ => 0.0,
        };
    }
    let lo = volts_to_counts(vlo) as f64;
    let hi = volts_to_counts(vhi) as f64;
    let one: Vec<i32> = shape
        .iter()
        .map(|s| clamp_s16(lo + s * (hi - lo)))
        .collect();

    // tile the largest whole number of periods landing on a 4-sample frame
    let mut reps = (PROGRAM_SAMPLES / period).max(1);
    let mut loop_len = reps * period;
    while reps > 1 && (loop_len % BRAM_FRAME_SAMPLES) != 0 {
        reps -= 1;
        loop_len = reps * period;
    }
    loop_len -= loop_len % BRAM_FRAME_SAMPLES;
    if loop_len < BRAM_FRAME_SAMPLES {
        loop_len = BRAM_FRAME_SAMPLES;
    }
    let samples: Vec<i32> = (0..loop_len).map(|i| one[i % one.len()]).collect();
    let words: Vec<u32> = (0..loop_len / 2)
        .map(|k| pack_pair(samples[2 * k], samples[2 * k + 1]))
        .collect();
    (words, (loop_len / BRAM_FRAME_SAMPLES) as u32)
}

// ------------------------------------------------------------------- display
/// Largest power of two <= n.
fn pow2_le(n: usize) -> usize {
    if n == 0 {
        return 0;
    }
    let mut p = 1;
    while p * 2 <= n {
        p *= 2;
    }
    p
}

/// In-place iterative radix-2 Cooley-Tukey FFT (re, im).
fn fft(re: &mut [f64], im: &mut [f64]) {
    let n = re.len();
    if n < 2 {
        return;
    }
    // bit reversal
    let mut j = 0usize;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if i < j {
            re.swap(i, j);
            im.swap(i, j);
        }
    }
    let mut len = 2;
    while len <= n {
        let ang = -2.0 * std::f64::consts::PI / len as f64;
        let (wr, wi) = (ang.cos(), ang.sin());
        let mut i = 0;
        while i < n {
            let (mut cr, mut ci) = (1.0f64, 0.0f64);
            for k in 0..len / 2 {
                let a = i + k;
                let b = i + k + len / 2;
                let tr = cr * re[b] - ci * im[b];
                let ti = cr * im[b] + ci * re[b];
                re[b] = re[a] - tr;
                im[b] = im[a] - ti;
                re[a] += tr;
                im[a] += ti;
                let ncr = cr * wr - ci * wi;
                ci = cr * wi + ci * wr;
                cr = ncr;
            }
            i += len;
        }
        len <<= 1;
    }
}

/// Counts (f64) -> (frequency Hz, magnitude dBFS) points, Hann-windowed.
pub fn magnitude_db(counts: &[f64], fs: f64) -> Vec<[f64; 2]> {
    let n = pow2_le(counts.len());
    if n < 2 {
        return vec![];
    }
    let start = counts.len() - n;
    let mean: f64 = counts[start..].iter().sum::<f64>() / n as f64;
    let mut re = vec![0.0; n];
    let mut im = vec![0.0; n];
    let mut wsum = 0.0;
    for i in 0..n {
        let w = 0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / (n as f64 - 1.0)).cos();
        wsum += w;
        re[i] = (counts[start + i] - mean) * w;
    }
    fft(&mut re, &mut im);
    let scale = 2.0 / wsum;
    let full = (DAC_FULLSCALE as f64) * VOLTS_PER_COUNT; // full-scale volts
    (0..n / 2)
        .map(|k| {
            let mag = (re[k] * re[k] + im[k] * im[k]).sqrt() * scale * VOLTS_PER_COUNT;
            let dbfs = 20.0 * (mag / full).max(1e-9).log10();
            [k as f64 * fs / n as f64, dbfs]
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dds_roundtrip() {
        assert_eq!(dds_freq_to_inc(62.5e6), 0x100000);
        assert_eq!(dds_freq_to_inc(125e6), 0x200000);
        assert_eq!(dds_freq_to_inc(1e12), 0x00FF_FFFF); // clamp
        assert!((dds_inc_to_freq(0x100000) - 62.5e6).abs() < 1.0);
    }

    #[test]
    fn q16() {
        assert_eq!(izh_to_q16(1.0), 0x10000);
        assert_eq!(izh_to_q16(0.02), (0.02f64 * 65536.0).round() as u32);
    }

    #[test]
    fn deinterleave_removes_square_keeps_length() {
        let core = [7.0, -7.0, 6.5, -6.6];
        let n = 4096usize;
        let x: Vec<i16> = (0..n)
            .map(|i| (core[i % 4] / (VOLTS_PER_COUNT * 1e3)) as i16)
            .collect();
        let y = deinterleave_baseline(&x);
        assert_eq!(y.len(), n);
        // each phase mean must now be ~0
        for k in 0..4 {
            let idx: Vec<usize> = (k..n).step_by(4).collect();
            let m: f64 = idx.iter().map(|&i| y[i]).sum::<f64>() / idx.len() as f64;
            assert!(m.abs() < 1e-6, "phase {k} mean {m}");
        }
    }

    #[test]
    fn waveform_frame_aligned() {
        let (words, frames) = gen_waveform("Sine", 35, 7, 0.0, 0.9);
        assert!(!words.is_empty());
        assert!(frames >= 1);
        // loop length (2 samples/word) must be a multiple of 4 samples
        assert_eq!((words.len() * 2) % BRAM_FRAME_SAMPLES, 0);
    }
}
