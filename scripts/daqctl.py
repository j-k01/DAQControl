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


def _parse_params(items) -> Dict[str, float]:
    params: Dict[str, float] = {}
    for item in items or []:
        if "=" not in item:
            raise daq.DaqControlError(f"param must be NAME=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        params[key.strip()] = float(value)
    return params


def cmd_describe(args: argparse.Namespace) -> int:
    print_result(daq.describe(), args.json)
    return 0


def cmd_dds(args: argparse.Namespace) -> int:
    result = daq.set_dds(freq_hz=args.freq, inc=args.inc,
                         port_name=args.port, baud=args.baud, timeout=args.timeout)
    print_result(result, args.json)
    return 0


def cmd_neuron(args: argparse.Namespace) -> int:
    result = daq.program_neuron(
        target=args.target, profile=args.profile,
        params=_parse_params(args.param) or None,
        port_name=args.port, baud=args.baud, timeout=args.timeout)
    print_result(result, args.json)
    return 0


def cmd_stream(args: argparse.Namespace) -> int:
    result = daq.start_stream(decim=args.decim, cic=args.cic,
                              port_name=args.port, baud=args.baud, timeout=args.timeout)
    print_result(result, args.json)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    result = daq.stop_stream(port_name=args.port, baud=args.baud, timeout=args.timeout)
    print_result(result, args.json)
    return 0


def cmd_cic(args: argparse.Namespace) -> int:
    result = daq.set_cic(args.state == "on",
                         port_name=args.port, baud=args.baud, timeout=args.timeout)
    print_result(result, args.json)
    return 0


def cmd_ping(args: argparse.Namespace) -> int:
    print_result(daq.ping_board(ip=args.ip, count=args.count, timeout=args.timeout), args.json)
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    result = daq.collect_ethernet_burst(
        kb=args.kb, port_name=args.port, baud=args.baud,
        board_ip=args.board_ip, cmd_port=args.cmd_port,
        local_ip=args.local_ip, local_port=args.local_port,
        save=not args.no_save, label=args.label, retries=args.retries)
    print_result(result, args.json)
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    result = daq.uart_capture_snapshot(
        frames=args.frames, port_name=args.port, baud=args.baud,
        timeout=args.timeout, save=not args.no_save, label=args.label)
    print_result(result, args.json)
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

    p = sub.add_parser("describe", help="print server defaults + capabilities")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("dds", help="set the DDS tone (DDSI)")
    add_uart_args(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--freq", type=float, help="DDS frequency in Hz (e.g. 62.5e6)")
    g.add_argument("--inc", type=lambda x: int(x, 0), help="raw 24-bit phase increment")
    p.set_defaults(func=cmd_dds)

    p = sub.add_parser("neuron", help="program a neuron profile and/or params")
    add_uart_args(p)
    p.add_argument("--target", default="all", help="0..3 or 'all'")
    p.add_argument("--profile", choices=list(daq.NEURON_PROFILES))
    p.add_argument("--param", action="append", metavar="NAME=VALUE",
                   help="repeatable; a/b/c/d/i/iconst physical, dt/period/reset raw")
    p.set_defaults(func=cmd_neuron)

    p = sub.add_parser("stream", help="start the cyclic UDP ADC stream")
    add_uart_args(p)
    p.add_argument("--decim", type=int, default=128, help="multiple of 4 in 4..65532")
    p.add_argument("--cic", action="store_true")
    p.set_defaults(func=cmd_stream)

    p = sub.add_parser("stop", help="stop the cyclic UDP ADC stream")
    add_uart_args(p)
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("cic", help="toggle ch2/3 CIC (needs an active stream)")
    add_uart_args(p)
    p.add_argument("state", choices=["on", "off"])
    p.set_defaults(func=cmd_cic)

    p = sub.add_parser("ping", help="ICMP-ping the board A53 ethernet IP")
    p.add_argument("--ip", default=daq.DEFAULT_BOARD_IP)
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("collect", help="one-shot burst capture over Ethernet")
    add_uart_args(p)
    p.add_argument("--kb", type=int, default=64, help="KB per chip (samples/ch = kb*256)")
    p.add_argument("--board-ip", default=daq.DEFAULT_BOARD_IP)
    p.add_argument("--cmd-port", type=int, default=daq.DEFAULT_CMD_PORT)
    p.add_argument("--local-ip", default=daq.DEFAULT_LOCAL_IP)
    p.add_argument("--local-port", type=int, default=daq.DEFAULT_LOCAL_PORT)
    p.add_argument("--label", default="cli")
    p.add_argument("--retries", type=int, default=1, help="extra attempts if UDP drain incomplete")
    p.add_argument("--no-save", action="store_true")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("capture", help="UART PCAP 4-channel snapshot")
    add_uart_args(p)
    p.add_argument("--frames", type=int, default=512, help="1..4096 (samples/ch = frames*4)")
    p.add_argument("--label", default="uart")
    p.add_argument("--no-save", action="store_true")
    p.set_defaults(func=cmd_capture)

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
