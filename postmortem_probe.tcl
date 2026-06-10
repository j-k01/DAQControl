# Post-mortem + environmental isolation for the stage-1 hang.
#
# Phase A: read the mailbox WITHOUT running anything (DDR content survives
#          the recovery reset): 0xDA000001 there means the last run reached
#          main() before the core became unhaltable; zeros mean BSP startup
#          never completed.
# Phase B: download the ELF but do NOT run it. Wait, then check the halted
#          core is still debuggable (rrd pc). If debug access degrades while
#          the core executes nothing, the problem is purely environmental
#          (watchdog/power/JTAG), not our code.
# Phase C: con (stage-1 spin loop), wait, stop. If halt fails only here,
#          the unhaltability is tied to the core actually running.
set script_dir [file dirname [file normalize [info script]]]
set elf_file [file join $script_dir sw ps_eth_workspace ps_eth_stream Debug ps_eth_stream.elf]

connect
after 1000
targets -set -filter {name =~ "Cortex-A53 #0"}

puts "=== PHASE A: post-mortem mailbox (no code run) ==="
catch {stop}
if {[catch {rst -processor -clear-registers} e]} {
    puts "rst -processor: $e"
}
after 500
if {[catch {mrd 0x0F000000 8} w]} {
    puts "A: mrd failed: $w"
} else {
    puts "A: $w"
}

puts "=== PHASE B: ELF loaded, core HALTED for 8s ==="
dow $elf_file
after 8000
if {[catch {rrd pc} pc]} {
    puts "B: debug access lost while halted: $pc"
} else {
    puts "B: still debuggable while halted. $pc"
    if {[catch {mrd 0x0F000000 4} w]} {
        puts "B: mrd failed: $w"
    } else {
        puts "B: $w"
    }
}

puts "=== PHASE C: run stage-1 spin for 8s, then halt ==="
if {[catch {con} e]} {
    puts "C: con failed: $e"
} else {
    after 8000
    if {[catch {stop} e]} {
        puts "C: HALT FAILED after running: $e"
    } else {
        puts "C: halt OK after running."
        catch {puts "C: [rrd pc]"}
        if {[catch {mrd 0x0F000000 8} w]} {
            puts "C: mrd failed: $w"
        } else {
            puts "C: $w"
        }
    }
}
