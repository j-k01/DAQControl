//! Ensemble de-noising DSP — the Rust port of `scripts/denoise_burst.py`.
//! All routines operate on `f64` ADC counts; the caller scales to volts for display.

/// 1.9 Vpp full-scale over a 16-bit ADC (matches the Qt GUI / dsp constant).
pub const VOLTS_PER_COUNT: f64 = 1.9 / 65536.0;

#[derive(Clone, Copy, PartialEq)]
pub enum Method {
    Mean,
    Median,
    Trimmed,
    Outlier,
}

impl Method {
    pub const ALL: [Method; 4] = [Method::Mean, Method::Median, Method::Trimmed, Method::Outlier];
    pub fn label(self) -> &'static str {
        match self {
            Method::Mean => "Coherent mean",
            Method::Median => "Median",
            Method::Trimmed => "Trimmed mean",
            Method::Outlier => "Outlier-reject mean",
        }
    }
}

// ---------------------------------------------------------------------------
// Per-sample reductions across the capture stack
// ---------------------------------------------------------------------------

pub fn mean_trace(stack: &[Vec<f64>]) -> Vec<f64> {
    let n = stack.len().max(1);
    let len = stack.first().map_or(0, |c| c.len());
    let mut out = vec![0.0; len];
    for cap in stack {
        for (o, &v) in out.iter_mut().zip(cap) {
            *o += v;
        }
    }
    out.iter_mut().for_each(|o| *o /= n as f64);
    out
}

pub fn median_trace(stack: &[Vec<f64>]) -> Vec<f64> {
    let n = stack.len();
    let len = stack.first().map_or(0, |c| c.len());
    let mut out = vec![0.0; len];
    let mut col = vec![0.0; n];
    for j in 0..len {
        for (i, cap) in stack.iter().enumerate() {
            col[i] = cap[j];
        }
        col.sort_by(|a, b| a.partial_cmp(b).unwrap());
        out[j] = if n % 2 == 1 {
            col[n / 2]
        } else {
            0.5 * (col[n / 2 - 1] + col[n / 2])
        };
    }
    out
}

// ---------------------------------------------------------------------------
// Sub-sample alignment (cross-correlation to the median reference)
// ---------------------------------------------------------------------------

/// Shift `x` by fractional `s` samples (output[i] = x[i - s]) via linear interp,
/// clamping at the edges.
pub fn shift(x: &[f64], s: f64) -> Vec<f64> {
    let n = x.len();
    if n == 0 {
        return Vec::new();
    }
    (0..n)
        .map(|i| {
            let src = i as f64 - s;
            if src <= 0.0 {
                x[0]
            } else if src >= (n - 1) as f64 {
                x[n - 1]
            } else {
                let i0 = src.floor() as usize;
                let f = src - i0 as f64;
                x[i0] * (1.0 - f) + x[i0 + 1] * f
            }
        })
        .collect()
}

/// Pre-transformed reference for repeated FFT cross-correlations against one
/// trace: correlating N captures against the same reference costs one forward
/// FFT of the reference plus 2 FFTs per capture, instead of the O(N*maxlag*len)
/// brute-force sum (which froze the UI for seconds on long burst captures).
struct XcorrRef {
    m: usize,
    re: Vec<f64>,
    im: Vec<f64>,
}

impl XcorrRef {
    fn new(b: &[f64]) -> XcorrRef {
        let m = (2 * b.len().max(1)).next_power_of_two();
        let mut re = vec![0.0; m];
        let mut im = vec![0.0; m];
        re[..b.len()].copy_from_slice(b);
        fft(&mut re, &mut im, false);
        XcorrRef { m, re, im }
    }

    /// c[lag] = sum_j a[j] * b[j - lag], for every circular lag; negative lags
    /// live at index m+lag. Zero-padding to m >= 2*len makes each value equal
    /// to the plain valid-overlap sum.
    fn corr(&self, a: &[f64]) -> Vec<f64> {
        let mut re = vec![0.0; self.m];
        let mut im = vec![0.0; self.m];
        re[..a.len()].copy_from_slice(a);
        fft(&mut re, &mut im, false);
        for k in 0..self.m {
            // A * conj(B)
            let (ar, ai) = (re[k], im[k]);
            re[k] = ar * self.re[k] + ai * self.im[k];
            im[k] = ai * self.re[k] - ar * self.im[k];
        }
        fft(&mut re, &mut im, true);
        re
    }
}

