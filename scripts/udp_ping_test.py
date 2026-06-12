"""Send PING to the PS Ethernet app and report the reply.

Usage: python udp_ping_test.py [board_ip] [cmd_port] [local_port]
"""
import socket
import sys

board_ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.2.10"
cmd_port = int(sys.argv[2]) if len(sys.argv) > 2 else 5006
local_port = int(sys.argv[3]) if len(sys.argv) > 3 else 5005

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("192.168.2.1", local_port))
s.settimeout(3.0)
s.sendto(b"PING", (board_ip, cmd_port))
try:
    data, addr = s.recvfrom(2048)
    print("reply from %s:%d -> %r" % (addr[0], addr[1], data))
except socket.timeout:
    print("TIMEOUT: no UDP reply")
finally:
    s.close()
