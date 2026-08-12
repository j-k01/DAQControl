"""Minimal PyDAQ configuration for the 9x6 crossbar heater Pico.

This is the MZI-control subset of MVM_Experiments/photonic_crossbar/
crossbar_config.py. The FPGA supplies the optical test waveform and performs
ADC capture, so the MCC ADC and the separate laser Pico are intentionally not
part of this netlist.
"""
import time


import pydaq.daq as daq
import pydaq.ser as ser
from mzi_heater_map import (
    EVAL0_CHANNELS, EVAL1_CHANNELS, HEATER_MAX_V, HEATER_MIN_V,
    MZI_NET_NAMES,
)

HEATER_SPI_GUARD_S = 0.001



def _pins(mapping):
    return [ser.AOPIN(name, channel) for name, channel in mapping.items()]


eval0 = ser.EVAL_AD5370("EVAL0", 0, 17, *_pins(EVAL0_CHANNELS))
eval1 = ser.EVAL_AD5370("EVAL1", 1, 21, *_pins(EVAL1_CHANNELS))
pico = ser.BoardManager("PICO-002", eval0, eval1)

# The caller installs pydaq_fpga_transport before importing this module, so
# ordinary PyDAQ discovery sees the FPGA-backed virtual serial port here.
ser.config_detected_devices([pico], verbose=False)
netlist = daq.Netlist(pico)


def _strict_mzi_vout(net_name: str, voltage: float) -> str:
    pin = netlist.pins_dict[net_name]
    command = f"W{pin.board.uid},{pin.chnl},{float(voltage)}\n"
    response = pin.board._expect_ack(command)
    if not response.startswith("ACK"):
        raise RuntimeError(
            f"{net_name} write was not acknowledged by PICO-002 "
            f"(response {response!r})")
    return response


def set_mzi_voltage(net_name: str, voltage: float) -> None:
    """Set one crossing heater without resetting unrelated PyDAQ outputs."""

    if net_name not in MZI_NET_NAMES:
        raise KeyError(f"Unknown crossbar heater net {net_name!r}")
    if not HEATER_MIN_V <= float(voltage) <= HEATER_MAX_V:
        raise ValueError(
            f"heater voltage must be {HEATER_MIN_V:g}..{HEATER_MAX_V:g} V")
    # Netlist.__exit__ resets every board and every output. Calibration owns
    # only this heater, so deliberately address its configured pin directly.
    _strict_mzi_vout(net_name, float(voltage))


def set_mzi_voltages(voltages, *, on_sent=None) -> None:
    """Set several crossing heaters while preserving every other output."""

    requested = {str(net): float(voltage) for net, voltage in voltages.items()}
    unknown = sorted(set(requested) - set(MZI_NET_NAMES))
    if unknown:
        raise KeyError(f"Unknown crossbar heater nets {unknown!r}")
    invalid = {net: voltage for net, voltage in requested.items()
               if not HEATER_MIN_V <= voltage <= HEATER_MAX_V}
    if invalid:
        raise ValueError(
            f"heater voltages must be {HEATER_MIN_V:g}..{HEATER_MAX_V:g} V: {invalid}")
    written = 0
    for net_name in MZI_NET_NAMES:
        if net_name in requested:
            if written:
                time.sleep(HEATER_SPI_GUARD_S)
            _strict_mzi_vout(net_name, requested[net_name])
            if on_sent is not None:
                on_sent(net_name, requested[net_name])
            written += 1
