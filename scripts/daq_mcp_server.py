#!/usr/bin/env python3
"""Minimal MCP stdio server for DAQ_LAUNCH board control.

This deliberately avoids a hard dependency on the MCP Python SDK.  It implements
the small JSON-RPC surface MCP clients need for tools/list and tools/call over
newline-delimited stdio messages.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import daq_control as daq


SERVER_INFO = {"name": "daq-launch-control", "version": "0.2.0"}


def _text_result(value: Any, is_error: bool = False) -> Dict[str, Any]:
    text = value if isinstance(value, str) else daq.json_dumps(value)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _schema(properties: Dict[str, Any], required: Optional[list[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _uart_args(**extra: Any) -> Dict[str, Any]:
    """Common UART connection properties, with tool-specific props placed first."""
    props: Dict[str, Any] = dict(extra)
    props.update(
        {
            "port": {"type": "string", "default": daq.DEFAULT_PORT},
            "baud": {"type": "integer", "default": daq.DEFAULT_BAUD},
            "timeout": {"type": "number", "default": daq.DEFAULT_UART_TIMEOUT},
        }
    )
    return props


TOOLS: Dict[str, Dict[str, Any]] = {
    "daq_list_uart_ports": {
        "description": "List serial ports visible to the local host.",
        "inputSchema": _schema({}),
    },
    "daq_status": {
        "description": "Read firmware STAT over the local PL UART and parse key fields.",
        "inputSchema": _schema(
            {
                "port": {"type": "string", "default": daq.DEFAULT_PORT},
                "baud": {"type": "integer", "default": daq.DEFAULT_BAUD},
                "timeout": {"type": "number", "default": daq.DEFAULT_UART_TIMEOUT},
            }
        ),
    },
    "daq_uart_command": {
        "description": "Send raw line-oriented UART commands to the DAQ firmware.",
        "inputSchema": _schema(
            {
                "commands": {"type": "array", "items": {"type": "string"}},
                "port": {"type": "string", "default": daq.DEFAULT_PORT},
                "baud": {"type": "integer", "default": daq.DEFAULT_BAUD},
                "timeout": {"type": "number", "default": daq.DEFAULT_UART_TIMEOUT},
            },
            required=["commands"],
        ),
    },
    "daq_set_dac_routes": {
        "description": "Set legal DAC source routes, then read back STAT.",
        "inputSchema": _schema(
            {
                "routes": {
                    "type": "object",
                    "description": "Map DAC channel numbers 0..3 to source names.",
                    "additionalProperties": {"type": "string"},
                },
                "port": {"type": "string", "default": daq.DEFAULT_PORT},
                "baud": {"type": "integer", "default": daq.DEFAULT_BAUD},
                "timeout": {"type": "number", "default": daq.DEFAULT_UART_TIMEOUT},
            },
            required=["routes"],
        ),
    },
    "daq_program_board_via_capitolpeak": {
        "description": "SSH to capitolpeak, pull the selected branch, and run program_board.tcl.",
        "inputSchema": _schema(
            {
                "host": {"type": "string", "default": daq.DEFAULT_REMOTE_HOST},
                "user": {"type": "string", "default": daq.DEFAULT_REMOTE_USER},
                "remote_repo": {"type": "string", "default": daq.DEFAULT_REMOTE_REPO},
                "branch": {"type": "string", "default": daq.DEFAULT_BRANCH},
                "ssh_key": {"type": "string"},
                "xilinx_wrapper": {"type": "string", "default": daq.DEFAULT_XILINX_WRAPPER},
                "timeout": {"type": "number", "default": 600.0},
                "dry_run": {"type": "boolean", "default": False},
            }
        ),
    },
    "daq_describe": {
        "description": "Static capability + defaults summary (DAC sources, neuron profiles/params, ethernet + remote defaults). No board I/O.",
        "inputSchema": _schema({}),
    },
    "daq_set_dds": {
        "description": "Set the DDS tone via DDSI. Provide freq_hz or a raw 24-bit inc (0..0xFFFFFF; 0 = HDL default). Route a DAC to 'dds' to emit it.",
        "inputSchema": _schema(
            _uart_args(
                freq_hz={"type": "number", "description": "DDS frequency in Hz (e.g. 62.5e6)"},
                inc={"type": "integer", "description": "raw 24-bit phase increment; overrides freq_hz"},
            )
        ),
    },
    "daq_program_neuron": {
        "description": "Program a neuron (0..3 or 'all') with a built-in profile and/or params. Params a/b/c/d/i/iconst are physical (converted to Q16.16); dt/period/reset are raw integers.",
        "inputSchema": _schema(
            _uart_args(
                target={"type": ["string", "integer"], "default": "all"},
                profile={"type": "string", "enum": list(daq.NEURON_PROFILES)},
                params={"type": "object", "additionalProperties": {"type": "number"}},
            )
        ),
    },
    "daq_start_stream": {
        "description": "Start the cyclic UDP ADC stream (STRM(decim) [cic]). decim must be a multiple of 4 in 4..65532.",
        "inputSchema": _schema(
            _uart_args(
                decim={"type": "integer", "default": 128},
                cic={"type": "boolean", "default": False},
            )
        ),
    },
    "daq_stop_stream": {
        "description": "Stop the cyclic UDP ADC stream (STRM STOP).",
        "inputSchema": _schema(_uart_args()),
    },
    "daq_set_cic": {
        "description": "Toggle the chip-1 (ch2/3) CIC anti-alias filter (STRM CIC on|off). Requires an active stream.",
        "inputSchema": _schema(_uart_args(on={"type": "boolean"}), required=["on"]),
    },
    "daq_ping_board": {
        "description": "ICMP-ping the board's A53 ethernet IP to confirm the PS-eth app is up (independent of UART).",
        "inputSchema": _schema(
            {
                "ip": {"type": "string", "default": daq.DEFAULT_BOARD_IP},
                "count": {"type": "integer", "default": 3},
                "timeout": {"type": "number", "default": 10.0},
            }
        ),
    },
    "daq_collect_ethernet": {
        "description": "One-shot full-rate burst capture over Ethernet (BCAP+BRDO+UDP drain). Returns per-channel summary + coverage and saves a .npz. The reliable way to read the ADC.",
        "inputSchema": _schema(
            _uart_args(
                kb={"type": "integer", "default": 64, "description": "KB per chip (samples/ch = kb*256)"},
                save={"type": "boolean", "default": True},
                label={"type": "string", "default": "mcp"},
                retries={"type": "integer", "default": 1, "description": "extra attempts if the UDP drain is incomplete"},
                board_ip={"type": "string", "default": daq.DEFAULT_BOARD_IP},
                cmd_port={"type": "integer", "default": daq.DEFAULT_CMD_PORT},
                local_ip={"type": "string", "default": daq.DEFAULT_LOCAL_IP},
                local_port={"type": "integer", "default": daq.DEFAULT_LOCAL_PORT},
                drain_timeout={"type": "number"},
                capture_dir={"type": "string"},
            )
        ),
    },
    "daq_uart_capture": {
        "description": "Grab a 4-channel ADC snapshot over UART (PCAP), no Ethernet needed. samples/ch = frames*4 (frames 1..4096). Returns a per-channel summary and saves a .npz.",
        "inputSchema": _schema(
            _uart_args(
                frames={"type": "integer", "default": 512},
                save={"type": "boolean", "default": True},
                label={"type": "string", "default": "uart"},
                capture_dir={"type": "string"},
            )
        ),
    },
}


def _tool_list() -> Dict[str, Any]:
    return {
        "tools": [
            {
                "name": name,
                "description": meta["description"],
                "inputSchema": meta["inputSchema"],
            }
            for name, meta in TOOLS.items()
        ]
    }


def _routes_from_args(value: Dict[str, Any]) -> Dict[int, str]:
    routes: Dict[int, str] = {}
    for key, source in value.items():
        if isinstance(key, str) and key.lower().startswith("dac"):
            key = key[3:]
        channel = int(key)
        routes[channel] = str(source)
    return routes


def call_tool(name: str, args: Dict[str, Any]) -> Any:
    if name == "daq_list_uart_ports":
        return daq.list_uart_ports()
    if name == "daq_status":
        return daq.read_status(
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
        )
    if name == "daq_uart_command":
        return daq.send_uart_commands(
            [str(command) for command in args["commands"]],
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
        )
    if name == "daq_set_dac_routes":
        return daq.set_dac_routes(
            _routes_from_args(dict(args["routes"])),
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
        )
    if name == "daq_program_board_via_capitolpeak":
        cfg = daq.RemoteConfig(
            host=args.get("host", daq.DEFAULT_REMOTE_HOST),
            user=args.get("user", daq.DEFAULT_REMOTE_USER),
            repo=args.get("remote_repo", daq.DEFAULT_REMOTE_REPO),
            branch=args.get("branch", daq.DEFAULT_BRANCH),
            ssh_key=Path(args["ssh_key"]) if args.get("ssh_key") else None,
            xilinx_wrapper=args.get("xilinx_wrapper", daq.DEFAULT_XILINX_WRAPPER),
        )
        return daq.program_board_via_capitolpeak(
            cfg,
            timeout=float(args.get("timeout", 600.0)),
            dry_run=bool(args.get("dry_run", False)),
        )
    if name == "daq_describe":
        return daq.describe()
    if name == "daq_set_dds":
        return daq.set_dds(
            freq_hz=args.get("freq_hz"),
            inc=args.get("inc"),
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
        )
    if name == "daq_program_neuron":
        return daq.program_neuron(
            target=args.get("target", "all"),
            profile=args.get("profile"),
            params=args.get("params"),
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
        )
    if name == "daq_start_stream":
        return daq.start_stream(
            decim=int(args.get("decim", 128)),
            cic=bool(args.get("cic", False)),
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
        )
    if name == "daq_stop_stream":
        return daq.stop_stream(
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
        )
    if name == "daq_set_cic":
        return daq.set_cic(
            bool(args["on"]),
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
        )
    if name == "daq_ping_board":
        return daq.ping_board(
            ip=args.get("ip", daq.DEFAULT_BOARD_IP),
            count=int(args.get("count", 3)),
            timeout=float(args.get("timeout", 10.0)),
        )
    if name == "daq_collect_ethernet":
        drain = args.get("drain_timeout")
        return daq.collect_ethernet_burst(
            kb=int(args.get("kb", 64)),
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            board_ip=args.get("board_ip", daq.DEFAULT_BOARD_IP),
            cmd_port=int(args.get("cmd_port", daq.DEFAULT_CMD_PORT)),
            local_ip=args.get("local_ip", daq.DEFAULT_LOCAL_IP),
            local_port=int(args.get("local_port", daq.DEFAULT_LOCAL_PORT)),
            drain_timeout=float(drain) if drain is not None else None,
            save=bool(args.get("save", True)),
            capture_dir=args.get("capture_dir"),
            label=str(args.get("label", "mcp")),
            retries=int(args.get("retries", 1)),
        )
    if name == "daq_uart_capture":
        return daq.uart_capture_snapshot(
            frames=int(args.get("frames", 512)),
            port_name=args.get("port", daq.DEFAULT_PORT),
            baud=int(args.get("baud", daq.DEFAULT_BAUD)),
            timeout=float(args.get("timeout", daq.DEFAULT_UART_TIMEOUT)),
            save=bool(args.get("save", True)),
            capture_dir=args.get("capture_dir"),
            label=str(args.get("label", "uart")),
        )
    raise daq.DaqControlError(f"unknown tool: {name}")


def handle_request(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method and method.startswith("notifications/"):
        return None
    if method == "initialize":
        protocol = params.get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": _tool_list()}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(str(name), dict(arguments))
            return {"jsonrpc": "2.0", "id": msg_id, "result": _text_result(result)}
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            return {"jsonrpc": "2.0", "id": msg_id, "result": _text_result(str(exc), True)}
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = handle_request(message)
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
