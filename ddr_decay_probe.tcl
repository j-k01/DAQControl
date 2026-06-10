# Track the A53->DDR path over time with the core HALTED and nothing running.
#
# Run recover_dap.tcl + program_and_load.tcl immediately before this, so the
# test starts from a clean rst -system + fresh psu_init.
#
# Each iteration: revive the core (rst -processor; a previously wedged DDR
# load leaves the core EDITR-dead, and rst -processor restores debug
# execution -- proven by env_probe.tcl), read clock/DDRC state via APB
# (safe: APB reads work even when the DDR path is dead), then attempt ONE
# DDR word read. If the path is dead the load hangs and wedges the core;
# the next iteration revives it.
#
# Interpretation:
#   DDR read ok at t=0, dies later  -> time decay with nothing running
#                                      (clock/PLL/DDRC state at that moment
#                                      is captured right before the kill)
#   DDR read dead already at t=0    -> fresh psu_init does not bring up the
#                                      A53 path at all
#   alive through the whole run     -> decay needs more time or a trigger
#                                      (running code / MB activity)

connect
after 1000
catch {targets -set -nocase -filter {name =~ "DAP*"}}
catch {rst -dap}
after 1000

proc rd {label addr {n 1}} {
    if {[catch {mrd $addr $n} v]} {
        puts "  $label: FAILED: [lindex [split $v "\n"] 0]"
        return ""
    }
    puts "  $label: [string trim $v]"
    return $v
}

proc revive {} {
    targets -set -filter {name =~ "Cortex-A53 #0"}
    catch {stop}
    if {[catch {rst -processor -clear-registers} e]} {
        puts "  rst-proc: $e"
    }
    after 300
}

proc iter {t} {
    revive
    puts "--- t=${t}s ---"
    rd PLL_STATUS_FPD 0xFD1A0044
    rd DDRC_MSTR   0xFD070000
    rd DDRC_STAT   0xFD070004
    rd DDRC_PWRCTL 0xFD070030
    rd DDRC_DBGCAM 0xFD070308
    rd DDRC_PCTRL0 0xFD070490
    rd DDRC_PCTRL1 0xFD070540
    rd DDRC_PCTRL2 0xFD0705F0
    rd DDRC_PCTRL3 0xFD0706A0
    rd DDRC_PCTRL4 0xFD070750
    rd DDRC_PCTRL5 0xFD070800
    set r [rd DDR_WORD_0x0F000000 0x0F000000]
    if {$r eq ""} {
        puts "  >>> A53->DDR DEAD at t=${t}s"
        return 0
    }
    puts "  >>> A53->DDR alive at t=${t}s"
    return 1
}

puts "=== ONE-TIME STATE (XMPU, LPD PLLs) ==="
revive
foreach {i base} {0 0xFD000000 1 0xFD010000 2 0xFD020000 3 0xFD030000 4 0xFD040000 5 0xFD050000} {
    rd XMPU${i}_CTRL $base
}
rd PLL_STATUS_LPD 0xFF5E0040

set schedule {0 15 30 60 120 240 420}
set prev 0
set dead_at -1
foreach t $schedule {
    set wait [expr {($t - $prev) * 1000}]
    if {$wait > 0} { after $wait }
    set prev $t
    if {![iter $t] && $dead_at < 0} {
        set dead_at $t
    }
}

if {$dead_at >= 0} {
    puts "=== RESULT: A53->DDR DEAD by t=${dead_at}s (state captured above) ==="
} else {
    puts "=== RESULT: A53->DDR still ALIVE after [lindex $schedule end]s halted ==="
}
