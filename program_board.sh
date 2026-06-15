#!/usr/bin/env bash
# Program the ZCU102 from a plain command line (git-bash on Windows).
#
#   ./program_board.sh            # FPGA + MicroBlaze + A53 PS-eth app (--init-ps)
#   ./program_board.sh --no-eth   # FPGA + MicroBlaze only (UART features)
#   ./program_board.sh --no-init  # load A53 app WITHOUT psu_init (already inited)
#
# Override tool locations if Xilinx isn't under C:\Xilinx:
#   VIVADO_BAT=/c/Tools/Vivado/2024.1/bin/vivado.bat \
#   XSCT_BAT=/c/Tools/Vitis/2024.1/bin/xsct.bat ./program_board.sh
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

# Pick the newest install if several versions exist (override via env vars).
find_bat() {  # $1 = product root, $2 = exe name -> newest match or empty
    ls -d "$1"/*/bin/"$2" 2>/dev/null | sort -Vr | head -n1
}
VIVADO_BAT="${VIVADO_BAT:-$(find_bat /c/Xilinx/Vivado vivado.bat)}"
XSCT_BAT="${XSCT_BAT:-$(find_bat /c/Xilinx/Vitis xsct.bat)}"

if [ -z "$VIVADO_BAT" ] || [ ! -e "$VIVADO_BAT" ]; then
    echo "ERROR: vivado.bat not found. Set VIVADO_BAT to its full path." >&2
    exit 1
fi

echo "==> FPGA bitstream + MicroBlaze firmware"
echo "    $VIVADO_BAT"
"$VIVADO_BAT" -mode batch -source program_and_load.tcl

if [ "$DO_ETH" -eq 1 ]; then
    if [ -z "$XSCT_BAT" ] || [ ! -e "$XSCT_BAT" ]; then
        echo "ERROR: xsct.bat not found. Set XSCT_BAT, or pass --no-eth." >&2
        exit 1
    fi
    echo "==> A53 PS-Ethernet app ${INIT_PS:-(no psu_init)}"
    echo "    $XSCT_BAT"
    "$XSCT_BAT" load_ps_eth_stream.tcl $INIT_PS
fi

echo "==> Done. Verify with: python scripts/uart_cmds.py --port COM10 STAT"
