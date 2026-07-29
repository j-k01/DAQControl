#!/usr/bin/env python3
"""Pyserial-shaped access to a Pico attached to the ZCU102 USB host.

Typical migration from a directly attached Pico is intentionally one import:

    # import serial
    import fpga_pico_serial as serial

    with serial.Serial("COM10", 115200, timeout=1) as pico:
        pico.write(b"existing Pico command\n")
        reply = pico.readline()

The baud rate describes the MicroBlaze console when UART fallback is used; the
Pico itself is a USB CDC endpoint. ``transport="auto"`` probes Ethernet once
when the object is opened and otherwise uses COM10 for the object's lifetime.
"""

from __future__ import annotations

import os
import random
import re
import socket
import struct
import time
from typing import Optional


HEADER = struct.Struct("<4sBBHIHH")
VERSION = 1
UDP_PORT = 5007
MAX_BYTES = 128
PCDC_PROBE = 0
PCDC_WRITE = 1
PCDC_READ = 2
PCDC_FLUSH = 3

# Common pyserial constants, so existing code that configures these explicitly
# can keep using the replacement module as the ``serial`` namespace.
FIVEBITS = 5
SIXBITS = 6
SEVENBITS = 7
EIGHTBITS = 8
PARITY_NONE = "N"
PARITY_EVEN = "E"
PARITY_ODD = "O"
PARITY_MARK = "M"
PARITY_SPACE = "S"
STOPBITS_ONE = 1
STOPBITS_ONE_POINT_FIVE = 1.5
STOPBITS_TWO = 2


class SerialException(OSError):
    """Transport or remote Pico bridge failure."""


class SerialTimeoutException(SerialException):
    """Write operation exceeded its configured timeout."""


