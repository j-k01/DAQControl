"""MZI weight-curve calibration helpers shared by the Qt GUI and tests."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import re
import sys
import time

import numpy as np
from mzi_heater_map import (
    HEATER_MAX_V, HEATER_MIN_V, validate_heater_voltage,
    validate_requested_heater_voltages,
)
from spike_test_analysis import detect_spikes_output


@dataclass(frozen=True)
class PulseTrainMeasurement:
    signed_height: float
    absolute_height: float
    per_rep_height: np.ndarray
    phase_offsets: np.ndarray
    aligned_average: np.ndarray


@dataclass(frozen=True)
class TriggeredSpikeMeasurement:
    signed_height: float
    absolute_height: float
    per_rep_height: np.ndarray
    per_peak_height: np.ndarray
    peak_indices: np.ndarray
    baseline_levels: np.ndarray
    averaged_waveform: np.ndarray
    start_indices: np.ndarray
    end_indices: np.ndarray
    polarities: np.ndarray
    widths_samples: np.ndarray
    fwhm_samples: np.ndarray
    areas_v_samples: np.ndarray
    detection_threshold_v: float
    boundary_thresholds_v: np.ndarray
    noise_sigma_v: float


def positive_average_peaks(
    measurement: TriggeredSpikeMeasurement,
) -> tuple[np.ndarray, np.ndarray]:
    """Return positive detected peaks on the 16-capture averaged waveform."""

    waveform = np.asarray(measurement.averaged_waveform, dtype=np.float64)
    peaks = np.asarray(measurement.peak_indices, dtype=np.int32)
    polarities = np.asarray(measurement.polarities, dtype=np.int8)
    valid = (polarities > 0) & (peaks >= 0) & (peaks < waveform.size)
    selected = peaks[valid]
    amplitudes = waveform[selected]
    keep = np.isfinite(amplitudes) & (amplitudes > 0.0)
    return selected[keep], amplitudes[keep]


def select_positive_average_peaks(
    measurement: TriggeredSpikeMeasurement,
    detected: TriggeredSpikeMeasurement | None = None,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Use independently detected positive peaks, with fixed-index fallback."""

    if detected is not None:
        peaks, amplitudes = positive_average_peaks(detected)
        if peaks.size:
            return peaks, amplitudes, "detected"
    peaks, amplitudes = positive_average_peaks(measurement)
    return peaks, amplitudes, "reference fallback"


def _phase_average(trace: np.ndarray, period: int) -> np.ndarray:
    phase = np.arange(trace.size) % period
    sums = np.bincount(phase, weights=trace, minlength=period)
    counts = np.bincount(phase, minlength=period)
    return sums / np.maximum(counts, 1)


