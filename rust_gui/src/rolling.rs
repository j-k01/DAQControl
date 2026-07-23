//! Fixed-memory rolling average for hardware-aligned ADC captures.

use std::collections::VecDeque;

pub type Capture = [Vec<i16>; 4];

pub struct RollingAverage {
    capacity: usize,
    sample_count: usize,
    captures: VecDeque<Capture>,
    sums: [Vec<i64>; 4],
    total_seen: u64,
}

impl RollingAverage {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity: capacity.max(1),
            sample_count: 0,
            captures: VecDeque::new(),
            sums: Default::default(),
            total_seen: 0,
        }
    }

    pub fn push(&mut self, capture: Capture) -> Result<(), String> {
        let n = capture[0].len();
        if n == 0 || capture.iter().any(|ch| ch.len() != n) {
            return Err("capture channels must have one equal, nonzero length".into());
        }
        if self.sample_count != 0 && self.sample_count != n {
            self.clear();
        }
        if self.sample_count == 0 {
            self.sample_count = n;
            self.sums = std::array::from_fn(|_| vec![0; n]);
        }

        if self.captures.len() == self.capacity {
            if let Some(oldest) = self.captures.pop_front() {
                for (sums, samples) in self.sums.iter_mut().zip(&oldest) {
                    for (sum, value) in sums.iter_mut().zip(samples) {
                        *sum -= *value as i64;
                    }
                }
            }
        }
        for (sums, samples) in self.sums.iter_mut().zip(&capture) {
            for (sum, value) in sums.iter_mut().zip(samples) {
                *sum += *value as i64;
            }
        }
        self.captures.push_back(capture);
        self.total_seen += 1;
        Ok(())
    }

    pub fn mean(&self) -> [Vec<f32>; 4] {
        let count = self.captures.len();
        if count == 0 {
            return Default::default();
        }
        let scale = 1.0 / count as f32;
        std::array::from_fn(|ch| {
            self.sums[ch]
                .iter()
                .map(|&sum| sum as f32 * scale)
                .collect()
        })
    }

    pub fn len(&self) -> usize {
        self.captures.len()
    }

    pub fn total_seen(&self) -> u64 {
        self.total_seen
    }

    fn clear(&mut self) {
        self.sample_count = 0;
        self.captures.clear();
        self.sums = Default::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn capture(value: i16, samples: usize) -> Capture {
        std::array::from_fn(|ch| vec![value + ch as i16; samples])
    }

    #[test]
    fn rolling_sum_adds_and_evicts_oldest() {
        let mut avg = RollingAverage::new(3);
        avg.push(capture(10, 4)).unwrap();
        avg.push(capture(20, 4)).unwrap();
        avg.push(capture(30, 4)).unwrap();
        assert_eq!(avg.mean()[0], vec![20.0; 4]);

        avg.push(capture(50, 4)).unwrap();
        assert_eq!(avg.len(), 3);
        assert_eq!(avg.total_seen(), 4);
        for value in avg.mean()[0].iter() {
            assert!((*value - 100.0 / 3.0).abs() < 1.0e-4);
        }
        for value in avg.mean()[3].iter() {
            assert!((*value - 109.0 / 3.0).abs() < 1.0e-4);
        }
    }

    #[test]
    fn changed_capture_length_restarts_window() {
        let mut avg = RollingAverage::new(4);
        avg.push(capture(10, 4)).unwrap();
        avg.push(capture(20, 6)).unwrap();
        assert_eq!(avg.len(), 1);
        assert_eq!(avg.mean()[0], vec![20.0; 6]);
    }
}
