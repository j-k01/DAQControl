from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

try:
    import serial
    import serial.tools.list_ports as serial_list_ports
except ImportError:
    serial = None
    serial_list_ports = None


ROOT = Path(__file__).resolve().parent
PREBUILT = ROOT / "prebuilt"
REMOTE_DIR = "/tmp/daq_pico_usb"
SUCCESS = b"PICO-HOST: READY - existing Pico CDC firmware preserved"
VALIDATED_XILINX_VERSION = (2024, 1)
GTH_REQUIRED = {
    "hmc_done", "qpll_locked", "tx_ready", "rx_ready",
    "litejesd_active", "litejesd_ready",
}

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
        detail = result.stdout.decode("utf-8", errors="replace")
        rendered = subprocess.list2cmdline([str(part) for part in command])
        sys.stderr.write(
            f"\n=== FAILED COMMAND (exit {result.returncode}) ===\n"
            f"{rendered}\n{detail}\n=== END FAILED COMMAND ===\n"
        )
        sys.stderr.flush()
        raise RuntimeError(
            f"command failed with exit code {result.returncode}; complete tool "
            "output is printed above"
        )
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


def make_tcl(work_dir: str = REMOTE_DIR) -> str:
    return f"""\
set work_dir "{work_dir}"
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
catch {{stop}}

# Configure the PL after the system reset.  A system reset after `fpga` erases
# the configuration and was invisible in the original USB-only proof.
if {{[catch {{fpga -file $bitstream}} fpga_err]}} {{
    set pl_ok 0
    foreach filt {{{{name =~ "*xczu9*"}} {{name =~ "*PL*"}}}} {{
        if {{![catch {{targets -set -nocase -filter $filt}}]}} {{
            set pl_ok 1
            break
        }}
    }}
    if {{!$pl_ok}} {{
        error "FPGA configuration failed and no PL target was found: $fpga_err"
    }}
    fpga -file $bitstream
}}
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
set mb_ok 0
foreach filt {{{{name =~ "MicroBlaze #*"}} {{name =~ "*MicroBlaze*"}} {{name =~ "*microblaze*"}}}} {{
    if {{![catch {{targets -set -nocase -filter $filt}}]}} {{
        set mb_ok 1
        break
    }}
}}
if {{!$mb_ok}} {{ error "No MicroBlaze target after FPGA configuration" }}
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

def rank_serial_ports(ports: list[object], interface_number: int) -> list[str]:
    """Prefer the ZCU102 CP2108 interface convention, then other ports."""
    wanted = f"interface {interface_number}"

    def key(info: object) -> tuple[int, str]:
        text = " ".join(
            str(getattr(info, field, "") or "")
            for field in ("description", "interface", "hwid")
        ).lower()
        preferred = "cp2108" in text and wanted in text
        return (0 if preferred else 1, str(getattr(info, "device", "")))

    return [str(info.device) for info in sorted(ports, key=key)]


def resolve_ps_port(requested: str) -> str:
    if requested.lower() != "auto":
        return requested
    if serial_list_ports is None:
        raise RuntimeError("pyserial port discovery is unavailable")
    ports = list(serial_list_ports.comports())
    ranked = rank_serial_ports(ports, 0)
    preferred = []
    for info in ports:
        text = " ".join(
            str(getattr(info, field, "") or "")
            for field in ("description", "interface", "hwid")
        ).lower()
        if "cp2108" in text and "interface 0" in text:
            preferred.append(str(info.device))
    if len(preferred) != 1:
        raise RuntimeError(
            "could not select the ZCU102 PS UART safely. Pass --port COMx; "
            f"detected ports: {', '.join(ranked) or '(none)'}"
        )
    return preferred[0]


def read_line_prefix(port: object, prefixes: tuple[str, ...], timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = port.readline().decode("ascii", errors="replace").strip()
        if line.startswith(prefixes):
            return line
    return ""


def daq_command(
    port: object, command: str, prefixes: tuple[str, ...], timeout: float = 4.0
) -> str:
    port.reset_input_buffer()
    port.write((command + "\n").encode("ascii"))
    port.flush()
    return read_line_prefix(port, prefixes, timeout)


def probe_daq_port(name: str, timeout: float = 1.5) -> bool:
    try:
        with serial.Serial(name, 115200, timeout=0.1, write_timeout=1.0) as port:
            reply = daq_command(port, "RDRW 17", ("REG17", "ERR"), timeout)
            return reply.startswith("REG17")
    except (OSError, serial.SerialException):
        return False


def resolve_daq_port(requested: str, ps_port: str) -> str:
    if requested.lower() != "auto":
        if requested.upper() == ps_port.upper():
            raise RuntimeError("PS UART and MicroBlaze DAQ UART cannot be the same port")
        if not probe_daq_port(requested):
            raise RuntimeError(
                f"{requested} did not answer the MicroBlaze RDRW 17 probe"
            )
        return requested
    if serial_list_ports is None:
        raise RuntimeError("pyserial port discovery is unavailable")
    ports = [
        info for info in serial_list_ports.comports()
        if str(info.device).upper() != ps_port.upper()
    ]
    for name in rank_serial_ports(ports, 2):
        if probe_daq_port(name):
            return name
    raise RuntimeError(
        "could not find the MicroBlaze DAQ UART automatically. Pass "
        "--daq-port COMx (normally CP2108 Interface 2)."
    )


def read_gth_gate(port: object) -> set[str]:
    port.reset_input_buffer()
    port.write(b"STAT\n")
    port.flush()
    line = read_line_prefix(port, ("gth_gate:",), 3.0)
    port.reset_input_buffer()
    if not line:
        raise RuntimeError("MicroBlaze STAT returned no gth_gate line")
    return set(line.split()[1:])


def read_adc_frontend_ready(port: object) -> tuple[bool, bool, int]:
    """Return the two ADC receiver-ready bits from the RO4 frontend summary."""
    reply = daq_command(port, "ADCS", ("ADC frontend", "ERR"), 3.0)
    match = re.search(r"\bRO4=(0x[0-9A-Fa-f]+|\d+)", reply)
    if not match:
        raise RuntimeError(
            f"ADC frontend status was not readable: {reply or 'no reply'}"
        )
    value = int(match.group(1), 0)
    return bool(value & (1 << 13)), bool(value & (1 << 14)), value


def recover_adc_receivers(port: object) -> tuple[bool, bool, int]:
    """Reinitialize both ADS54J60s and the shared GTH/LiteJESD path."""
    rw3_reply = daq_command(port, "RDRW 3", ("REG3", "ERR"), 3.0)
    if not rw3_reply.startswith("REG3"):
        raise RuntimeError(
            f"cannot preserve RW3 for ADC restart: {rw3_reply or 'no reply'}"
        )
    rw3 = int(rw3_reply.split("=", 1)[1].strip(), 0)
    for value in (rw3 | 0x4, rw3 & ~0x4):
        reply = daq_command(
            port, f"WRTE 3 0x{value:08X}", ("OK", "ERR"), 3.0
        )
        if not reply.startswith("OK"):
            raise RuntimeError(
                f"ADS54J60 restart write failed: {reply or 'no reply'}"
            )
    time.sleep(1.0)
    reply = daq_command(port, "TXRS", ("OK", "ERR"), 8.0)
    if not reply.startswith("OK"):
        raise RuntimeError(f"JESD TXRS recovery failed: {reply or 'no reply'}")

    deadline = time.monotonic() + 10.0
    ready = (False, False, 0)
    while time.monotonic() < deadline:
        time.sleep(0.5)
        ready = read_adc_frontend_ready(port)
        if ready[0] and ready[1]:
            return ready
    return ready


def verify_daq_runtime(requested_port: str, ps_port: str) -> tuple[str, str]:
    """Require a live MB, healthy JESD links, and a completed burst engine."""
    daq_port = resolve_daq_port(requested_port, ps_port)
    with serial.Serial(
        daq_port, 115200, timeout=0.1, write_timeout=1.0
    ) as port:
        tokens = read_gth_gate(port)
        missing = GTH_REQUIRED - tokens
        if missing:
            reply = daq_command(port, "TXRS", ("OK", "ERR"), 8.0)
            if not reply.startswith("OK"):
                raise RuntimeError(f"JESD TXRS recovery failed: {reply or 'no reply'}")
            time.sleep(1.0)
            tokens = read_gth_gate(port)
            missing = GTH_REQUIRED - tokens
        if missing:
            recover_adc_receivers(port)
            tokens = read_gth_gate(port)
            missing = GTH_REQUIRED - tokens
        if missing:
            raise RuntimeError(
                "DAQ JESD links are not ready after recovery; missing "
                + ", ".join(sorted(missing))
            )
        adc0_ready, adc1_ready, adc_status = read_adc_frontend_ready(port)
        if not (adc0_ready and adc1_ready):
            print(
                "ADC receiver preflight is not ready "
                f"(RO4=0x{adc_status:08X}); reinitializing both ADCs..."
            )
            adc0_ready, adc1_ready, adc_status = recover_adc_receivers(port)
        if not (adc0_ready and adc1_ready):
            raise RuntimeError(
                "ADC receivers are not ready after reinitialization: "
                f"RO4=0x{adc_status:08X} adc0={int(adc0_ready)} "
                f"adc1={int(adc1_ready)}"
            )

        burst = daq_command(
            port, "BCAP 64k", ("OK BCAP", "ERR BCAP"), timeout=180.0
        )
        if burst.startswith("ERR BCAP timeout (engine)"):
            # The first capture after PS/PL initialization can be the one that
            # releases stale DMA/FIFO state. Do not reset otherwise healthy
            # converters here: that merely turns the retry into another first
            # capture. Receiver recovery is justified only by the real RO4
            # ready bits dropping.
            adc0_ready, adc1_ready, adc_status = read_adc_frontend_ready(port)
            if adc0_ready and adc1_ready:
                print(
                    "Initial ADC capture did not complete while both receivers "
                    "remained ready; retrying once without resetting them..."
                )
            else:
                print(
                    "ADC capture timed out and a receiver is not ready "
                    f"(RO4=0x{adc_status:08X}); reinitializing the ADC path..."
                )
                adc0_ready, adc1_ready, adc_status = recover_adc_receivers(port)
            if adc0_ready and adc1_ready:
                burst = daq_command(
                    port, "BCAP 64k", ("OK BCAP", "ERR BCAP"), timeout=180.0
                )
        if not burst.startswith("OK BCAP"):
            raise RuntimeError(
                f"DAQ burst-engine self-test failed: {burst or 'no UART reply'}"
            )
    return daq_port, burst


def interrupt_uboot(
    port: object,
    output: bytearray,
    lock: threading.Lock,
    done: threading.Event,
) -> None:
    """Stop U-Boot autoboot even when it starts before XSDB returns."""
    interrupted = False
    cancelled_bootp = False
    while not done.is_set():
        with lock:
            snapshot = bytes(output)
        try:
            if not interrupted and b"Hit any key to stop autoboot" in snapshot:
                port.write(b" ")
                port.flush()
                interrupted = True
            if (not cancelled_bootp and b"BOOTP broadcast" in snapshot and
                    b"ZynqMP>" not in snapshot):
                port.write(b"\x03")
                port.flush()
                cancelled_bootp = True
        except (OSError, serial.SerialException):
            return
        if b"ZynqMP>" in snapshot:
            return
        time.sleep(0.02)


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
    parser.add_argument(
        "--port", default="auto",
        help="ZCU102 PS UART0 (auto selects CP2108 Interface 0)",
    )
    parser.add_argument(
        "--daq-port", default="auto",
        help="MicroBlaze DAQ UART (auto probes CP2108 Interface 2)",
    )
    parser.add_argument(
        "--local-jtag",
        action="store_true",
        help="use XSDB and JTAG attached to this PC instead of SSH",
    )
    parser.add_argument(
        "--xsdb",
        help="local xsdb/xsct executable (used with --local-jtag)",
    )
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


def xilinx_version(path: Path) -> tuple[int, int]:
    match = re.search(r"(20\d{2})[./\\](\d+)", str(path))
    return ((int(match.group(1)), int(match.group(2)))
            if match else (0, 0))


def rank_xsdb_candidates(candidates: list[Path]) -> list[Path]:
    """Prefer the artifact-producing 2024.1 tools, then newer fallbacks."""
    unique = set(path.resolve() for path in candidates)

    def key(path: Path) -> tuple[int, int, int, int]:
        version = xilinx_version(path)
        return (
            int(version == VALIDATED_XILINX_VERSION),
            version[0], version[1],
            int(path.name.lower().startswith("xsdb")),
        )

    return sorted(unique, key=key, reverse=True)


def find_local_xsdb_candidates() -> list[Path]:
    candidates = []
    for name in ("xsdb", "xsdb.bat", "xsct", "xsct.bat"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for pattern in (
        "Xilinx/*/Vivado/bin/xsdb.bat",
        "Xilinx/Vivado/*/bin/xsdb.bat",
        "Xilinx/*/Vitis/bin/xsct.bat",
        "Xilinx/Vitis/*/bin/xsct.bat",
    ):
        candidates.extend(Path("C:/").glob(pattern))
    return rank_xsdb_candidates(candidates)


def discover_local_xsdb(explicit: str | None = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"local XSDB/XSCT does not exist: {path}")
        return path

    candidates = find_local_xsdb_candidates()
    if not candidates:
        raise RuntimeError(
            "could not find local xsdb/xsct. Install Xilinx tools or pass "
            "--xsdb C:\\Xilinx\\<version>\\Vivado\\bin\\xsdb.bat"
        )
    return candidates[0]

def local_xsdb_command(executable: Path, tcl_path: Path) -> list[str]:
    command = [str(executable), str(tcl_path)]
    if executable.suffix.lower() in {".bat", ".cmd"}:
        return ["cmd.exe", "/d", "/c", *command]
    return command


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

def standard_daq_command(local_xsdb: Path) -> list[str]:
    """Build the real target-PC program_board.ps1 invocation."""
    script = ROOT.parent / "program_board.ps1"
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script), "-NoPing", "-NoNicSetup",
    ]
    version = xilinx_version(local_xsdb)
    if version != (0, 0):
        command += ["-Vivado", f"{version[0]}.{version[1]}"]
    return command


def program_standard_daq(local_xsdb: Path, *, label: str) -> None:
    """Run the established full target-PC DAQ programming procedure."""
    print(f"\n{label}: program_board.ps1")
    result = run_checked(standard_daq_command(local_xsdb), timeout=600)
    output = result.stdout.decode("utf-8", errors="replace")
    if output:
        print(output)


def restore_standard_daq(local_xsdb: Path) -> None:
    """Return a failed unified load to the proven ordinary DAQ image."""
    program_standard_daq(local_xsdb, label="Restoring standard DAQ runtime")

def main() -> int:
    args = parse_args()
    if serial is None:
        raise RuntimeError("pyserial is required; run uv sync --frozen")
    paths = require_artifacts()
    initramfs_size = paths["initramfs"].stat().st_size
    ps_port = resolve_ps_port(args.port)
    print(f"PS/Linux UART: {ps_port}")
    if args.local_jtag:
        local_xsdb = discover_local_xsdb(args.xsdb)
        version = xilinx_version(local_xsdb)
        version_text = (
            f"{version[0]}.{version[1]}" if version != (0, 0) else "unknown"
        )
        print(
            f"JTAG tool: {local_xsdb} (version {version_text}; "
            "2024.1 preferred when installed)"
        )
        ssh = scp = None
    else:
        ssh, scp = command_bases(args)
        remote_xsdb = discover_xsdb(ssh)

    programming_started = False
    try:
        if args.local_jtag:
            # The unified image replaces only the A53 runtime. The external
            # converter clocks, JESD links, and MicroBlaze application must
            # first be initialized by the same sequence used by the normal
            # DAQ workflow. This is why an earlier unified load worked only
            # after program_board.ps1 had already run.
            program_standard_daq(
                local_xsdb,
                label=(
                    "Initializing complete DAQ hardware before unified "
                    "Linux/Pico boot"
                ),
            )

        with tempfile.TemporaryDirectory(prefix="daq_pico_usb_") as temp_dir:
            if args.local_jtag:
                stage_dir = Path(temp_dir) / "artifacts"
                stage_dir.mkdir()
                for path in paths.values():
                    shutil.copy2(path, stage_dir / path.name)
                tcl_path = stage_dir / "load_usb_host.tcl"
                with tcl_path.open("w", encoding="utf-8", newline="\n") as tcl_file:
                    tcl_file.write(make_tcl(stage_dir.as_posix()))
                load_command = local_xsdb_command(local_xsdb, tcl_path)
            else:
                tcl_path = Path(temp_dir) / "load_usb_host.tcl"
                with tcl_path.open("w", encoding="utf-8", newline="\n") as tcl_file:
                    tcl_file.write(make_tcl())
                upload(args, ssh, scp, paths, tcl_path)
                load_command = [
                    *ssh,
                    f"{remote_xsdb} {REMOTE_DIR}/{tcl_path.name}",
                ]

            output = bytearray()
            lock = threading.Lock()
            reader_done = threading.Event()
            interrupt_done = threading.Event()
            with serial.Serial(
                ps_port, 115200, timeout=0.1, write_timeout=1.0
            ) as port:
                port.reset_input_buffer()
                reader = threading.Thread(
                    target=read_uart,
                    args=(port, output, lock, reader_done),
                    daemon=True,
                )
                watcher = threading.Thread(
                    target=interrupt_uboot,
                    args=(port, output, lock, interrupt_done),
                    daemon=True,
                )
                reader.start()
                watcher.start()
                try:
                    programming_started = True
                    load = run_checked(load_command, timeout=240)
                    sys.stdout.write(
                        "\n=== XSDB ===\n"
                        + load.stdout.decode("utf-8", errors="replace")
                    )
                    sys.stdout.flush()

                    # The watcher has been active throughout XSDB programming,
                    # so it can stop U-Boot during its countdown instead of
                    # racing it only after XSDB exits. Slow starts and an
                    # already-entered BOOTP are both given time to recover.
                    if not wait_for(output, lock, b"ZynqMP>", 90):
                        port.write(b"\x03\r")
                        port.flush()
                        if not wait_for(output, lock, b"ZynqMP>", 30):
                            raise RuntimeError(
                                "U-Boot prompt did not appear on the selected "
                                f"PS UART {ps_port}"
                            )
                    interrupt_done.set()
                    watcher.join(timeout=1)

                    boot_command = (
                        "setenv bootargs 'console=ttyPS0,115200 earlycon "
                        "root=/dev/ram0 rw rdinit=/init mem=240M'; "
                        f"booti 08000000 0C000000:{initramfs_size:x} 04000000\r"
                    )
                    port.write(boot_command.encode("ascii"))
                    port.flush()
                    if not wait_for(output, lock, SUCCESS, 90):
                        with lock:
                            transcript = bytes(output)
                        if b"PICO-HOST: ERROR" in transcript:
                            raise RuntimeError(
                                "The ZCU102 Linux USB host could not enumerate "
                                "the Pico; inspect the UART transcript above."
                            )
                        raise RuntimeError(
                            "Unified Linux/Pico startup did not complete; "
                            "inspect the UART transcript above."
                        )
                finally:
                    interrupt_done.set()
                    reader_done.set()
                    watcher.join(timeout=1)
                    reader.join(timeout=1)

        # A PONG alone proves only the Linux service. Verify the independent
        # MicroBlaze/JESD/capture path that the GUI actually needs as well.
        daq_port, burst = verify_daq_runtime(args.daq_port, ps_port)
        print(f"DAQ UART: {daq_port}; JESD ready; {burst}")
        verify_ethernet(args.board_ip, args.local_ip)
        verify_pico_bridge(args.board_ip, args.local_ip)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        if args.local_jtag and programming_started:
            print(
                "\nUnified load failed; restoring the ordinary known-good DAQ "
                "runtime so the board is not left partially initialized...",
                file=sys.stderr,
            )
            try:
                restore_standard_daq(local_xsdb)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as restore_exc:
                raise RuntimeError(
                    f"unified load failed: {exc}; automatic DAQ restore also "
                    f"failed: {restore_exc}"
                ) from exc
            raise RuntimeError(
                f"unified load failed: {exc}. The ordinary DAQ runtime was "
                "restored successfully; Pico-over-FPGA is not active."
            ) from exc
        raise

    print(
        "\nPASS: MicroBlaze and both JESD links are ready, a real 64 KB ADC "
        "burst completed, Linux DAQ Ethernet answered PING, and PICO-002 "
        "completed its CDC handshake."
    )
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
