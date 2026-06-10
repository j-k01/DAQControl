# Read the PS Ethernet app debug mailbox (see sw/ps_eth_stream/README.md).
# Mailbox base: 0x0F000000, 8 u32 words.
#
# Prefers the PSU/DAP target so the read works even when the A53 is hung on
# a bus transaction (halt timeout). Falls back to stopping the A53 core.
connect
after 1000

set done 0
if {![catch {targets -set -filter {name =~ "*PSU*"}}]} {
    puts "Reading via PSU/DAP target:"
    if {![catch {mrd 0x0F000000 8} words]} {
        puts $words
        set done 1
    } else {
        puts "PSU mrd failed: $words"
    }
}

if {!$done} {
    puts "Falling back to A53 core read (stop/mrd/continue):"
    targets -set -filter {name =~ "Cortex-A53 #0"}
    set was_running 0
    if {[catch {stop} stop_err]} {
        puts "stop: $stop_err"
    } else {
        set was_running 1
    }
    after 200
    if {![catch {mrd 0x0F000000 8} words]} {
        puts $words
    } else {
        puts "A53 mrd failed: $words"
    }
    catch {puts "PC: [rrd pc]"}
    if {$was_running} {
        catch {con}
    }
}

puts "progress legend: DA000001 main, DA000002 gic, DA000003 lwip_init,"
puts "  DA000004 emac_added, DA000005 netif_up, DA000006 udp_ready, DA0000FF loop"
puts "  DAE000xx = error (1 emac_add, 2 udp_new, 3 udp_bind)"
