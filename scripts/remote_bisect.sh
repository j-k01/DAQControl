#!/bin/bash
# Runs ON capitolpeak. Bisects the ps_eth_stream system hang by barrier stage:
# builds the app with each DAQ_HALT_STAGE, loads it on the A53 through
# probe_stage.tcl, and checks whether the core is still haltable. On the
# first hanging stage it recovers the JTAG view, reprograms the FPGA + MB,
# and reports. Stage order is execution order in main.c:
#   1=mailbox only, 7=after first xil_printf, 2=after GIC init,
#   3=after lwip_init, 4=after xemac_add, 5=netif up + IRQs on, 6=UDP ready
set -u
cd /home/jkincaid/DAQControl
X=/home/jkincaid/bin/with_xilinx_2024_1

recover_and_reprogram() {
    echo "--- recovering JTAG + reprogramming FPGA/MB ---"
    $X xsct recover_dap.tcl >/tmp/bisect_recover.log 2>&1
    $X vivado -mode batch -source program_and_load.tcl >/tmp/bisect_reprogram.log 2>&1
    tail -2 /tmp/bisect_reprogram.log
}

for stage in 1 7 2 3 4 5 6; do
    echo "=== STAGE $stage ==="
    if ! $X xsct build_app_only.tcl $stage >/tmp/bisect_build.log 2>&1; then
        echo "BUILD FAILED:"
        tail -20 /tmp/bisect_build.log
        exit 1
    fi

    tries=0
    while true; do
        $X xsct probe_stage.tcl 6 >/tmp/bisect_probe.log 2>&1
        grep -E "Halt OK|HALT FAILED|PC:|0x0F000000" -A1 /tmp/bisect_probe.log | head -10

        if grep -q "HALT FAILED" /tmp/bisect_probe.log; then
            echo "=== BISECT RESULT: system hangs at stage $stage ==="
            recover_and_reprogram
            exit 0
        elif grep -q "Halt OK" /tmp/bisect_probe.log; then
            break
        else
            tries=$((tries + 1))
            echo "--- probe infra error (attempt $tries) ---"
            tail -8 /tmp/bisect_probe.log
            if [ $tries -ge 2 ]; then
                echo "=== BISECT ABORT: probe infrastructure failing at stage $stage ==="
                exit 1
            fi
            recover_and_reprogram
        fi
    done
done

echo "=== BISECT RESULT: all stages healthy ==="