def measure_periodic_pulses(
    repetitions: np.ndarray,
    period_samples: int,
    high_samples: int,
) -> PulseTrainMeasurement:
    """Align periodic pulse captures and measure high-minus-baseline height."""

    reps = np.asarray(repetitions, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 1 or reps.shape[1] < 4:
        raise ValueError("repetitions must have shape [N, samples]")
    period = int(period_samples)
    high = int(high_samples)
    if period < 4 or period > reps.shape[1]:
        raise ValueError("period_samples must be between 4 and capture length")
    if high < 1 or high >= period:
        raise ValueError("high_samples must be between 1 and period-1")

    template = np.zeros(period, dtype=np.float64)
    template[:high] = 1.0
    template -= template.mean()
    template_fft = np.fft.rfft(template)

    offsets = np.empty(reps.shape[0], dtype=np.int32)
    heights = np.empty(reps.shape[0], dtype=np.float64)
    aligned = np.empty_like(reps)
    guard = min(max(1, high // 8), max(1, (period - high) // 4))
    low_phase = np.arange(period) >= min(period - 1, high + guard)
    low_phase &= np.arange(period) < max(high + 1, period - guard)
    if not np.any(low_phase):
        low_phase = np.arange(period) >= high

    for index, trace in enumerate(reps):
        folded = _phase_average(trace, period)
        folded -= np.median(folded)
        correlation = np.fft.irfft(
            np.fft.rfft(folded) * np.conj(template_fft), n=period
        )
        offset = int(np.argmax(np.abs(correlation)))
        offsets[index] = offset
        aligned[index] = np.roll(trace, -offset)

        phase_trace = _phase_average(aligned[index], period)
        high_level = np.median(phase_trace[:high])
        low_level = np.median(phase_trace[low_phase])
        heights[index] = high_level - low_level

    signed = float(np.mean(heights))
    return PulseTrainMeasurement(
        signed_height=signed,
        absolute_height=abs(signed),
        per_rep_height=heights,
        phase_offsets=offsets,
        aligned_average=aligned.mean(axis=0),
    )


def parse_heater_voltages(text: str) -> np.ndarray:
    """Parse a separated list within the photonic-heater safety range."""

    tokens = [token for token in re.split(r"[,;\s]+", str(text).strip()) if token]
    if len(tokens) < 2:
        raise ValueError("enter at least two heater voltages")
    try:
        values = np.asarray([float(token) for token in tokens], dtype=np.float64)
    except ValueError as exc:
        raise ValueError("heater voltage list contains a non-number") from exc
    if not np.all(np.isfinite(values)):
        raise ValueError("heater voltages must be finite")
    if np.any(values < HEATER_MIN_V) or np.any(values > HEATER_MAX_V):
        raise ValueError(
            f"heater voltages must be between {HEATER_MIN_V:g} and "
            f"{HEATER_MAX_V:g} V")
    return values


def measure_triggered_spikes(
    repetitions: np.ndarray,
    step_sample: int,
    *,
    threshold_sigma: float = 5.0,
    boundary_sigma: float = 2.0,
    boundary_quiet_samples: int = 3,
    minimum_seed_samples: int = 2,
    minimum_width_samples: int = 1,
    smooth_samples: int = 1,
) -> TriggeredSpikeMeasurement:
    """Measure repeatable neuron-spike pulses from hardware-aligned captures.

    Each repetition is baseline-subtracted independently. Peaks are detected
    once on the fixed-index average, then every repetition is measured at the
    same indices; no software time shifting is applied.
    """

    reps = np.asarray(repetitions, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 1 or reps.shape[1] < 16:
        raise ValueError("repetitions must have shape [N, samples]")
    step = int(step_sample)
    if step < 8 or step >= reps.shape[1] - 4:
        raise ValueError("step_sample must leave baseline and response regions")
    baseline_end = max(4, step - 10)
    baseline_levels = np.median(reps[:, :baseline_end], axis=1)
    centered = reps - baseline_levels[:, None]
    averaged = centered.mean(axis=0)
    detection = detect_spikes_output(
        averaged, response_start=step, repetitions_v=centered,
        threshold_sigma=threshold_sigma, boundary_sigma=boundary_sigma,
        boundary_quiet_samples=boundary_quiet_samples,
        minimum_seed_samples=minimum_seed_samples,
        minimum_width_samples=minimum_width_samples,
        smooth_samples=smooth_samples)
    measured = detection.measurements
    if measured.peak_indices.size == 0:
        raise ValueError("no triggered spikes exceeded the detection threshold")
    peak_indices = measured.peak_indices
    per_peak = measured.per_rep_amplitudes_v
    per_rep = per_peak.mean(axis=1)
    signed = float(np.mean(per_rep))
    return TriggeredSpikeMeasurement(
        signed_height=signed,
        absolute_height=abs(signed),
        per_rep_height=per_rep,
        per_peak_height=per_peak,
        peak_indices=peak_indices,
        baseline_levels=baseline_levels,
        averaged_waveform=averaged,
        start_indices=measured.start_indices,
        end_indices=measured.end_indices,
        polarities=measured.polarities,
        widths_samples=measured.widths_samples,
        fwhm_samples=measured.fwhm_samples,
        areas_v_samples=measured.areas_v_samples,
        detection_threshold_v=detection.threshold,
        boundary_thresholds_v=measured.boundary_thresholds_v,
        noise_sigma_v=detection.noise_sigma,
    )

def measure_spikes_at_indices(
    repetitions: np.ndarray,
    step_sample: int,
    peak_indices: np.ndarray,
    *,
    start_indices: np.ndarray | None = None,
    end_indices: np.ndarray | None = None,
    polarities: np.ndarray | None = None,
) -> TriggeredSpikeMeasurement:
    """Measure every capture at one shared set of hardware spike indices.

    This is used after detecting the spike schedule at the strongest optical
    sweep point. It intentionally accepts zero-response traces at extinction.
    """

    reps = np.asarray(repetitions, dtype=np.float64)
    if reps.ndim != 2 or reps.shape[0] < 1 or reps.shape[1] < 16:
        raise ValueError("repetitions must have shape [N, samples]")
    step = int(step_sample)
    if step < 8 or step >= reps.shape[1] - 4:
        raise ValueError("step_sample must leave baseline and response regions")
    peaks = np.asarray(peak_indices, dtype=np.int32)
    if peaks.ndim != 1 or peaks.size < 1:
        raise ValueError("peak_indices must contain at least one sample")
    if np.any(peaks < step) or np.any(peaks >= reps.shape[1]):
        raise ValueError("peak_indices must lie in the response region")

    baseline_end = max(4, step - 10)
    baseline_levels = np.median(reps[:, :baseline_end], axis=1)
    centered = reps - baseline_levels[:, None]
    averaged = centered.mean(axis=0)
    starts = peaks.copy() if start_indices is None else np.asarray(start_indices, dtype=np.int32)
    ends = peaks.copy() if end_indices is None else np.asarray(end_indices, dtype=np.int32)
    signs = (np.sign(averaged[peaks]).astype(np.int8) if polarities is None else
             np.asarray(polarities, dtype=np.int8))
    if starts.shape != peaks.shape or ends.shape != peaks.shape or signs.shape != peaks.shape:
        raise ValueError("boundary and polarity arrays must match peak_indices")
    if np.any(starts > peaks) or np.any(ends < peaks):
        raise ValueError("each fixed peak must lie inside its boundary")
    # Measure each event inside its reference boundary rather than at one exact
    # sample. This preserves the hardware-aligned data while avoiding a large
    # amplitude error when a narrow pulse peak moves by a sample inside the
    # same detected event window.
    per_peak = np.empty((centered.shape[0], peaks.size), dtype=np.float64)
    for index, (start, end, polarity) in enumerate(zip(starts, ends, signs)):
        window = centered[:, int(start):int(end) + 1]
        if int(polarity) < 0:
            per_peak[:, index] = np.min(window, axis=1)
        else:
            per_peak[:, index] = np.max(window, axis=1)
    per_rep = per_peak.mean(axis=1)
    signed = float(np.mean(per_rep))
    widths = ends - starts + 1
    areas = np.asarray([
        np.sum(int(signs[index]) * averaged[starts[index]:ends[index] + 1])
        for index in range(peaks.size)], dtype=np.float64)
    baseline_average = averaged[:baseline_end]
    median = float(np.median(baseline_average))
    noise = 1.4826 * float(np.median(np.abs(baseline_average - median)))
    return TriggeredSpikeMeasurement(
        signed_height=signed,
        absolute_height=abs(signed),
        per_rep_height=per_rep,
        per_peak_height=per_peak,
        peak_indices=peaks.copy(),
        baseline_levels=baseline_levels,
        averaged_waveform=averaged,
        start_indices=starts.copy(),
        end_indices=ends.copy(),
        polarities=signs.copy(),
        widths_samples=widths,
        fwhm_samples=np.zeros(peaks.size, dtype=np.int32),
        areas_v_samples=areas,
        detection_threshold_v=float("nan"),
        boundary_thresholds_v=np.full(peaks.size, np.nan),
        noise_sigma_v=noise,
    )

def probe_fpga_pico_bridge(
    board_ip: str = "192.168.2.10",
    local_ip: str = "192.168.2.1",
    *,
    timeout: float = 0.5,
    serial_factory=None,
) -> None:
    """Verify that the Linux Pico bridge answers without touching the Pico."""

    if serial_factory is None:
        from fpga_pico_serial import Serial as serial_factory
    try:
        with serial_factory(
            transport="ethernet",
            board_ip=board_ip,
            local_ip=local_ip,
            timeout=timeout,
            write_timeout=timeout,
        ) as bridge:
            bridge.reset_input_buffer()
    except OSError as exc:
        raise RuntimeError(
            f"FPGA Pico bridge is not responding at {board_ip}:5007. "
            f"ADC capture on {board_ip}:5006 is a separate service and can "
            "still work. The ordinary program_board.ps1/program_board.tcl "
            "runtime does not provide the USB host or Pico bridge. Load the "
            "unified runtime with uv run python pico_usb\\load_and_test.py "
            "--local-jtag --port COM9, then retry."
        ) from exc


class PydaqMziController:
    """Lazy connection to the FPGA-attached PICO-002 through unmodified PyDAQ."""

    def __init__(self) -> None:
        self._installation = None
        self._config = None

    @property
    def connected(self) -> bool:
        return self._config is not None

    def connect(
        self,
        *,
        board_ip: str = "192.168.2.10",
        local_ip: str = "192.168.2.1",
    ) -> None:
        if self.connected:
            return
        module_path = Path(__file__).resolve()
        for search_path in (str(module_path.parent), str(module_path.parents[1])):
            if search_path not in sys.path:
                sys.path.insert(0, search_path)
        from pydaq_fpga_transport import install

        probe_fpga_pico_bridge(board_ip, local_ip)
        installation = install(
            board_ip=board_ip,
            local_ip=local_ip,
            transport="ethernet",
        )
        try:
            module_name = "mzi_pydaq_config"
            if module_name in sys.modules:
                config = importlib.reload(sys.modules[module_name])
            else:
                config = importlib.import_module(module_name)
        except Exception as exc:
            installation.uninstall(close_connections=True)
            message = str(exc)
            endpoint = f"{board_ip}:{installation.backend.config.udp_port}"
            if "No serial DAQ boards found" in message:
                raise RuntimeError(
                    f"The FPGA Pico bridge answered at {endpoint}, but "
                    "PICO-002 did not answer the PyDAQ HANDSHAKE. The bridge "
                    "service is running; check the Pico USB cable and confirm "
                    "the directly connected Pico still returns UID:PICO-002."
                ) from exc
            if "Board PICO-002 not found" in message:
                raise RuntimeError(
                    f"The FPGA bridge at {endpoint} returned a Pico UID other "
                    f"than PICO-002. Program the expected PICO-002 firmware."
                ) from exc
            raise
        self._installation = installation
        self._config = config

    def available_nets(self) -> tuple[str, ...]:
        if not self.connected:
            raise RuntimeError("PyDAQ MZI controller is not connected")
        return tuple(self._config.MZI_NET_NAMES)

    def test_connection(self, probes: int = 5) -> dict[str, object]:
        """Exercise the live Pico CDC path without changing DAC outputs."""

        if not self.connected:
            raise RuntimeError("PyDAQ MZI controller is not connected")
        serial_port = self._config.pico.serial
        durations_ms = []
        for _ in range(max(1, int(probes))):
            started = time.monotonic()
            serial_port.reset_input_buffer()
            serial_port.write(b"HANDSHAKE\n")
            uid = serial_port.readline().decode(
                "ascii", errors="replace").strip()
            # Always leave handshake mode, including after a bad UID response.
            serial_port.write(b"ENDHS\n")
            response = serial_port.readline().decode(
                "ascii", errors="replace").strip()
            if uid != "UID:PICO-002":
                raise RuntimeError(
                    f"Pico handshake returned {uid!r}, expected 'UID:PICO-002'")
            if response != "HSOK":
                raise RuntimeError(
                    f"Pico handshake termination returned {response!r}")
            durations_ms.append((time.monotonic() - started) * 1000.0)
        return {
            "probes": len(durations_ms),
            "mean_ms": float(np.mean(durations_ms)),
            "max_ms": float(np.max(durations_ms)),
        }

    def set_voltage(self, net_name: str, voltage: float) -> None:
        if not self.connected:
            raise RuntimeError("PyDAQ MZI controller is not connected")
        requested = validate_requested_heater_voltages({net_name: voltage})
        self._config.set_mzi_voltage(net_name, requested[net_name])
    def set_voltages(self, voltages, *, on_sent=None) -> None:
        if not self.connected:
            raise RuntimeError("PyDAQ MZI controller is not connected")
        requested = validate_requested_heater_voltages(
            {str(net): voltage for net, voltage in voltages.items()})
        if hasattr(self._config, "set_mzi_voltages"):
            self._config.set_mzi_voltages(requested, on_sent=on_sent)
        else:
            for net, voltage in requested.items():
                self._config.set_mzi_voltage(net, voltage)
                if on_sent is not None:
                    on_sent(net, voltage)


    def close(self) -> None:
        if self._installation is not None:
            self._installation.uninstall(close_connections=True)
        self._installation = None
        self._config = None


def calibration_voltage_sequence(
    start: float,
    stop: float,
    points: int,
    reverse: bool,
    *,
    spacing: str = "voltage",
    explicit: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    if explicit is not None:
        forward = np.asarray(explicit, dtype=np.float64)
        if forward.ndim != 1 or forward.size < 2:
            raise ValueError("explicit heater sweep requires at least two values")
    else:
        if points < 2:
            raise ValueError("points must be at least 2")
        start = float(start)
        stop = float(stop)
        if spacing == "voltage":
            forward = np.linspace(start, stop, int(points))
        elif spacing == "power":
            if start < 0.0 or stop < 0.0:
                raise ValueError("power-spaced heater voltages must be non-negative")
            forward = np.sqrt(np.linspace(start * start, stop * stop, int(points)))
        else:
            raise ValueError("spacing must be voltage or power")
    for voltage in forward:
        validate_heater_voltage(voltage)
    if not reverse:
        return forward, np.zeros(forward.size, dtype=np.int8)
    backward = forward[::-1]
    return (
        np.concatenate((forward, backward)),
        np.concatenate((
            np.zeros(forward.size, dtype=np.int8),
            np.ones(backward.size, dtype=np.int8),
        )),
    )