class Serial:
    """Small, compatible subset of :class:`serial.Serial`.

    The normal pyserial ``port`` and ``baudrate`` arguments are retained for
    source compatibility with a controller that was configured for the Pico's
    former direct PC connection. The physical FPGA console independently
    defaults to COM10 at 115200 baud.
    """

    def __init__(
        self,
        port: Optional[str] = "COM10",
        baudrate: int = 115200,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1,
        timeout: Optional[float] = None,
        xonxoff: bool = False,
        rtscts: bool = False,
        write_timeout: Optional[float] = None,
        dsrdtr: bool = False,
        inter_byte_timeout: Optional[float] = None,
        exclusive: Optional[bool] = None,
        *,
        transport: str = "auto",
        board_ip: str = "192.168.2.10",
        local_ip: str = "192.168.2.1",
        udp_port: int = UDP_PORT,
        ethernet_probe_timeout: float = 0.35,
        fpga_uart_port: Optional[str] = None,
        fpga_uart_baudrate: int = 115200,
    ) -> None:
        if transport not in ("auto", "ethernet", "uart"):
            raise ValueError("transport must be auto, ethernet, or uart")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be nonnegative or None")
        if write_timeout is not None and write_timeout < 0:
            raise ValueError("write_timeout must be nonnegative or None")

        self.port = port
        self.name = self.port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.xonxoff = xonxoff
        self.rtscts = rtscts
        self.dsrdtr = dsrdtr
        self.inter_byte_timeout = inter_byte_timeout
        self.exclusive = exclusive
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.fpga_uart_port = (
            fpga_uart_port
            or os.environ.get("FPGA_PICO_UART_PORT")
            or "COM10"
        )
        self.fpga_uart_baudrate = fpga_uart_baudrate
        self.board_ip = board_ip
        self.local_ip = local_ip
        self.udp_port = udp_port
        self._requested_transport = transport
        self._transport = transport
        self._ethernet_probe_timeout = ethernet_probe_timeout
        self._uart = None
        self._read_buffer = bytearray()
        self.is_open = False
        self.open()

    @property
    def transport(self) -> str:
        """The selected transport after automatic probing."""
        return self._transport

    def open(self) -> None:
        if self.is_open:
            return
        if self._requested_transport == "auto":
            try:
                self._ethernet_rpc(
                    PCDC_PROBE, b"", 0, self._ethernet_probe_timeout
                )
                self._transport = "ethernet"
            except SerialException:
                self._transport = "uart"
        if self._transport == "uart":
            self._open_uart()
        self.is_open = True

    def close(self) -> None:
        if self._uart is not None:
            self._uart.close()
            self._uart = None
        self.is_open = False

    def __enter__(self) -> "Serial":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def _require_open(self) -> None:
        if not self.is_open:
            raise SerialException("port is closed")

    def _open_uart(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise SerialException(
                "pyserial is required for FPGA UART fallback"
            ) from exc
        try:
            self._uart = serial.Serial(
                self.fpga_uart_port,
                self.fpga_uart_baudrate,
                timeout=0.1,
                write_timeout=self.write_timeout,
            )
            self._uart.reset_input_buffer()
        except (OSError, serial.SerialException) as exc:
            raise SerialException(
                f"cannot open FPGA console {self.fpga_uart_port}: {exc}"
            ) from exc

    def _ethernet_rpc(
        self,
        operation: int,
        payload: bytes,
        requested_length: int,
        timeout: float,
    ) -> bytes:
        sequence = random.getrandbits(32)
        timeout_ms = max(0, min(60000, int(round(timeout * 1000))))
        length = len(payload) if operation == PCDC_WRITE else requested_length
        request = HEADER.pack(
            b"PCDC", VERSION, operation, timeout_ms,
            sequence, length, 0
        ) + payload
        socket_timeout = max(0.1, timeout + 0.25)

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind((self.local_ip, 0))
                sock.settimeout(socket_timeout)
                sock.sendto(request, (self.board_ip, self.udp_port))
                while True:
                    response, peer = sock.recvfrom(HEADER.size + MAX_BYTES)
                    if peer[0] != self.board_ip or len(response) < HEADER.size:
                        continue
                    (
                        magic,
                        version,
                        status,
                        rx_length,
                        reply_sequence,
                        _,
                        _,
                    ) = HEADER.unpack_from(response)
                    if (
                        magic != b"PCDR"
                        or version != VERSION
                        or reply_sequence != sequence
                    ):
                        continue
                    data = response[HEADER.size:]
                    if status != 0:
                        raise SerialException(
                            f"FPGA Pico bridge returned status {status}"
                        )
                    if rx_length != len(data):
                        raise SerialException(
                            "FPGA Pico bridge response length mismatch"
                        )
                    return data
        except (OSError, socket.timeout) as exc:
            raise SerialException(f"FPGA Pico Ethernet bridge failed: {exc}") from exc

    def _uart_command(self, command: str, timeout: float) -> str:
        if self._uart is None:
            raise SerialException("FPGA UART transport is not open")
        self._uart.reset_input_buffer()
        try:
            self._uart.write((command + "\n").encode("ascii"))
            self._uart.flush()
        except OSError as exc:
            raise SerialException(f"FPGA UART write failed: {exc}") from exc

        deadline = time.monotonic() + max(timeout, 0.25)
        while time.monotonic() < deadline:
            line = self._uart.readline().decode(
                "ascii", errors="replace"
            ).strip()
            if line.startswith("ERR PICO"):
                raise SerialException(line)
            if line.startswith("OK PICO"):
                return line
        raise SerialTimeoutException("FPGA Pico UART bridge timed out")

    def _write_chunk(self, data: bytes) -> None:
        if self._transport == "ethernet":
            timeout = self.write_timeout if self.write_timeout is not None else 1.0
            self._ethernet_rpc(PCDC_WRITE, data, 0, timeout)
            return
        line = self._uart_command(
            f"PICO W {data.hex()}",
            self.write_timeout if self.write_timeout is not None else 2.0,
        )
        match = re.search(r"\bn=(\d+)$", line)
        if match is None or int(match.group(1)) != len(data):
            raise SerialException(f"malformed FPGA UART write response: {line}")

    def write(self, data: bytes | bytearray | memoryview) -> int:
        self._require_open()
        raw = bytes(data)
        for offset in range(0, len(raw), MAX_BYTES):
            self._write_chunk(raw[offset:offset + MAX_BYTES])
        return len(raw)

    def _read_chunk(self, capacity: int, timeout: float) -> bytes:
        capacity = max(1, min(MAX_BYTES, capacity))
        if self._transport == "ethernet":
            return self._ethernet_rpc(
                PCDC_READ, b"", capacity, timeout
            )
        timeout_ms = max(0, min(60000, int(round(timeout * 1000))))
        line = self._uart_command(
            f"PICO R {capacity} {timeout_ms}", timeout + 1.0
        )
        match = re.search(r"\bn=(\d+)\s+data=([0-9A-Fa-f]*)$", line)
        if match is None:
            raise SerialException(f"malformed FPGA UART read response: {line}")
        data = bytes.fromhex(match.group(2))
        if int(match.group(1)) != len(data):
            raise SerialException(f"FPGA UART read length mismatch: {line}")
        return data

    def _remaining_timeout(self, deadline: Optional[float]) -> float:
        if deadline is None:
            return 1.0
        return max(0.0, deadline - time.monotonic())

    def read(self, size: int = 1) -> bytes:
        self._require_open()
        if size <= 0:
            return b""
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        while len(self._read_buffer) < size:
            remaining = self._remaining_timeout(deadline)
            if deadline is not None and remaining <= 0 and self.timeout != 0:
                break
            chunk = self._read_chunk(
                min(MAX_BYTES, size - len(self._read_buffer)), remaining
            )
            if chunk:
                self._read_buffer.extend(chunk)
            elif deadline is not None:
                break
        result = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return result

    def read_until(self, expected: bytes = b"\n", size: Optional[int] = None) -> bytes:
        self._require_open()
        if not expected:
            raise ValueError("expected terminator must not be empty")
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        while True:
            end = self._read_buffer.find(expected)
            if end >= 0:
                end += len(expected)
                result = bytes(self._read_buffer[:end])
                del self._read_buffer[:end]
                return result
            if size is not None and len(self._read_buffer) >= size:
                result = bytes(self._read_buffer[:size])
                del self._read_buffer[:size]
                return result
            remaining = self._remaining_timeout(deadline)
            if deadline is not None and remaining <= 0 and self.timeout != 0:
                result = bytes(self._read_buffer)
                self._read_buffer.clear()
                return result
            capacity = MAX_BYTES
            if size is not None:
                capacity = min(capacity, max(1, size - len(self._read_buffer)))
            chunk = self._read_chunk(capacity, remaining)
            if chunk:
                self._read_buffer.extend(chunk)
            elif deadline is not None:
                result = bytes(self._read_buffer)
                self._read_buffer.clear()
                return result

    def readline(self, size: int = -1) -> bytes:
        return self.read_until(b"\n", None if size < 0 else size)

    @property
    def in_waiting(self) -> int:
        self._require_open()
        self._read_buffer.extend(self._read_chunk(MAX_BYTES, 0.0))
        return len(self._read_buffer)

    def read_all(self) -> bytes:
        self._require_open()
        while True:
            chunk = self._read_chunk(MAX_BYTES, 0.0)
            if not chunk:
                break
            self._read_buffer.extend(chunk)
        result = bytes(self._read_buffer)
        self._read_buffer.clear()
        return result

    def flush(self) -> None:
        self._require_open()
        if self._uart is not None:
            self._uart.flush()

    def reset_input_buffer(self) -> None:
        self._require_open()
        self._read_buffer.clear()
        if self._transport == "ethernet":
            self._ethernet_rpc(PCDC_FLUSH, b"", 0, 0.25)
        else:
            self._uart_command("PICO F", 1.0)

    def reset_output_buffer(self) -> None:
        self._require_open()


serial_for_url = Serial
