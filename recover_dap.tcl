# Recover the ZynqMP PS debug view when APU/PSU targets disappear and the
# DAP reports "AXI AP transaction error" (status 0x30000021).
#
# Validated 2026-06-10: rst -dap on the DAP target clears the sticky error
# and brings the PSU/APU/Cortex-A53 targets back. rst -system alone does
# NOT clear it, and rst -por is not supported on this target.
#
# After recovery the PS has been reset: the PL is cleared, so rerun
# program_and_load.tcl and then reload any PS apps.
connect
after 2000
puts "=== Targets before recovery ==="
puts [targets]

if {![catch {targets -set -nocase -filter {name =~ "*PSU*"}}]} {
    puts "PSU target present; issuing rst -system."
    if {[catch {rst -system} err]} {
        puts "rst -system failed: $err"
    }
} elseif {![catch {targets -set -nocase -filter {name =~ "DAP*"}}]} {
    puts "PSU missing; clearing sticky DAP error with rst -dap."
    if {[catch {rst -dap} err]} {
        puts "rst -dap failed: $err"
    }
} else {
    puts "ERROR: no PSU/DAP target available."
}

after 5000
puts "=== Targets after recovery ==="
puts [targets]
