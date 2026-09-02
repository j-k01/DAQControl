"""Coherent pure-tone analysis for optical crossbar calibration."""

from dataclasses import dataclass
from typing import Mapping

import numpy as np

DDS_PHASE_BITS = 24
DAC_SAMPLE_RATE_HZ = 1.0e9


@dataclass(frozen=True)
class ToneFit:
    sin_coefficient: float
    cos_coefficient: float
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
        sin_coefficient=float(sin_coeff),
        cos_coefficient=float(cos_coeff),
        amplitude=float(np.hypot(sin_coeff, cos_coeff)),
        phase_rad=float(np.arctan2(cos_coeff, sin_coeff)),
        offset=float(offset),
        residual_rms=float(np.sqrt(np.mean(residual * residual))),
        fitted=fitted,
    )


def analyze_tone_capture(stacks_by_adc: Mapping[int, np.ndarray],
                         frequency_hz, *, reference_adc=None,
                         sample_rate_hz=DAC_SAMPLE_RATE_HZ,
                         start_sample=64):
    """Average and fit each ADC independently.

    reference_adc is optional diagnostic context. A present, nonzero reference
    adds relative gain/phase/latency fields, but it is never needed to obtain
    the absolute fitted amplitude of any ADC.
    """
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

    reference = None
    reference_reason = "not requested"
    if reference_adc is not None:
        reference_adc = int(reference_adc)
        if reference_adc not in fits:
            reference_reason = f"ADC{reference_adc} is missing"
        elif fits[reference_adc].amplitude <= np.finfo(np.float64).eps:
            reference_reason = f"ADC{reference_adc} fitted amplitude is zero"
        else:
            reference = fits[reference_adc]
            reference_reason = "available"

    period_s = 1.0 / float(frequency_hz)
    channels = {}
    for channel, fit in fits.items():
        channels[channel] = {
            "amplitude_v": fit.amplitude,
            "in_phase_v": fit.sin_coefficient,
            "quadrature_v": fit.cos_coefficient,
            "phase_rad": fit.phase_rad,
            "amplitude_std_v": amplitude_std[channel],
            "offset_v": fit.offset,
            "residual_rms_v": fit.residual_rms,
            "average_v": averages[channel],
            "fitted_v": fit.fitted,
        }
        if reference is not None:
            phase_delta = float(np.angle(np.exp(
                1j * (fit.phase_rad - reference.phase_rad))))
            latency_s = (
                -phase_delta / (2.0 * np.pi * float(frequency_hz))) % period_s
            channels[channel].update({
                "gain_vs_reference": fit.amplitude / reference.amplitude,
                "phase_vs_reference_rad": phase_delta,
                "latency_modulo_period_s": latency_s,
                "latency_modulo_period_ns": latency_s * 1.0e9,
            })
    return {
        "frequency_hz": float(frequency_hz),
        "sample_rate_hz": float(sample_rate_hz),
        "reference_adc": reference_adc,
        "reference_available": reference is not None,
        "reference_status": reference_reason,
        "period_s": period_s,
        "channels": channels,
    }


def fit_phasor_locus(in_phase, quadrature):
    """Fit the best straight line through real I/Q tone coefficients."""
    in_phase = np.asarray(in_phase, dtype=np.float64)
    quadrature = np.asarray(quadrature, dtype=np.float64)
    if in_phase.shape != quadrature.shape:
        raise ValueError("I and Q arrays must have matching shapes")
    valid = np.isfinite(in_phase) & np.isfinite(quadrature)
    points = np.column_stack((in_phase[valid], quadrature[valid]))
    if points.shape[0] < 2:
        raise ValueError("phasor locus fit requires at least two finite points")

    center = points.mean(axis=0)
    centered = points - center
    _u, singular_values, axes = np.linalg.svd(centered, full_matrices=False)
    if singular_values[0] <= np.finfo(np.float64).eps:
        raise ValueError("phasor locus points do not vary")
    direction = axes[0]
    projection = centered @ direction
    line_start = center + float(np.min(projection)) * direction
    line_end = center + float(np.max(projection)) * direction
    reconstructed = np.outer(projection, direction)
    perpendicular = centered - reconstructed
    perpendicular_rms = float(np.sqrt(np.mean(np.sum(
        perpendicular * perpendicular, axis=1))))
    total_energy = float(np.sum(singular_values * singular_values))
    linearity = (
        float((singular_values[0] * singular_values[0]) / total_energy)
        if total_energy > 0.0 else 1.0)
    normal = np.asarray([-direction[1], direction[0]])
    origin_distance = float(abs(center @ normal))
    return {
        "center": center,
        "direction": direction,
        "line_start": line_start,
        "line_end": line_end,
        "linearity": linearity,
        "perpendicular_rms": perpendicular_rms,
        "origin_distance": origin_distance,
        "point_count": int(points.shape[0]),
    }
