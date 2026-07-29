from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
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
SUCCESS = b"PICO-HOST: PASS - USB update, Pico execution, and SPI loopback verified"

ARTIFACTS = {
    "bitstream": "top.bit",
    "fsbl": "zynqmp_fsbl.elf",
    "pmufw": "zynqmp_pmufw.elf",
    "bl31": "bl31.elf",
    "uboot": "u-boot",
    "dtb": "u-boot.dtb",
    "pico": "pico2_usb_spi_test.bin",
    "pico_uf2": "pico2_usb_spi_test.uf2",
    "linux_image": "Image",
    "linux_dtb": "system.dtb",
    "initramfs": "pico-initramfs.cpio.gz",
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
        if name != "bitstream"
    }
    paths["bitstream"] = ROOT.parent / "prebuilt" / "top.bit"
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
set pico_image [file join $work_dir {ARTIFACTS["pico"]}]
set pico_uf2 [file join $work_dir {ARTIFACTS["pico_uf2"]}]
set linux_image [file join $work_dir {ARTIFACTS["linux_image"]}]
set linux_dtb [file join $work_dir {ARTIFACTS["linux_dtb"]}]
set initramfs [file join $work_dir {ARTIFACTS["initramfs"]}]
set dtb_addr 0x00100000
set pico_image_addr 0x35000000
set pico_uf2_addr 0x36000000
set linux_addr 0x08000000
set linux_dtb_addr 0x04000000
set initramfs_addr 0x20000000

connect
targets -set -filter {{name =~ "PL"}}
fpga -file $bitstream

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

targets -set -filter {{name =~ "Cortex-A53 #0"}}
catch {{stop}}
rst -processor
dow $fsbl
con
after 20000
stop

dow -data $dtb $dtb_addr
dow $uboot
dow -data $pico_image $pico_image_addr
dow -data $pico_uf2 $pico_uf2_addr
dow -data $linux_image $linux_addr
dow -data $linux_dtb $linux_dtb_addr
dow -data $initramfs $initramfs_addr
dow $bl31
puts "Starting ZCU102 USB host runtime"
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
        description="Load and verify the ZCU102 J96 to Pico 2 USB/SPI test"
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
    return parser.parse_args()


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
                    "root=/dev/ram0 rw rdinit=/init'; "
                    f"booti 08000000 20000000:{initramfs_size:x} 04000000\r"
                )
                port.write(boot_command.encode("ascii"))
                if not wait_for(output, lock, SUCCESS, 90):
                    with lock:
                        transcript = bytes(output)
                    if b"PICO-HOST: FAIL" in transcript:
                        raise RuntimeError(
                            "Pico executed, but the GP6-GP13 SPI wiring test failed."
                        )
                    if b"PICO-HOST: ERROR" in transcript:
                        raise RuntimeError(
                            "The ZCU102 Linux USB updater reported an error; "
                            "inspect the UART transcript above."
                        )
                    raise RuntimeError(
                        "Pico USB/SPI verification did not complete; inspect the "
                        "UART transcript above."
                    )
            finally:
                done.set()
                reader.join(timeout=1)

    print("\nPASS: Pico LED is blinking and bidirectional SPI/USB is verified.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
