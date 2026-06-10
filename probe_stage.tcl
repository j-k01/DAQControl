# Load the ps_eth_stream ELF onto A53 #0, let it run briefly, then halt and
# dump the debug mailbox through the core.
#
# IMPORTANT: do NOT read memory through the PSU/DAP target in this setup.
# The DAP AXI-AP path to DDR does not work here: it times out, sets a sticky
# DAP error (status 0x30000021), and poisons the JTAG view until rst -dap.
# Memory access only works through a halted A53 core.
set script_dir [file dirname [file normalize [info script]]]

if {![info exists argv]} {
    set argv {}
}
set run_seconds 5
if {[llength $argv] > 0} {
    set run_seconds [lindex $argv 0]
}

set elf_file [file join $script_dir sw ps_eth_workspace ps_eth_stream Debug ps_eth_stream.elf]

connect
after 1000

# Clear any sticky DAP error left by earlier AXI-AP attempts.
catch {targets -set -nocase -filter {name =~ "DAP*"}}
catch {targets -set -nocase -filter {name =~ "*PSU*"}}
catch {rst -dap}
after 1000

targets -set -filter {name =~ "Cortex-A53 #0"}
puts "Stopping/resetting A53 #0..."
catch {stop}
if {[catch {rst -processor -clear-registers} err]} {
    puts "rst -processor failed: $err"
}

puts "Downloading: $elf_file"
dow $elf_file

puts "Running for $run_seconds s..."
con
after [expr {$run_seconds * 1000}]

if {[catch {stop} err]} {
    puts "HALT FAILED: $err"
    puts "Core is unhaltable: the stage under test hangs the system."
} else {
    puts "Halt OK. PC and mailbox:"
    catch {puts [rrd pc]}
    if {[catch {mrd 0x0F000000 8} words]} {
        puts "core mrd failed: $words"
    } else {
        puts $words
    }
    con
}
puts "progress legend: DA000001 main, DA000002 gic, DA000003 lwip_init,"
puts "  DA000004 emac_added, DA000005 netif_up, DA000006 udp_ready, DA0000FF loop"