/// Align every capture to a common reference. Two passes: first against the
/// (robust) median of the raw stack, then against the mean of the aligned
/// stack -- the cleaner second reference recovers shifts a noisy first-pass
/// reference can miss. Returns the aligned stack and the per-capture offset
/// (in samples) that was removed.
pub fn align(stack: &[Vec<f64>], subsample: bool) -> (Vec<Vec<f64>>, Vec<f64>) {
    let len = stack.first().map_or(0, |c| c.len());
    if len == 0 || stack.len() < 2 {
        return (stack.to_vec(), vec![0.0; stack.len()]);
    }
    let maxlag = (len / 8).max(1) as isize;
    let demean = |x: &[f64]| -> Vec<f64> {
        let m = x.iter().sum::<f64>() / x.len() as f64;
        x.iter().map(|v| v - m).collect()
    };

    let mut offsets = vec![0.0; stack.len()];
    let mut aligned: Vec<Vec<f64>> = stack.to_vec();
    let mut reference = median_trace(stack);
    for pass in 0..2 {
        let rf = XcorrRef::new(&demean(&reference));
        let idx = |lag: isize| lag.rem_euclid(rf.m as isize) as usize;
        for (i, cap) in stack.iter().enumerate() {
            let c = rf.corr(&demean(cap));
            let mut best = f64::NEG_INFINITY;
            let mut best_lag = 0isize;
            for lag in -maxlag..=maxlag {
                let v = c[idx(lag)];
                if v > best {
                    best = v;
                    best_lag = lag;
                }
            }
            let mut off = best_lag as f64;
            if subsample {
                let ym1 = c[idx(best_lag - 1)];
                let yp1 = c[idx(best_lag + 1)];
                let d = ym1 - 2.0 * best + yp1;
                if d.abs() > 1e-12 {
                    off += 0.5 * (ym1 - yp1) / d;
                }
            }
            // offsets are vs the current reference, recomputed from the
            // ORIGINAL capture each pass, so they stay absolute.
            offsets[i] = off;
            aligned[i] = shift(cap, -off);
        }
        if pass == 0 {
            reference = mean_trace(&aligned);
        }
    }
    (aligned, offsets)
}

// ---------------------------------------------------------------------------
// Ensemble estimators
// ---------------------------------------------------------------------------

