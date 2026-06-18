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


SERVER_INFO = {"name": "daq-launch-control", "version": "0.1.0"}


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
