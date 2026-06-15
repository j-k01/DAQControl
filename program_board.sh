#!/usr/bin/env bash
# Program the ZCU102 from a plain command line (Linux shell or git-bash/Windows).
#
#   ./program_board.sh            # FPGA + MicroBlaze + A53 PS-eth app (--init-ps)
#   ./program_board.sh --no-eth   # FPGA + MicroBlaze only (UART features)
#   ./program_board.sh --no-init  # load A53 app WITHOUT psu_init (already inited)
#
# Tool discovery (first hit wins): explicit env (VIVADO=/XSCT=), a
# `with_xilinx_2024_1` wrapper on PATH (capitolpeak), `vivado`/`xsdb` on PATH,
# $XILINX_VIVADO / $XILINX_VITIS, then C:\Xilinx (Windows). Override e.g.:
#   VIVADO=/c/Xilinx/Vivado/2024.1/bin/vivado.bat ./program_board.sh
#
# Headless note: Vitis' launcher probes the X server with `xlsclients`; on a
# box without it that prints "xlsclients not available on the system" and can
# abort the load. We put a tiny no-op `xlsclients` on PATH for the duration --
# the truthful answer on a headless host is "no X clients" -- so the launcher
# proceeds normally. (We also prefer `xsdb`, the headless debugger shell.)
set -euo pipefail
cd "$(dirname "$0")"

DO_ETH=1
INIT_PS="--init-ps"
for arg in "$@"; do
    case "$arg" in
        --no-eth)  DO_ETH=0 ;;
        --no-init) INIT_PS="" ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# --- satisfy Vitis' X-server probe (Linux + Windows) -------------------------
# The Vitis/XSCT launcher runs `xlsclients` to detect X sessions; on a headless
# Linux box (or on Windows, where it doesn't exist) that aborts immediately with
# "xlsclients not available on the system". tools/ holds no-op shims
# (xlsclients.bat/.cmd for the Windows launcher, a POSIX script for Linux) that
# truthfully report "no X clients" (exit 0) so the launcher proceeds. Prepend
# tools/ for this run.
PATH="$(pwd)/tools:$PATH"; export PATH

# --- locate the tools --------------------------------------------------------
WRAP=""
if command -v with_xilinx_2024_1 >/dev/null 2>&1; then
    WRAP="with_xilinx_2024_1"      # capitolpeak env wrapper: prefixes the tool
fi

resolve() {   # echo a runnable command for base tool $1 (vivado/xsct/xsdb), or ""
    base="$1"
    [ -n "$WRAP" ] && { echo "$base"; return; }          # wrapper puts it on PATH
    # On Windows (git-bash/MSYS) the extensionless launcher in bin/ is the *Linux*
    # script (it execs unwrapped/lnx64.o/rlwrap and dies); the .bat is the real
    # Windows entry point, so try .bat/.cmd FIRST there. On POSIX, bare name first.
    case "$(uname -s 2>/dev/null)" in
        MINGW*|MSYS*|CYGWIN*) try="$base.bat $base.cmd $base" ;;
        *)                    try="$base $base.bat $base.cmd" ;;
    esac
    for name in $try; do
        if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return; fi
    done
    case "$base" in
        vivado)     root="${XILINX_VIVADO:-}" ;;
        xsct|xsdb)  root="${XILINX_VITIS:-}" ;;
        *)          root="" ;;
    esac
    if [ -n "$root" ]; then
        for ext in ".bat" ""; do
            [ -e "$root/bin/$base$ext" ] && { echo "$root/bin/$base$ext"; return; }
        done
    fi
    # last resort: scan common Windows installs -- xsdb ships under Vivado too
    for d in /c/Xilinx/Vivado /c/Xilinx/Vitis; do
        cand=$(ls -d "$d"/*/bin/"$base".bat 2>/dev/null | sort -Vr | head -n1)
        [ -n "$cand" ] && { echo "$cand"; return; }
    done
}

run() {   # run resolved tool $1 with the wrapper if needed; rest = args
    cmd="$1"; shift
    if [ -n "$WRAP" ]; then "$WRAP" "$cmd" "$@"; else "$cmd" "$@"; fi
}

VIVADO="${VIVADO:-$(resolve vivado)}"
# prefer xsdb (headless debugger) over xsct for the ELF load
XSCT="${XSCT:-$(resolve xsdb)}"
[ -z "$XSCT" ] && XSCT="$(resolve xsct)"

if [ -z "$VIVADO" ]; then
    echo "ERROR: vivado not found. Set VIVADO=/full/path/to/vivado[.bat]." >&2
    exit 1
fi

echo "==> FPGA bitstream + MicroBlaze firmware   ($VIVADO)"
run "$VIVADO" -mode batch -source program_and_load.tcl

if [ "$DO_ETH" -eq 1 ]; then
    if [ -z "$XSCT" ]; then
        echo "ERROR: xsdb/xsct not found. Set XSCT=/full/path, or pass --no-eth." >&2
        exit 1
    fi
    echo "==> A53 PS-Ethernet app ${INIT_PS:-(no psu_init)}   ($XSCT)"
    run "$XSCT" load_ps_eth_stream.tcl $INIT_PS
fi

echo "==> Done. Verify with: python scripts/uart_cmds.py --port COM10 STAT"
