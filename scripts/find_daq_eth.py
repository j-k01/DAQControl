#!/usr/bin/env python3
"""Discover the DAQ board on every Ethernet path this PC can reach.

The board's A53 PS-eth app answers a UDP "PING" on its command port (5006)
with "PONG", replying to the *sender's* address -- so a PONG proves a working
round trip on that path, including through a local-network router (the reply
is unicast back to us and routes normally).

What it probes:
  * the known static board IP (192.168.2.10) via the OS default route -- this
    is the "through a router / different subnet" case (needs a route to
    192.168.2.0/24, since the board has a fixed 192.168.2.10/24, no DHCP);
  * every host on each local interface's subnet (direct-attached / same switch);
  * any extra IPs (--target) or subnets (--cidr) you name.
It also reads the ARP table for the board's fixed MAC (00:0A:35:00:01:10) so a
direct-attached board shows up even if PONG is filtered.

  python scripts/find_daq_eth.py
  python scripts/find_daq_eth.py --cidr 10.0.0.0/24      # also sweep a router subnet
  python scripts/find_daq_eth.py --target 192.168.50.77  # probe one IP

Found a board? Drive it with the UART tools / GUI, pointing at the IP shown:
  uv run python scripts/dac_scope_qt.py --port COM10 --board-ip <ip>
(control is still over UART; --board-ip only affects the Ethernet collect path.)
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import select
import socket
import subprocess
import sys
import time

BOARD_MAC = "00:0a:35:00:01:10"     # ZCU102 PS GEM, fixed in the PS-eth app
DEFAULT_BOARD_IP = "192.168.2.10"
DEFAULT_CMD_PORT = 5006


def local_ipv4_interfaces():
    """[{ip, netmask, network}] for each up IPv4 interface. Uses psutil when
    available (accurate masks); otherwise falls back to the host's IP list and
    assumes /24 (the board's subnet size), which is the common case."""
    ifaces = []
    try:
        import psutil  # optional; gives real netmasks
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and a.address and a.netmask:
                    if a.address.startswith("127."):
                        continue
                    try:
                        net = ipaddress.ip_network(f"{a.address}/{a.netmask}",
                                                   strict=False)
                    except ValueError:
                        continue
                    ifaces.append({"ip": a.address, "netmask": a.netmask,
                                   "network": net})
        if ifaces:
            return ifaces
    except Exception:  # noqa: BLE001 -- psutil missing or odd platform
        pass
    # fallback: every IPv4 the host resolves to, assumed /24
    seen = set()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip.startswith("127.") or ip in seen:
                continue
            seen.add(ip)
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
            ifaces.append({"ip": ip, "netmask": "255.255.255.0", "network": net})
    except socket.gaierror:
        pass
    return ifaces


def arp_lookup(mac):
    """Return IPs currently mapped to `mac` in the OS ARP table (best effort)."""
    mac_norm = mac.lower().replace("-", ":")
    mac_re = re.compile(re.escape(mac_norm.replace(":", "")), re.I)
    cmds = (["arp", "-a"], ["ip", "neigh"], ["arp", "-n"])
    hits = []
    for cmd in cmds:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if not out:
            continue
        for line in out.splitlines():
            # normalise any MAC on the line to bare hex and compare
            line_macs = re.findall(r"(?:[0-9a-f]{2}[-:]){5}[0-9a-f]{2}", line, re.I)
            for lm in line_macs:
                if mac_re.search(lm.replace(":", "").replace("-", "")):
                    ip = re.search(r"\d+\.\d+\.\d+\.\d+", line)
                    if ip:
                        hits.append(ip.group(0))
        if hits:
            break
    return sorted(set(hits))


def build_plan(ifaces, args):
    """-> (probes, broadcasts): probes = list of (src_label, src_bind_ip, target);
    broadcasts = list of (src_label, src_bind_ip, bcast_ip). src_bind_ip "" means
    let the OS pick (default route)."""
    probes, broadcasts = [], []
    capped = []

    # 1) known/explicit targets via the default route (the through-a-router case)
    known = [args.board_ip] + list(args.target or [])
    for tgt in known:
        probes.append(("default-route", "", tgt))

    # 2) per-interface subnet sweeps (direct-attached / same switch)
    if not args.no_sweep:
        for itf in ifaces:
            net = itf["network"]
            hosts = list(net.hosts())
            if len(hosts) > args.max_hosts:
                capped.append((str(net), len(hosts)))
                # still probe the known board IP from this interface
                probes.append((itf["ip"], itf["ip"], args.board_ip))
                continue
            for h in hosts:
                probes.append((itf["ip"], itf["ip"], str(h)))
            if net.num_addresses > 2:
                broadcasts.append((itf["ip"], itf["ip"],
                                   str(net.broadcast_address)))

    # 3) explicit extra subnets via the default route
    for cidr in (args.cidr or []):
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            print(f"  ! ignoring bad --cidr {cidr}", file=sys.stderr)
            continue
        hosts = list(net.hosts())
        if len(hosts) > args.max_hosts:
            capped.append((str(net), len(hosts)))
            continue
        for h in hosts:
            probes.append((f"cidr {net}", "", str(h)))

    for net, n in capped:
        print(f"  ! {net} has {n} hosts (> --max-hosts {args.max_hosts}); "
              f"NOT swept. Narrow it with --cidr or raise --max-hosts.",
              file=sys.stderr)
    return probes, broadcasts


def discover(probes, broadcasts, cmd_port, timeout):
    """Send PING on every (src,target) and collect PONG replies.
    -> {board_ip: {"via": src_label, "rtt_ms": float}}."""
    socks = {}            # bind_ip -> socket
    labels = {}           # id(sock) -> default src_label for that bind
    sendtime = {}         # (bind_ip, target_ip) -> t
    found = {}

    def sock_for(bind_ip, label):
        if bind_ip not in socks:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
            try:
                s.bind((bind_ip, 0))
            except OSError as e:
                print(f"  ! cannot bind {bind_ip or '0.0.0.0'}: {e}",
                      file=sys.stderr)
                return None
            s.setblocking(False)
            socks[bind_ip] = s
            labels[id(s)] = label
        return socks[bind_ip]

    n_sent = 0
    for label, bind_ip, tgt in probes:
        s = sock_for(bind_ip, label)
        if s is None:
            continue
        try:
            s.sendto(b"PING", (tgt, cmd_port))
            sendtime[(bind_ip, tgt)] = time.time()
            n_sent += 1
        except OSError:
            pass
    for label, bind_ip, bcast in broadcasts:
        s = sock_for(bind_ip, label)
        if s is None:
            continue
        try:
            s.sendto(b"PING", (bcast, cmd_port))
            n_sent += 1
        except OSError:
            pass

    print(f"  sent {n_sent} PING probes from {len(socks)} interface(s); "
          f"listening {timeout:.1f}s...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select(list(socks.values()), [], [], 0.2)
        for s in ready:
            try:
                data, addr = s.recvfrom(256)
            except OSError:
                continue
            if data.strip() != b"PONG":
                continue
            bip = addr[0]
            bind_ip = s.getsockname()[0]
            t0 = sendtime.get((bind_ip, bip)) or sendtime.get(("", bip))
            rtt = (time.time() - t0) * 1000.0 if t0 else float("nan")
            if bip not in found:
                via = bind_ip if bind_ip not in ("", "0.0.0.0") \
                    else labels.get(id(s), "default-route")
                found[bip] = {"via": via, "rtt_ms": rtt}
    for s in socks.values():
        s.close()
    return found


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board-ip", default=DEFAULT_BOARD_IP,
                    help="known static board IP, always probed (default %(default)s)")
    ap.add_argument("--cmd-port", type=int, default=DEFAULT_CMD_PORT)
    ap.add_argument("--target", action="append",
                    help="extra IP to probe (repeatable)")
    ap.add_argument("--cidr", action="append",
                    help="extra subnet to sweep, e.g. 10.0.0.0/24 (repeatable)")
    ap.add_argument("--timeout", type=float, default=2.0,
                    help="seconds to listen for replies (default %(default)s)")
    ap.add_argument("--max-hosts", type=int, default=1024,
                    help="skip sweeping subnets larger than this (default %(default)s)")
    ap.add_argument("--no-sweep", action="store_true",
                    help="only probe known/--target/--cidr IPs, no interface sweeps")
    ap.add_argument("--mac", default=BOARD_MAC,
                    help="board MAC for the ARP cross-check (default %(default)s)")
    args = ap.parse_args()

    ifaces = local_ipv4_interfaces()
    print("Local IPv4 interfaces:")
    if ifaces:
        for itf in ifaces:
            print(f"  {itf['ip']:<16} / {itf['netmask']:<15} -> {itf['network']}")
    else:
        print("  (none found; subnet sweep skipped -- known IP still probed)")

    probes, broadcasts = build_plan(ifaces, args)
    print("\nProbing for the DAQ board (UDP PING -> PONG on "
          f"port {args.cmd_port})...")
    found = discover(probes, broadcasts, args.cmd_port, args.timeout)

    arp_ips = arp_lookup(args.mac)

    print("\n=== Results ===")
    if found:
        for ip, info in sorted(found.items(),
                               key=lambda kv: ipaddress.ip_address(kv[0])):
            mac_note = "  [MAC matches board]" if ip in arp_ips else ""
            rtt = info["rtt_ms"]
            rtt_s = f"{rtt:.1f} ms" if rtt == rtt else "n/a"
            print(f"  BOARD at {ip}  via {info['via']}  (PONG, rtt {rtt_s})"
                  f"{mac_note}")
        first = sorted(found, key=lambda x: ipaddress.ip_address(x))[0]
        print(f"\nUse it:  uv run python scripts/dac_scope_qt.py --port COM10 "
              f"--board-ip {first}")
    else:
        print("  No PONG from any path.")
        if arp_ips:
            print("  But the board MAC appears in the ARP table at: "
                  + ", ".join(arp_ips))
            print("  -> reachable at L2 but PONG was blocked (firewall?) or the "
                  "cmd port differs. Try: --target " + arp_ips[0])
        else:
            print("  Checks: board powered + PS-eth app loaded (load_ps_eth_"
                  "stream.tcl)?  cable/link up?  If it's behind a router, the "
                  "board is still 192.168.2.10/24 -- you need a route to that "
                  "subnet, or put this PC on 192.168.2.0/24.")
    # show any ARP MAC hits we didn't already PONG (e.g., wrong subnet on us)
    extra = [ip for ip in arp_ips if ip not in found]
    if extra:
        print("  ARP also shows the board MAC at (no PONG): " + ", ".join(extra))


if __name__ == "__main__":
    main()
