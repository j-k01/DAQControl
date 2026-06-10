# Read the PS Ethernet app debug mailbox (see sw/ps_eth_stream/README.md).
# Reads through the A53 core: stop briefly, dump, resume. The lwIP app
# tolerates the pause. Mailbox base: 0x0F000000, 8 u32 words.
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
puts [mrd 0x0F000000 8]
puts "PC and state:"
catch {puts [rrd pc]}
if {$was_running} {
    catch {con}
}
puts "progress legend: DA000001 main, DA000002 gic, DA000003 lwip_init,"
puts "  DA000004 emac_added, DA000005 netif_up, DA000006 udp_ready, DA0000FF loop"
puts "  DAE000xx = error (1 emac_add, 2 udp_new, 3 udp_bind)"
