#!/usr/bin/env python3
"""Stable command-line control surface for DAQ_LAUNCH.

Examples:
  python scripts/daqctl.py ports
  python scripts/daqctl.py status --json
  python scripts/daqctl.py route --dac0 current --dac1 monitor0 --dac2 spike0
  python scripts/daqctl.py program --host capitolpeak.ece.ucdavis.edu
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional

import daq_control as daq


def add_uart_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", default=daq.DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=daq.DEFAULT_BAUD)
    parser.add_argument("--timeout", type=float, default=daq.DEFAULT_UART_TIMEOUT)
    parser.add_argument("--json", action="store_true", help="print structured JSON")


def print_result(value, as_json: bool) -> None:
    if as_json:
        print(daq.json_dumps(value))
    else:
        if isinstance(value, str):
            print(value)
        else:
            print(daq.json_dumps(value))


def cmd_ports(args: argparse.Namespace) -> int:
    print_result(daq.list_uart_ports(), args.json)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    result = daq.read_status(args.port, args.baud, args.timeout)
    print_result(result, args.json)
    return 0


def cmd_uart(args: argparse.Namespace) -> int:
    result = daq.send_uart_commands(
        args.commands,
        port_name=args.port,
        baud=args.baud,
        timeout=args.timeout,
    )
    print_result(result, args.json)
    return 0


def _route_args(args: argparse.Namespace) -> Dict[int, str]:
    routes: Dict[int, str] = {}
    for ch in range(4):
        value = getattr(args, f"dac{ch}")
        if value is not None:
            routes[ch] = value
    if not routes:
        raise daq.DaqControlError("provide at least one --dacN source")
    return routes


def cmd_route(args: argparse.Namespace) -> int:
    result = daq.set_dac_routes(_route_args(args), args.port, args.baud, args.timeout)
    print_result(result, args.json)
    return 0


def cmd_program(args: argparse.Namespace) -> int:
    cfg = daq.RemoteConfig(
        host=args.host,
        user=args.user,
        repo=args.remote_repo,
        branch=args.branch,
        ssh_key=Path(args.ssh_key) if args.ssh_key else None,
        xilinx_wrapper=args.xilinx_wrapper,
    )
    result = daq.program_board_via_capitolpeak(cfg, timeout=args.timeout, dry_run=args.dry_run)
    print_result(result, args.json or args.dry_run)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ports", help="list visible serial ports")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("status", help="read DAQ firmware STAT over UART")
    add_uart_args(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("uart", help="send raw firmware UART commands")
    add_uart_args(p)
    p.add_argument("commands", nargs="+")
    p.set_defaults(func=cmd_uart)

    p = sub.add_parser("route", help="set DAC source crossbar routes")
    add_uart_args(p)
    for ch in range(4):
        p.add_argument(f"--dac{ch}", metavar="SOURCE", help="source for DAC channel")
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("program", help="program board through capitolpeak")
    p.add_argument("--host", default=daq.DEFAULT_REMOTE_HOST)
    p.add_argument("--user", default=daq.DEFAULT_REMOTE_USER)
    p.add_argument("--remote-repo", default=daq.DEFAULT_REMOTE_REPO)
    p.add_argument("--branch", default=daq.DEFAULT_BRANCH)
    p.add_argument("--ssh-key", default=None)
    p.add_argument("--xilinx-wrapper", default=daq.DEFAULT_XILINX_WRAPPER)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_program)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except daq.DaqControlError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
