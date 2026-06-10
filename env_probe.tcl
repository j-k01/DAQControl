# Environmental probe for the stage-1 wedge.
#
# The bisect proved the system wedges even when the app is just
# "write mailbox word, spin forever" -- no UART, GIC, lwIP, or GEM access.
# So the cause is environmental. This script gathers the evidence in one
# pass, through a halted A53 core only (PSU/DAP mrd is broken here, see
# recover_dap.tcl):
#
#   BOOT_MODE_USER/POR  -- is the board set to boot from QSPI/SD? If so a
#                          flashed FSBL re-launches after every rst -system
#                          and fights the JTAG session (and may enable a
#                          watchdog that resets the PS seconds later).
#   RESET_REASON        -- PMU/PS-only/SRST bits show if something has been
#                          resetting the PS behind our back.
#   LPD/FPD SWDT ZMR    -- watchdog enable bits (bit0 = WDEN).
#   PMU_GLOBAL_CNTRL    -- bit4 FW_IS_PRESENT: a PMU firmware can only have
#                          come from a boot image, never from our JTAG flow.
#   mailbox @0x0F000000 -- post-mortem of the last stage-1 run.
#
# Fix-ups applied automatically:
#   * enabled watchdog  -> disable (ZMR write key 0xABC in [23:12], WDEN=0)
#   * boot mode != JTAG -> BOOT_MODE_USER = 0x0100 (USE_ALT -> JTAG, per
#                          Xilinx AR#68657) + rst -system + rst -dap so the
#                          BootROM parks the cores instead of launching the
#                          flashed image.
# Afterwards run program_and_load.tcl (psu_init/DDR/PL/MB) and
# probe_stage.tcl to re-test stage-1 survival.

connect
after 1000

proc word0 {mrdout} {
    set line [lindex [split $mrdout "\n"] 0]
    return [expr 0x[string trim [lindex [split $line ":"] 1]]]
}

proc rd {label addr {n 1}} {
    if {[catch {mrd $addr $n} v]} {
        puts "RD $label: FAILED: $v"
        return ""
    }
    puts "RD $label: $v"
    return $v
}

# --- make sure we have a usable A53 target -------------------------------
set tl ""
catch {set tl [targets]}
if {[string first "Cortex-A53 #0" $tl] < 0} {
    puts "ENV: no A53 target visible -- running DAP recovery first"
    catch {targets -set -nocase -filter {name =~ "DAP*"}}
    catch {rst -system} e
    puts "ENV: rst -system: $e"
    after 3000
    catch {rst -dap} e
    puts "ENV: rst -dap: $e"
    after 2000
}

# Clear sticky DAP errors (same precaution as probe_stage.tcl).
catch {targets -set -nocase -filter {name =~ "DAP*"}}
catch {rst -dap}
after 1000

targets -set -filter {name =~ "Cortex-A53 #0"}
catch {stop}
if {[catch {rst -processor -clear-registers} e]} {
    puts "ENV: rst -processor: $e"
}
after 500

# --- evidence -------------------------------------------------------------
puts "=== ENV EVIDENCE (read via halted A53) ==="
set bm  [rd BOOT_MODE_USER 0xFF5E0200]
rd BOOT_MODE_POR 0xFF5E0204
rd RESET_REASON  0xFF5E0220
set wl  [rd LPD_SWDT_ZMR 0xFF150000]
set wf  [rd FPD_SWDT_ZMR 0xFD4D0000]
set pmu [rd PMU_GLOBAL_CNTRL 0xFFD80000]
rd MAILBOX_POSTMORTEM 0x0F000000 8

if {$pmu ne ""} {
    if {[expr {[word0 $pmu] & 0x10}]} {
        puts "ENV: PMU firmware IS PRESENT -> a boot image has run (JTAG flow never loads PMUFW)"
    } else {
        puts "ENV: no PMU firmware loaded"
    }
}

# --- fixups ---------------------------------------------------------------
foreach {nm v base} [list LPD_SWDT $wl 0xFF150000 FPD_SWDT $wf 0xFD4D0000] {
    if {$v ne "" && [expr {[word0 $v] & 0x1}]} {
        puts "ENV: $nm is ENABLED -- disabling (ZMR <= 0x00ABC000)"
        catch {mwr $base 0x00ABC000} e
        rd ${nm}_ZMR_after $base
    }
}

set need_reset 0
if {$bm ne ""} {
    set mode [expr {[word0 $bm] & 0xF}]
    set names [dict create 0 JTAG 1 QSPI24 2 QSPI32 3 SD0 4 NAND 5 SD1 6 eMMC18 7 USB0]
    if {[dict exists $names $mode]} {
        set mn [dict get $names $mode]
    } else {
        set mn "code$mode"
    }
    puts "ENV: boot mode pins = $mode ($mn)"
    if {$mode != 0} {
        puts "ENV: non-JTAG boot -- writing JTAG override (BOOT_MODE_USER <= 0x0100)"
        if {[catch {mwr 0xFF5E0200 0x0100} e]} {
            puts "ENV: override write FAILED: $e"
        } else {
            rd BOOT_MODE_USER_after 0xFF5E0200
            set need_reset 1
        }
    }
}

if {$need_reset} {
    puts "ENV: rst -system so the BootROM honors the override (cores park in JTAG mode)"
    catch {targets -set -nocase -filter {name =~ "DAP*"}}
    catch {rst -system} e
    puts "ENV: rst -system: $e"
    after 3000
    catch {rst -dap} e
    puts "ENV: rst -dap: $e"
    after 2000
    catch {targets -set -filter {name =~ "Cortex-A53 #0"}}
    catch {stop}
    catch {rst -processor -clear-registers}
    after 500
    rd BOOT_MODE_USER_postreset 0xFF5E0200
    rd RESET_REASON_postreset  0xFF5E0220
}

puts "=== ENV PROBE DONE (next: program_and_load.tcl, then probe_stage.tcl) ==="
