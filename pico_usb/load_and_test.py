from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

try:
    import serial
except ImportError:
    serial = None


ROOT = Path(__file__).resolve().parent
PREBUILT = ROOT / "prebuilt"
REMOTE_DIR = "/tmp/daq_pico_usb"
SUCCESS = b"PICO-HOST: READY - existing Pico CDC firmware preserved"

ARTIFACTS = {
    "bitstream": "top.bit",
    "fsbl": "zynqmp_fsbl.elf",
    "pmufw": "zynqmp_pmufw.elf",
    "bl31": "bl31.elf",
    "uboot": "u-boot",
    "dtb": "u-boot.dtb",
    "linux_image": "Image",
    "linux_dtb": "system.dtb",
    "initramfs": "pico-initramfs.cpio.gz",
    "mb_firmware": "firmware.elf",
    "psu_init": "psu_init.tcl",
}


def command_bases(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    common = ["-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes"]
    if args.identity:
        common += ["-i", str(Path(args.identity).expanduser())]
    return ["ssh", *common, args.remote], ["scp", *common]


def run_checked(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **kwargs,
    )
    if result.returncode:
        text = result.stdout.decode("utf-8", errors="replace")
        raise RuntimeError(f"command failed ({result.returncode}):\n{text}")
    return result


def require_artifacts() -> dict[str, Path]:
    paths = {
        name: PREBUILT / filename
        for name, filename in ARTIFACTS.items()
        if name not in {"bitstream", "mb_firmware", "psu_init"}
    }
    paths["bitstream"] = ROOT.parent / "prebuilt" / "top.bit"
    paths["mb_firmware"] = ROOT.parent / "prebuilt" / "firmware.elf"
    paths["psu_init"] = ROOT.parent / "prebuilt" / "psu_init.tcl"
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise RuntimeError("missing prebuilt artifact(s):\n  " + "\n  ".join(missing))
    manifest_path = PREBUILT / "SHA256SUMS"
    expected = {}
    for line in manifest_path.read_text(encoding="ascii").splitlines():
        digest, filename = line.split(maxsplit=1)
        expected[filename.strip()] = digest
    for path in paths.values():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected.get(path.name):
            raise RuntimeError(f"prebuilt artifact checksum mismatch: {path}")
    return paths


def discover_xsdb(ssh: list[str]) -> str:
    command = (
        "bash -lc 'command -v xsdb || "
        "find \"$HOME/Xilinx\" -path \"*/bin/xsdb\" -type f 2>/dev/null "
        "| sort -V | tail -1'"
    )
    result = run_checked([*ssh, command], timeout=20)
    path = result.stdout.decode("utf-8", errors="replace").strip().splitlines()
    if not path:
        raise RuntimeError("could not find xsdb on the JTAG host")
    return path[-1]


def make_tcl() -> str:
    return f"""\
set work_dir "{REMOTE_DIR}"
set bitstream [file join $work_dir {ARTIFACTS["bitstream"]}]
set fsbl [file join $work_dir {ARTIFACTS["fsbl"]}]
set pmufw [file join $work_dir {ARTIFACTS["pmufw"]}]
set bl31 [file join $work_dir {ARTIFACTS["bl31"]}]
set uboot [file join $work_dir {ARTIFACTS["uboot"]}]
set dtb [file join $work_dir {ARTIFACTS["dtb"]}]
set linux_image [file join $work_dir {ARTIFACTS["linux_image"]}]
set linux_dtb [file join $work_dir {ARTIFACTS["linux_dtb"]}]
set initramfs [file join $work_dir {ARTIFACTS["initramfs"]}]
set mb_firmware [file join $work_dir {ARTIFACTS["mb_firmware"]}]
set psu_init_tcl [file join $work_dir {ARTIFACTS["psu_init"]}]
set dtb_addr 0x00100000
set linux_addr 0x08000000
set linux_dtb_addr 0x04000000
set initramfs_addr 0x0C000000

connect
targets -set -filter {{name =~ "PSU"}}
rst -system
after 3000
mwr 0xffca0038 0x1ff

if {{[catch {{targets -set -filter {{name =~ "MicroBlaze PMU"}}}}]}} {{
    targets -set -filter {{name =~ "PMU"}}
}}
catch {{stop}}
rst -processor
dow $pmufw
con
after 1000

# FSBL establishes the standard ZynqMP handoff state required by BL31.  The
# DAQ-generated psu_init below is still run after PL configuration to restore
# the exact HP-port and PS-to-PL settings used by the known-good DAQ flow.
targets -set -filter {{name =~ "Cortex-A53 #0"}}
catch {{stop}}
rst -processor
dow $fsbl
con
after 20000
stop

# Configure the PL after the system reset.  A system reset after `fpga` erases
# the configuration and was invisible in the original USB-only proof.
targets -set -filter {{name =~ "PL"}}
fpga -file $bitstream
after 1000

# Use the exact, known-good DAQ JTAG initialization path.  It configures DDR,
# PS clocks, and the PS-to-PL HP ports without an FSBL protection handoff.
targets -set -filter {{name =~ "PSU"}}
source $psu_init_tcl
foreach p {{psu_init psu_ps_pl_isolation_removal psu_ps_pl_reset_config}} {{
    if {{[llength [info commands $p]] > 0}} {{
        $p
    }}
}}

# Start the existing DAQ MicroBlaze after FSBL has established the final PS,
# DDR, isolation, and reset state and the PL is configured.
targets -set -filter {{name =~ "MicroBlaze #*"}}
catch {{stop}}
rst -processor
dow $mb_firmware
con

targets -set -filter {{name =~ "Cortex-A53 #0"}}
catch {{stop}}
rst -processor -clear-registers
dow -data $dtb $dtb_addr
dow $uboot
dow -data $linux_image $linux_addr
dow -data $linux_dtb $linux_dtb_addr
dow -data $initramfs $initramfs_addr
dow $bl31
puts "Starting unified ZCU102 DAQ Ethernet and Pico USB runtime"
con
exit
"""


def read_uart(
    port: object, output: bytearray, lock: threading.Lock, done: threading.Event
) -> None:
    while not done.is_set():
        data = port.read(4096)
        if not data:
            continue
        with lock:
            output.extend(data)
        sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.flush()


def output_contains(output: bytearray, lock: threading.Lock, needle: bytes) -> bool:
    with lock:
        return needle in output


def wait_for(
    output: bytearray, lock: threading.Lock, needle: bytes, timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if output_contains(output, lock, needle):
            return True
        time.sleep(0.05)
    return False


def upload(
    args: argparse.Namespace,
    ssh: list[str],
    scp: list[str],
    paths: dict[str, Path],
    tcl_path: Path,
) -> None:
    run_checked([*ssh, f"mkdir -p {REMOTE_DIR}"], timeout=20)
    files = [str(path) for path in paths.values()] + [str(tcl_path)]
    run_checked([*scp, *files, f"{args.remote}:{REMOTE_DIR}/"], timeout=120)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load and verify unified ZCU102 DAQ Ethernet + Pico USB runtime"
    )
    parser.add_argument("--port", default="COM9", help="ZCU102 PS UART0")
    parser.add_argument(
        "--remote",
        default="jkincaid@capitolpeak.ece.ucdavis.edu",
        help="SSH host with the ZCU102 JTAG connection",
    )
    parser.add_argument(
        "--identity",
        default=str(Path.home() / ".ssh" / "capitolpeak_auto"),
        help="SSH private key",
    )
    parser.add_argument(
        "--board-ip", default="192.168.2.10", help="ZCU102 Linux DAQ address"
    )
    parser.add_argument(
        "--local-ip", default="192.168.2.1", help="direct-link host address"
    )
    return parser.parse_args()


def verify_ethernet(board_ip: str, local_ip: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.bind((local_ip, 0))
        except OSError as exc:
            raise RuntimeError(
                f"cannot bind Ethernet test to {local_ip}: {exc}. "
                "Configure the direct-link adapter before running."
            ) from exc
        while time.monotonic() < deadline:
            try:
                sock.sendto(b"PING", (board_ip, 5006))
                reply, peer = sock.recvfrom(128)
                if peer[0] == board_ip and reply == b"PONG\n":
                    return
                last_error = f"unexpected reply {reply!r} from {peer}"
            except OSError as exc:
                last_error = str(exc)
    raise RuntimeError(
        f"Linux USB passed, but DAQ Ethernet PING to {board_ip}:5006 failed: "
        f"{last_error}"
    )


# Exercise the production protocol used by unmodified PyDAQ and always
# terminate handshake mode before returning.
def verify_pico_bridge(
    board_ip: str, local_ip: str, timeout: float = 15.0
) -> None:
    parent = str(ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    from fpga_pico_serial import Serial as PicoSerial

    try:
        with PicoSerial(
            transport="ethernet",
            board_ip=board_ip,
            local_ip=local_ip,
            timeout=timeout,
            write_timeout=2.0,
        ) as pico:
            pico.reset_input_buffer()
            pico.write(b"HANDSHAKE\n")
            uid = pico.readline().decode(
                "ascii", errors="replace"
            ).strip()
            if uid != "UID:PICO-002":
                raise RuntimeError(
                    f"expected UID:PICO-002, received {uid!r}"
                )
            pico.write(b"ENDHS\n")
            response = pico.readline().decode(
                "ascii", errors="replace"
            ).strip()
            if response != "HSOK":
                raise RuntimeError(
                    f"handshake termination returned {response!r}"
                )
    except OSError as exc:
        raise RuntimeError(
            f"Pico production CDC bridge on {board_ip}:5007 failed: {exc}"
        ) from exc

def main() -> int:
    args = parse_args()
    if serial is None:
        raise RuntimeError("pyserial is required: python -m pip install pyserial")
    paths = require_artifacts()
    initramfs_size = paths["initramfs"].stat().st_size
    ssh, scp = command_bases(args)
    xsdb = discover_xsdb(ssh)

    with tempfile.TemporaryDirectory(prefix="daq_pico_usb_") as temp_dir:
        tcl_path = Path(temp_dir) / "load_usb_host.tcl"
        with tcl_path.open("w", encoding="utf-8", newline="\n") as tcl_file:
            tcl_file.write(make_tcl())
        upload(args, ssh, scp, paths, tcl_path)

        output = bytearray()
        lock = threading.Lock()
        done = threading.Event()
        with serial.Serial(
            args.port, 115200, timeout=0.1, write_timeout=1.0
        ) as port:
            port.reset_input_buffer()
            reader = threading.Thread(
                target=read_uart,
                args=(port, output, lock, done),
                daemon=True,
            )
            reader.start()
            try:
                load = run_checked(
                    [
                        *ssh,
                        f"{xsdb} {REMOTE_DIR}/{tcl_path.name}",
                    ],
                    timeout=240,
                )
                sys.stdout.write(
                    "\n=== XSDB ===\n"
                    + load.stdout.decode("utf-8", errors="replace")
                )
                sys.stdout.flush()

                # Any character stops U-Boot autoboot. Keep sending an empty
                # line until the prompt appears so slow/fast tool versions work.
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    port.write(b"\r")
                    if wait_for(output, lock, b"ZynqMP>", 0.25):
                        break
                if not output_contains(output, lock, b"ZynqMP>"):
                    port.write(b"\x03\r")
                    if not wait_for(output, lock, b"ZynqMP>", 8):
                        raise RuntimeError("U-Boot prompt did not appear")

                boot_command = (
                    "setenv bootargs 'console=ttyPS0,115200 earlycon "
                    "root=/dev/ram0 rw rdinit=/init mem=240M'; "
                    f"booti 08000000 0C000000:{initramfs_size:x} 04000000\r"
                )
                port.write(boot_command.encode("ascii"))
                if not wait_for(output, lock, SUCCESS, 90):
                    with lock:
                        transcript = bytes(output)
                    if b"PICO-HOST: ERROR" in transcript:
                        raise RuntimeError(
                            "The ZCU102 Linux USB host reported an error; "
                            "inspect the UART transcript above."
                        )
                    raise RuntimeError(
                        "Pico USB CDC startup did not complete; inspect the UART "
                        "transcript above."
                    )
                verify_ethernet(args.board_ip, args.local_ip)
                verify_pico_bridge(args.board_ip, args.local_ip)
            finally:
                done.set()
                reader.join(timeout=1)

    print(
        "\nPASS: MicroBlaze is running, Linux DAQ Ethernet answered PING, "
        "and the preserved PICO-002 firmware completed its CDC handshake."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
