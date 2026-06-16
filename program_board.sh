#!/usr/bin/env bash
# Program the ZCU102 from a plain command line (Linux shell or git-bash/Windows).
#
#   ./program_board.sh                  # FPGA + MicroBlaze + A53 PS-eth (--init-ps)
#   ./program_board.sh --no-eth         # FPGA + MicroBlaze only (UART features)
#   ./program_board.sh --no-init        # load A53 app WITHOUT psu_init
#   ./program_board.sh --vivado 2024.1  # pin a Vivado version (default: newest)
#
# Two Vivado versions installed? It defaults to the newest and pins vivado +
# xsdb/xsct to the SAME version; use --vivado <ver> (or XVER=<ver>) to choose.
# Tool discovery (first hit wins): explicit env (VIVADO=/XSCT=), a
# `with_xilinx_2024_1` wrapper (capitolpeak), the version-pinned C:\Xilinx dirs,
# PATH, then $XILINX_VIVADO / $XILINX_VITIS. Override e.g.:
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
XVER="${XVER:-}"          # pin a Vivado/Vitis version, e.g. 2024.1; empty = newest
while [ $# -gt 0 ]; do
    case "$1" in
        --no-eth)   DO_ETH=0 ;;
        --no-init)  INIT_PS="" ;;
        --vivado)   shift; XVER="${1:-}" ;;
        --vivado=*) XVER="${1#*=}" ;;
        -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
    shift
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

list_versions() {   # version numbers present under Xilinx (both install layouts)
    # New layout: C:\Xilinx\<ver>\Vivado ; old layout: C:\Xilinx\Vivado\<ver>.
    { ls -d /c/Xilinx/*/Vivado /c/Xilinx/*/Vitis \
            /c/Xilinx/Vivado/* /c/Xilinx/Vitis/* 2>/dev/null || true; } \
        | grep -oE '[0-9]{4}\.[0-9]+' | sort -Vru || true
}
pick_version() {    # the chosen version: --vivado/XVER if set, else newest
    if [ -n "$XVER" ]; then echo "$XVER"; else list_versions | head -n1; fi
}

resolve() {   # echo a runnable command for base tool $1 (vivado/xsct/xsdb), or ""
    base="$1"
    [ -n "$WRAP" ] && { echo "$base"; return; }          # wrapper puts it on PATH
    ver="$(pick_version)"
    # version-pinned install dirs first (both layouts), so vivado + xsdb/xsct
    # come from the SAME version. xsdb ships under Vivado; xsct under Vitis.
    case "$base" in
        vivado) vdirs="/c/Xilinx/$ver/Vivado/bin /c/Xilinx/Vivado/$ver/bin" ;;
        xsct)   vdirs="/c/Xilinx/$ver/Vitis/bin /c/Xilinx/Vitis/$ver/bin /c/Xilinx/$ver/Vivado/bin /c/Xilinx/Vivado/$ver/bin" ;;
        xsdb)   vdirs="/c/Xilinx/$ver/Vivado/bin /c/Xilinx/Vivado/$ver/bin /c/Xilinx/$ver/Vitis/bin /c/Xilinx/Vitis/$ver/bin" ;;
        *)      vdirs="" ;;
    esac
    if [ -n "$ver" ]; then
        for d in $vdirs; do
            for ext in .bat .cmd; do
                [ -e "$d/$base$ext" ] && { echo "$d/$base$ext"; return; }
            done
        done
    fi
    # then PATH. On Windows the extensionless launcher in bin/ is the *Linux*
    # script (execs unwrapped/lnx64.o/rlwrap and dies), so try .bat/.cmd FIRST.
    case "$(uname -s 2>/dev/null)" in
        MINGW*|MSYS*|CYGWIN*) try="$base.bat $base.cmd $base" ;;
        *)                    try="$base $base.bat $base.cmd" ;;
    esac
    for name in $try; do
        if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return; fi
    done
    # then $XILINX_* env roots
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
}

run() {   # run resolved tool $1 with the wrapper if needed; rest = args
    cmd="$1"; shift
    if [ -n "$WRAP" ]; then "$WRAP" "$cmd" "$@"; else "$cmd" "$@"; fi
}

if [ -z "$WRAP" ]; then
    avail="$(list_versions | tr '\n' ' ')"
    [ -n "$avail" ] && echo "Xilinx versions found: $avail"
    [ -n "$XVER" ] && echo "using version: $XVER"   || true
fi

VIVADO="${VIVADO:-$(resolve vivado)}"
# prefer xsdb (headless debugger) over xsct for the ELF load
XSCT="${XSCT:-$(resolve xsdb)}"
[ -z "$XSCT" ] && XSCT="$(resolve xsct)"

if [ -z "$VIVADO" ]; then
    echo "ERROR: vivado not found${XVER:+ for version $XVER}." >&2
    echo "  Installed: $(list_versions | tr '\n' ' ')" >&2
    echo "  Pick one with --vivado <ver>, or set VIVADO=/full/path/to/vivado.bat" >&2
    exit 1
fi

echo "==> FPGA bitstream + MicroBlaze firmware   ($VIVADO)"
run "$VIVADO" -mode batch -source quiet.tcl -tclargs program_and_load.tcl

if [ "$DO_ETH" -eq 1 ]; then
    if [ -z "$XSCT" ]; then
        echo "ERROR: xsdb/xsct not found. Set XSCT=/full/path, or pass --no-eth." >&2
        exit 1
    fi
    # run xsdb/xsct directly (NOT via quiet.tcl): their `source` rejects
    # -notrace, and they don't echo commands the way Vivado does anyway.
    echo "==> A53 PS-Ethernet app ${INIT_PS:-(no psu_init)}   ($XSCT)"
    run "$XSCT" load_ps_eth_stream.tcl $INIT_PS
fi

echo "==> Done. Launch the GUI: python scripts/dac_scope_qt.py --port COM10"
