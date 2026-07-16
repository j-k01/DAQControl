# Read the A53 Ethernet-app mailbox twice, resuming the core between reads.
# This uses the A53 debug target (never the poisoned PSU/DAP DDR path).
# It briefly halts/resumes A53 but does not program or reset anything.

proc fail {message code} {
    puts "FAIL JTAG: $message"
    exit $code
}

proc read_eth_mailbox {} {
    if {[catch {stop} stop_err]} {
        puts "WARN stop: $stop_err"
    }
    after 150
    if {[catch {mrd -value 0x0F000000 8} values]} {
        catch {con}
        fail "cannot read A53 mailbox: $values" 2
    }
    catch {con}
    return $values
}

connect
after 500
if {[catch {targets -set -filter {name =~ "Cortex-A53 #0"}} target_err]} {
    fail "cannot select Cortex-A53 #0: $target_err" 2
}

set first [read_eth_mailbox]
after 1400
set second [read_eth_mailbox]

if {[llength $first] < 4 || [llength $second] < 4} {
    fail "unexpected mailbox values: first='$first' second='$second'" 2
}

set progress [expr {wide([lindex $second 0]) & 0xFFFFFFFF}]
set hb1      [expr {wide([lindex $first 1])  & 0xFFFFFFFF}]
set hb2      [expr {wide([lindex $second 1]) & 0xFFFFFFFF}]
set rx       [expr {wide([lindex $second 2]) & 0xFFFFFFFF}]
set tx       [expr {wide([lindex $second 3]) & 0xFFFFFFFF}]

puts [format "ETH_MAILBOX progress=0x%08X heartbeat=%u->%u rx_cmds=%u tx_pkts=%u" \
      $progress $hb1 $hb2 $rx $tx]

if {$progress == 0xDA0000FF && $hb2 != $hb1} {
    puts "PASS JTAG: A53 Ethernet app main loop is alive."
    exit 0
}
if {$progress == 0xDA000003} {
    fail "A53 is stuck in PHY autonegotiation (check GEM3 cable/link LEDs)." 3
}
if {$progress == 0xDA000004 || $progress == 0xDA000005 || $progress == 0xDA000006} {
    fail [format "A53 reached network init (0x%08X) but main loop/heartbeat is not running." $progress] 3
}
if {($progress & 0xFFFF0000) == 0xDAE00000} {
    fail [format "A53 reported Ethernet init error 0x%08X." $progress] 3
}
fail [format "A53 app is absent/stale (progress 0x%08X, heartbeat %u->%u)." \
      $progress $hb1 $hb2] 3