/// Combine the aligned stack into one estimate. Returns (trace, captures_used).
pub fn ensemble(stack: &[Vec<f64>], method: Method, trim: f64, z: f64) -> (Vec<f64>, usize) {
    let n = stack.len();
    let len = stack.first().map_or(0, |c| c.len());
    match method {
        Method::Mean => (mean_trace(stack), n),
        Method::Median => (median_trace(stack), n),
        Method::Trimmed => {
            let c = ((n as f64) * trim).floor() as usize;
            if n <= 2 * c + 1 {
                return (mean_trace(stack), n);
            }
            let mut out = vec![0.0; len];
            let mut col = vec![0.0; n];
            for j in 0..len {
                for (i, cap) in stack.iter().enumerate() {
                    col[i] = cap[j];
                }
                col.sort_by(|a, b| a.partial_cmp(b).unwrap());
                let kept = &col[c..n - c];
                out[j] = kept.iter().sum::<f64>() / kept.len() as f64;
            }
            (out, n - 2 * c)
        }
        Method::Outlier => {
            let med = median_trace(stack);
            let rms: Vec<f64> = stack
                .iter()
                .map(|cap| {
                    let ss: f64 = cap.iter().zip(&med).map(|(a, b)| (a - b) * (a - b)).sum();
                    (ss / len as f64).sqrt()
                })
                .collect();
            let mut sorted = rms.clone();
            sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let rmed = sorted[sorted.len() / 2];
            // Robust scale (median absolute deviation): a mean/std threshold is
            // inflated by the very outliers being rejected, so a big glitch can
            // mask itself under the threshold. 1.4826*MAD ~ sigma for Gaussians.
            let mut dev: Vec<f64> = rms.iter().map(|r| (r - rmed).abs()).collect();
            dev.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let mad = dev[dev.len() / 2];
            let thr = rmed + z * (1.4826 * mad + 1e-12);
            let kept: Vec<Vec<f64>> = stack
                .iter()
                .zip(&rms)
                .filter(|(_, r)| **r <= thr)
                .map(|(c, _)| c.clone())
                .collect();
            if kept.len() >= 2 {
                (mean_trace(&kept), kept.len())
            } else {
                (mean_trace(stack), n)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Trigger-synchronous artifact removal / temporal filters
// ---------------------------------------------------------------------------

/// Remove the mod-4 JESD interleave baseline (per-lane DC), matching the Python tool.
pub fn deinterleave(x: &[f64]) -> Vec<f64> {
    let mut y = x.to_vec();
    for k in 0..4 {
        let idx: Vec<usize> = (k..y.len()).step_by(4).collect();
        if idx.is_empty() {
            continue;
        }
        let m: f64 = idx.iter().map(|&i| y[i]).sum::<f64>() / idx.len() as f64;
        for &i in &idx {
            y[i] -= m;
        }
    }
    y
}

pub fn moving_avg(x: &[f64], k: usize) -> Vec<f64> {
    if k <= 1 {
        return x.to_vec();
    }
    let n = x.len();
    let h = k / 2;
    (0..n)
        .map(|i| {
            let a = i.saturating_sub(h);
            let b = (i + h + 1).min(n);
            x[a..b].iter().sum::<f64>() / (b - a) as f64
        })
        .collect()
}

/// Anti-aliased decimation by `factor` (box-filter then downsample).
pub fn decimate(x: &[f64], factor: usize) -> Vec<f64> {
    if factor <= 1 {
        return x.to_vec();
    }
    let sm = moving_avg(x, factor);
    sm.iter().step_by(factor).copied().collect()
}

/// In-place radix-2 Cooley-Tukey FFT; `inverse` selects IFFT (with 1/N scaling).
fn fft(re: &mut [f64], im: &mut [f64], inverse: bool) {
    let n = re.len();
    if n < 2 {
        return;
    }
    // bit-reversal permutation
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
    let mut len = 2usize;
    while len <= n {
        let ang = (if inverse { 2.0 } else { -2.0 }) * std::f64::consts::PI / len as f64;
        let (wr, wi) = (ang.cos(), ang.sin());
        let mut i = 0usize;
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
    if inverse {
        let s = 1.0 / n as f64;
        for k in 0..n {
            re[k] *= s;
            im[k] *= s;
        }
    }
}

/// Brick-wall FFT low-pass at `cutoff_hz` (zero-phase). Edge-padded to the next
/// power of two to limit wrap-around. Returns a trace of the original length.
pub fn lowpass_fft(x: &[f64], fs: f64, cutoff_hz: f64) -> Vec<f64> {
    let n0 = x.len();
    if n0 < 4 || cutoff_hz <= 0.0 || cutoff_hz >= fs / 2.0 {
        return x.to_vec();
    }
    let n = n0.next_power_of_two();
    let mut re = vec![0.0; n];
    let mut im = vec![0.0; n];
    for i in 0..n {
        re[i] = if i < n0 { x[i] } else { x[n0 - 1] };
    }
    fft(&mut re, &mut im, false);
    let kcut = ((cutoff_hz / fs) * n as f64).round() as usize;
    for k in 1..n {
        let f = k.min(n - k);
        if f > kcut {
            re[k] = 0.0;
            im[k] = 0.0;
        }
    }
    fft(&mut re, &mut im, true);
    re[..n0].to_vec()
}

// ---------------------------------------------------------------------------
// Noise statistics
// ---------------------------------------------------------------------------

pub struct NoiseStats {
    pub sigma: Vec<f64>,   // per-sample std across the stack
    pub snr_single: f64,   // dB of one capture vs the estimate
    pub snr_ensemble: f64, // dB after √N averaging
}

pub fn noise_stats(stack: &[Vec<f64>], est: &[f64]) -> NoiseStats {
    let n = stack.len();
    let len = est.len();
    let mut sigma = vec![0.0; len];
    if n > 0 {
        for j in 0..len {
            let mean = stack.iter().map(|c| c[j]).sum::<f64>() / n as f64;
            let var = stack.iter().map(|c| (c[j] - mean).powi(2)).sum::<f64>() / n as f64;
            sigma[j] = var.sqrt();
        }
    }
    // residual rms of raw captures vs the estimate
    let mut ss = 0.0;
    let mut cnt = 0usize;
    for cap in stack {
        let m = len.min(cap.len());
        for j in 0..m {
            let d = cap[j] - est[j];
            ss += d * d;
            cnt += 1;
        }
    }
    let rms = if cnt > 0 { (ss / cnt as f64).sqrt() } else { 0.0 };
    let pp = est.iter().cloned().fold(f64::NEG_INFINITY, f64::max)
        - est.iter().cloned().fold(f64::INFINITY, f64::min);
    let snr_single = if rms > 0.0 { 20.0 * (pp / rms).log10() } else { f64::INFINITY };
    let eff = rms / (n.max(1) as f64).sqrt();
    let snr_ensemble = if eff > 0.0 { 20.0 * (pp / eff).log10() } else { f64::INFINITY };
    NoiseStats { sigma, snr_single, snr_ensemble }
}
