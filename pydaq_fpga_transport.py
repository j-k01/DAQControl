#!/usr/bin/env python3
"""Expose the FPGA-connected Pico to an unmodified PyDAQ installation.

PyDAQ discovers controllers by enumerating PC serial ports.  The Pico in this
system is connected to the FPGA and its existing CDC byte stream is forwarded
to the PC by :mod:`fpga_pico_serial`.  Calling :func:`install` changes only the
``serial`` namespace used inside ``pydaq.ser``.  It does not modify PyDAQ,
program the Pico, or replace process-wide pyserial.

Typical use::

    from pydaq_fpga_transport import install
    install(board_ip="192.168.2.10", local_ip="192.168.2.1")

    from my_mvm_configuration import netlist
    # Use the configuration exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from types import SimpleNamespace
from typing import Any

import fpga_pico_serial


VIRTUAL_PORT = "FPGA-PICO"


@dataclass(frozen=True)
class VirtualPortInfo:
    """Subset of pyserial's ListPortInfo consumed by PyDAQ."""

    device: str = VIRTUAL_PORT
    name: str = VIRTUAL_PORT
    description: str = "Pico through FPGA transparent CDC bridge"
    hwid: str = "FPGA_PCDC"
    vid: None = None
    pid: None = None
    serial_number: None = None
    location: None = None
    manufacturer: str = "DAQControl"
    product: str = "FPGA Pico CDC bridge"
    interface: str = "PCDC"

    def __str__(self) -> str:
        return self.device


@dataclass(frozen=True)
class TransportConfig:
    board_ip: str = "192.168.2.10"
    local_ip: str = "192.168.2.1"
    udp_port: int = fpga_pico_serial.UDP_PORT
    transport: str = "ethernet"
    fpga_uart_port: str = "COM10"
    fpga_uart_baudrate: int = 115200
    ethernet_probe_timeout: float = 0.35
    virtual_port: str = VIRTUAL_PORT

    def __post_init__(self) -> None:
        if self.transport not in {"ethernet", "uart", "auto"}:
            raise ValueError("transport must be ethernet, uart, or auto")
        if not 1 <= self.udp_port <= 65535:
            raise ValueError("udp_port must be between 1 and 65535")


class _SerialFactory:
    def __init__(self, config: TransportConfig) -> None:
        self.config = config

    def __call__(self, *args: Any, **kwargs: Any) -> fpga_pico_serial.Serial:
        # PyDAQ supplies the virtual device as its first argument.  Preserve
        # that pyserial-compatible call while forcing the actual bridge path.
        kwargs.update(
            transport=self.config.transport,
            board_ip=self.config.board_ip,
            local_ip=self.config.local_ip,
            udp_port=self.config.udp_port,
            fpga_uart_port=self.config.fpga_uart_port,
            fpga_uart_baudrate=self.config.fpga_uart_baudrate,
            ethernet_probe_timeout=self.config.ethernet_probe_timeout,
        )
        return fpga_pico_serial.Serial(*args, **kwargs)


class _PydaqSerialBackend:
    """Module-shaped serial namespace installed only in ``pydaq.ser``."""

    SerialException = fpga_pico_serial.SerialException
    SerialTimeoutException = fpga_pico_serial.SerialTimeoutException

    def __init__(self, config: TransportConfig) -> None:
        self._fpga_pydaq_backend = True
        self.config = config
        self.Serial = _SerialFactory(config)
        port = VirtualPortInfo(
            device=config.virtual_port,
            name=config.virtual_port,
        )
        self.tools = SimpleNamespace(
            list_ports=SimpleNamespace(comports=lambda: [port])
        )


class Installation:
    """Handle used to restore direct-PC serial discovery when required."""

    def __init__(self, pydaq_serial: Any, backend: Any, previous: Any) -> None:
        self.pydaq_serial = pydaq_serial
        self.backend = backend
        self.previous = previous
        self.active = True

    def uninstall(self, *, close_connections: bool = False) -> None:
        if not self.active:
            return
        if self.pydaq_serial.serial is not self.backend:
            raise RuntimeError("PyDAQ serial backend changed after installation")
        board_dict = self.pydaq_serial._board_dict
        if board_dict:
            if not close_connections:
                raise RuntimeError(
                    "PyDAQ still has detected boards; pass close_connections=True"
                )
            for connection in tuple(board_dict.values()):
                connection.close()
            board_dict.clear()
        self.pydaq_serial.serial = self.previous
        self.active = False

    def __enter__(self) -> "Installation":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        del exc_type, exc_value, traceback
        self.uninstall(close_connections=True)


def _load_pydaq_serial() -> Any:
    try:
        module = importlib.import_module("pydaq.ser")
    except ImportError as exc:
        raise RuntimeError(
            "PyDAQ is not importable. Install it or add the MVM_Experiments "
            "PyDAQ source directory to PYTHONPATH first."
        ) from exc

    required = ("serial", "find_boards", "config_detected_devices", "_board_dict")
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise RuntimeError(
            "Unsupported PyDAQ serial interface; missing: " + ", ".join(missing)
        )
    if not isinstance(module._board_dict, dict):
        raise RuntimeError("Unsupported PyDAQ serial interface: _board_dict is not a dict")
    return module


def install(
    *,
    board_ip: str = "192.168.2.10",
    local_ip: str = "192.168.2.1",
    udp_port: int = fpga_pico_serial.UDP_PORT,
    transport: str = "ethernet",
    fpga_uart_port: str = "COM10",
    fpga_uart_baudrate: int = 115200,
    ethernet_probe_timeout: float = 0.35,
    virtual_port: str = VIRTUAL_PORT,
) -> Installation:
    """Install the FPGA bridge before PyDAQ discovers controller boards.

    This function performs no hardware I/O.  PyDAQ opens the bridge later from
    ``find_boards`` or ``config_detected_devices``.  Ethernet is the GUI-safe
    default because the DAQ application may already own the FPGA UART.
    """

    config = TransportConfig(
        board_ip=board_ip,
        local_ip=local_ip,
        udp_port=udp_port,
        transport=transport,
        fpga_uart_port=fpga_uart_port,
        fpga_uart_baudrate=fpga_uart_baudrate,
        ethernet_probe_timeout=ethernet_probe_timeout,
        virtual_port=virtual_port,
    )
    pydaq_serial = _load_pydaq_serial()
    current = pydaq_serial.serial
    if getattr(current, "_fpga_pydaq_backend", False):
        if current.config == config:
            raise RuntimeError("The PyDAQ FPGA transport is already installed")
        raise RuntimeError(
            "The PyDAQ FPGA transport is already installed with different settings"
        )
    if pydaq_serial._board_dict:
        raise RuntimeError(
            "PyDAQ already detected serial boards. Install this adapter before "
            "calling find_boards/config_detected_devices."
        )

    backend = _PydaqSerialBackend(config)
    pydaq_serial.serial = backend
    return Installation(pydaq_serial, backend, current)
