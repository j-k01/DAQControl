# Read the PS Ethernet app debug mailbox (see sw/ps_eth_stream/README.md).
# Mailbox base: 0x0F000000, 8 u32 words.
#
# IMPORTANT: never read memory through the PSU/DAP target in this setup. The
# DAP AXI-AP path to DDR times out, sets a sticky DAP error (0x30000021), and
# poisons the JTAG view until rst -system + rst -dap (which wipes the PL).
# Memory access only works through a halted A53 core, so this script stops
# the core briefly, dumps the mailbox, and resumes it.
connect
after 1000

targets -set -filter {name =~ "Cortex-A53 #0"}
set was_running 0
if {[catch {stop} stop_err]} {
    puts "stop: $stop_err"
} else {
    set was_running 1
}
after 200
puts "PS Ethernet mailbox at 0x0F000000:"
if {[catch {mrd 0x0F000000 8} words]} {
    puts "core mrd failed: $words"
} else {
    puts $words
}
catch {puts "PC: [rrd pc]"}
if {$was_running} {
    catch {con}
}

puts "progress legend: DA000001 main, DA000002 gic, DA000003 lwip_init,"
puts "  DA000004 emac_added, DA000005 netif_up, DA000006 udp_ready, DA0000FF loop"
puts "  DAE000xx = error (1 emac_add, 2 udp_new, 3 udp_bind)"
