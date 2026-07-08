//! Load a burst capture `.npz` (numpy zip) written by the Qt GUI's `_save_burst`.
//! Members of interest: `raw_ch0..3` = N (captures) x L (samples) int16 stacks.
//! (`avg_ch0..3` and `offsets` are also present but recomputed here, so ignored.)

use std::io::Read;

/// One loaded burst file: up to 4 channels, each a stack of N captures of length L.
pub struct Burst {
    pub path: String,
    pub n: usize,
    pub len: usize,
    /// `chans[ch]` = Some(N captures, each `len` f64 samples) or None if absent.
    pub chans: [Option<Vec<Vec<f64>>>; 4],
}

impl Burst {
    pub fn load(path: &str) -> Result<Burst, String> {
        let f = std::fs::File::open(path).map_err(|e| format!("open: {e}"))?;
        let mut zip = zip::ZipArchive::new(f).map_err(|e| format!("not a .npz (zip): {e}"))?;
        let mut chans: [Option<Vec<Vec<f64>>>; 4] = Default::default();
        let mut n = 0usize;
        let mut len = 0usize;

        for ch in 0..4 {
            // numpy stores members as "<key>.npy"; accept the bare key too.
            let mut buf: Option<Vec<u8>> = None;
            for name in [format!("raw_ch{ch}.npy"), format!("raw_ch{ch}")] {
                if let Ok(mut e) = zip.by_name(&name) {
                    let mut b = Vec::with_capacity(e.size() as usize);
                    e.read_to_end(&mut b).map_err(|x| format!("read {name}: {x}"))?;
                    buf = Some(b);
                    break;
                }
            }
            let Some(buf) = buf else { continue };
            let (rows, cols, data) = parse_npy_i16(&buf).map_err(|e| format!("raw_ch{ch}: {e}"))?;
            if rows == 0 || cols == 0 {
                continue;
            }
            let mut caps = Vec::with_capacity(rows);
            for r in 0..rows {
                caps.push(data[r * cols..(r + 1) * cols].iter().map(|&v| v as f64).collect());
            }
            n = rows;
            len = cols;
            chans[ch] = Some(caps);
        }

        if chans.iter().all(|c| c.is_none()) {
            return Err("no raw_ch0..3 arrays found in file".into());
        }
        Ok(Burst { path: path.to_string(), n, len, chans })
    }

    pub fn file_name(&self) -> &str {
        std::path::Path::new(&self.path)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or(&self.path)
    }
}

/// Parse a single `.npy` member holding an int16 array of shape [N, L] (or [L]).
fn parse_npy_i16(buf: &[u8]) -> Result<(usize, usize, Vec<i16>), String> {
    let npy = npyz::NpyFile::new(std::io::Cursor::new(buf)).map_err(|e| e.to_string())?;
    let shape: Vec<u64> = npy.shape().to_vec();
    let (rows, cols) = match shape.as_slice() {
        [r, c] => (*r as usize, *c as usize),
        [c] => (1usize, *c as usize),
        other => return Err(format!("unexpected shape {other:?}")),
    };
    let data: Vec<i16> = npy.into_vec().map_err(|e| format!("expected int16: {e}"))?;
    if data.len() != rows * cols {
        return Err(format!("data len {} != {rows}x{cols}", data.len()));
    }
    Ok((rows, cols, data))
}
