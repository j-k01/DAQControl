"""Pure-data description of the 9x6 MZI heater wiring."""

from __future__ import annotations

import math


# PyDAQ's EVAL-AD5370 transport accepts the board's much wider electrical
# range (-4..8 V by default). These are photonic-heater safety limits, not
# DAC capability limits, and every application write must be checked here.
HEATER_MIN_V = 0.0
HEATER_MAX_V = 1.0

EVAL0_CHANNELS = {
    "h_7_1": 0, "h_6_1": 2, "h_8_6": 4, "h_7_6": 6,
    "h_4_5": 22, "h_6_6": 20, "h_7_5": 18, "h_9_6": 16,
    "h_1_6": 14, "h_2_6": 12, "h_3_6": 10, "h_5_6": 8,
    "h_2_3": 5, "h_1_3": 3, "h_3_4": 1, "h_1_5": 38,
    "h_4_6": 36, "h_5_5": 34, "h_6_5": 32, "h_8_5": 30,
    "h_9_5": 28, "h_2_5": 26, "h_3_5": 24, "h_3_1": 39,
    "h_2_1": 37, "h_1_2": 35, "h_5_4": 33, "h_4_4": 31,
    "h_1_1": 29, "h_4_2": 27, "h_2_2": 25, "h_4_3": 23,
    "h_3_3": 21, "h_2_4": 19, "h_1_4": 17, "h_5_1": 15,
    "h_4_1": 13, "h_5_2": 11, "h_3_2": 9, "h_5_3": 7,
}

EVAL1_CHANNELS = {
    "h_6_4": 6, "h_9_3": 8, "h_7_3": 10, "h_6_3": 12,
    "h_8_4": 20, "h_7_4": 22, "h_9_2": 24, "h_8_2": 26,
    "h_9_4": 28, "h_8_3": 30, "h_7_2": 32, "h_6_2": 34,
    "h_9_1": 36, "h_8_1": 38,
}

BOARD_DEFINITIONS = {
    "EVAL0": {"uid": 0, "cs_pin": 17, "channels": EVAL0_CHANNELS},
    "EVAL1": {"uid": 1, "cs_pin": 21, "channels": EVAL1_CHANNELS},
}

MZI_NET_NAMES = tuple(
    sorted(
        (*EVAL0_CHANNELS, *EVAL1_CHANNELS),
        key=lambda name: tuple(int(part) for part in name.split("_")[1:]),
    )
)

# Electrical tone outputs are wired to the final three photonic input rows.
# DAC3 remains the electrical timing/amplitude reference and does not drive a
# photonic input during calibration.
PHOTONIC_INPUT_ROW_BY_DAC = {0: 7, 1: 8, 2: 9}
PHOTONIC_INPUT_DAC_BY_ROW = {
    row: dac for dac, row in PHOTONIC_INPUT_ROW_BY_DAC.items()
}


def calibration_heaters_for_dac(dac: int) -> tuple[str, ...]:
    try:
        row = PHOTONIC_INPUT_ROW_BY_DAC[int(dac)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("photonic input DAC must be 0, 1, or 2") from exc
    return tuple(
        net for net in MZI_NET_NAMES
        if int(net.split("_")[1]) == row
    )


HEATER_HARDWARE = {
    net: {
        "board": board,
        "uid": definition["uid"],
        "cs_pin": definition["cs_pin"],
        "channel": channel,
        "row": int(net.split("_")[1]),
        "column": int(net.split("_")[2]),
    }
    for board, definition in BOARD_DEFINITIONS.items()
    for net, channel in definition["channels"].items()
}


def validate_heater_voltage(value, *, label="heater") -> float:
    """Return one finite heater voltage inside the application safety range."""

    voltage = float(value)
    if (not math.isfinite(voltage) or
            not HEATER_MIN_V <= voltage <= HEATER_MAX_V):
        raise ValueError(
            f"{label} voltage {voltage:g} V is outside "
            f"{HEATER_MIN_V:g}..{HEATER_MAX_V:g} V")
    return voltage


def validate_requested_heater_voltages(voltages) -> dict[str, float]:
    """Return only the requested heaters after strict safety validation."""

    if not isinstance(voltages, dict):
        raise ValueError("heater voltages must be a net-to-voltage mapping")
    unknown = sorted(set(voltages) - set(MZI_NET_NAMES))
    if unknown:
        raise ValueError(f"unknown heater nets: {', '.join(unknown)}")
    result = {}
    for net, value in voltages.items():
        result[net] = validate_heater_voltage(value, label=net)
    return result


def validate_heater_voltages(voltages) -> dict[str, float]:
    """Return a complete, range-checked heater-voltage mapping."""

    requested = validate_requested_heater_voltages(voltages)
    return {
        net: requested.get(net, 0.0)
        for net in MZI_NET_NAMES
    }


def ordered_heater_nets(nets) -> tuple[str, ...]:
    requested = set(nets)
    unknown = sorted(requested - set(MZI_NET_NAMES))
    if unknown:
        raise ValueError(f"unknown heater nets: {', '.join(unknown)}")
    return tuple(net for net in MZI_NET_NAMES if net in requested)
