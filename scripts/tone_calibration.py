"""Coherent pure-tone analysis for optical crossbar calibration."""

from dataclasses import dataclass
from typing import Mapping

import numpy as np

DDS_PHASE_BITS = 24
DAC_SAMPLE_RATE_HZ = 1.0e9


@dataclass(frozen=True)
class ToneFit:
    amplitude: float
    phase_rad: float
    offset: float
    residual_rms: float
    fitted: np.ndarray


def dds_phase_increment(frequency_hz, sample_rate_hz=DAC_SAMPLE_RATE_HZ):
    """Return (24-bit increment, actual frequency) for the shared FPGA DDS."""
    frequency_hz = float(frequency_hz)
    sample_rate_hz = float(sample_rate_hz)
    if not 0.0 < frequency_hz < sample_rate_hz / 2.0:
        raise ValueError("DDS frequency must be between 0 and Nyquist")
    increment = int(round(frequency_hz * (1 << DDS_PHASE_BITS) / sample_rate_hz))
    increment = max(1, min((1 << DDS_PHASE_BITS) - 1, increment))
    actual = increment * sample_rate_hz / float(1 << DDS_PHASE_BITS)
    return increment, actual


def fit_tone(waveform, frequency_hz, sample_rate_hz=DAC_SAMPLE_RATE_HZ,
             start_sample=64):
    """Least-squares sine fit with independent DC offset."""
    waveform = np.asarray(waveform, dtype=np.float64)
    start_sample = max(0, int(start_sample))
    if waveform.ndim != 1 or waveform.size - start_sample < 8:
        raise ValueError("tone fit requires a one-dimensional capture")
    y = waveform[start_sample:]
    n = np.arange(start_sample, waveform.size, dtype=np.float64)
    omega = 2.0 * np.pi * float(frequency_hz) / float(sample_rate_hz)
    design = np.column_stack((
        np.sin(omega * n),
        np.cos(omega * n),
        np.ones(n.size, dtype=np.float64),
    ))
    sin_coeff, cos_coeff, offset = np.linalg.lstsq(
        design, y, rcond=None)[0]
    fitted_tail = design @ np.asarray([sin_coeff, cos_coeff, offset])
    fitted = np.full(waveform.shape, np.nan, dtype=np.float64)
    fitted[start_sample:] = fitted_tail
    residual = y - fitted_tail
    return ToneFit(
        amplitude=float(np.hypot(sin_coeff, cos_coeff)),
        phase_rad=float(np.arctan2(cos_coeff, sin_coeff)),
        offset=float(offset),
        residual_rms=float(np.sqrt(np.mean(residual * residual))),
        fitted=fitted,
    )


def analyze_tone_capture(stacks_by_adc: Mapping[int, np.ndarray],
                         frequency_hz, *, reference_adc=3,
                         sample_rate_hz=DAC_SAMPLE_RATE_HZ,
                         start_sample=64):
    """Average aligned repetitions per ADC, then fit gain and phase vs ADC3."""
    averages = {}
    fits = {}
    amplitude_std = {}
    for channel, stack in stacks_by_adc.items():
        stack = np.asarray(stack, dtype=np.float64)
        if stack.ndim != 2 or stack.shape[0] < 1:
            raise ValueError(f"ADC{channel} stack must have shape (N, samples)")
        average = stack.mean(axis=0)
        averages[int(channel)] = average
        fits[int(channel)] = fit_tone(
            average, frequency_hz, sample_rate_hz, start_sample)
        rep_amplitudes = [
            fit_tone(rep, frequency_hz, sample_rate_hz, start_sample).amplitude
            for rep in stack
        ]
        amplitude_std[int(channel)] = float(np.std(rep_amplitudes))

    reference_adc = int(reference_adc)
    if reference_adc not in fits:
        raise ValueError(f"reference ADC{reference_adc} is missing")
    reference = fits[reference_adc]
    if reference.amplitude <= np.finfo(np.float64).eps:
        raise ValueError("electrical reference tone has zero fitted amplitude")

    period_s = 1.0 / float(frequency_hz)
    channels = {}
    for channel, fit in fits.items():
        phase_delta = float(np.angle(np.exp(
            1j * (fit.phase_rad - reference.phase_rad))))
        latency_s = (-phase_delta / (2.0 * np.pi * float(frequency_hz))) % period_s
        channels[channel] = {
            "amplitude_v": fit.amplitude,
            "amplitude_std_v": amplitude_std[channel],
            "gain_vs_reference": fit.amplitude / reference.amplitude,
            "phase_vs_reference_rad": phase_delta,
            "latency_modulo_period_s": latency_s,
            "latency_modulo_period_ns": latency_s * 1.0e9,
            "offset_v": fit.offset,
            "residual_rms_v": fit.residual_rms,
            "average_v": averages[channel],
            "fitted_v": fit.fitted,
        }
    return {
        "frequency_hz": float(frequency_hz),
        "sample_rate_hz": float(sample_rate_hz),
        "reference_adc": reference_adc,
        "period_s": period_s,
        "channels": channels,
    }
