# PS DDR address-line integrity probe.
#
# 2026-06-11 finding: this board's PS DDR4 SODIMM drops byte-address bit 14.
# A write to base+0x4000 lands on base (16 KB aliasing), and accesses on the
# flaky line sometimes never complete, hanging the DDRC port and the coherent
# interconnect (unhaltable core, DAP timeouts). This is why every A53 ELF
# died instantly: the download self-corrupts (sections 16 KB apart overwrite
# each other), so the entry point executes the wrong code.
#
# Run after reseating/replacing the SODIMM. All "bitN" lines must read back
# their own value (0xB0000000+N) and the base must still hold 0x000000AA.
#
#   xsct ddr_alias_probe.tcl
#
# Requires a JTAG-visible board; does a full PS reset + psu_init first.

set script_dir [file dirname [file normalize [info script]]]

set psu_init_file ""
foreach root [list \
    [file join $script_dir sw ps_eth_workspace] \
    [file join $script_dir sw workspace] \
    [file join $script_dir hw]] {
    set found [glob -nocomplain -directory $root -types f */psu_init.tcl */*/psu_init.tcl */*/*/psu_init.tcl]
    if {[llength $found] > 0} {
        set psu_init_file [lindex $found 0]
        break
    }
}
if {$psu_init_file eq ""} {
    error "No psu_init.tcl found. Run build_ps_eth_stream.tcl or build_sw.tcl first."
}

connect
after 1000
targets -set -nocase -filter {name =~ "PSU"}
rst -system
after 3000
targets -set -filter {name =~ "Cortex-A53 #0"}
catch {stop}
rst -processor -clear-registers
after 500
source $psu_init_file
psu_init
after 1000

set failures 0
foreach base {0x30000000 0x05000000 0x01000000} {
    puts "=== alias test at [format 0x%08X $base] ==="
    mwr $base 0x000000AA
    foreach bit {12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27} {
        set addr [expr {$base + (1 << $bit)}]
        if {[catch {mwr $addr [expr {0xB0000000 + $bit}]} e]} {
            puts "  bit$bit [format 0x%08X $addr]: WRITE HUNG: [lindex [split $e "\n"] 0]"
            incr failures
            catch {targets -set -filter {name =~ "Cortex-A53 #0"}; stop; rst -processor -clear-registers}
            after 300
        }
    }
    if {[catch {mrd -value $base} v]} {
        puts "  base re-read HUNG: $v"
        incr failures
        catch {targets -set -filter {name =~ "Cortex-A53 #0"}; stop; rst -processor -clear-registers}
        after 300
        continue
    }
    if {$v == 0xAA} {
        puts "  base intact (0x000000AA): no write aliased onto it"
    } else {
        puts "  BASE CLOBBERED: reads [format 0x%08X $v] - the write to bit [expr {$v - 0xB0000000}] aliased onto base"
        incr failures
    }
    foreach bit {12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27} {
        set addr [expr {$base + (1 << $bit)}]
        if {[catch {mrd -value $addr} rv]} {
            puts "  bit$bit [format 0x%08X $addr]: READ HUNG"
            incr failures
            catch {targets -set -filter {name =~ "Cortex-A53 #0"}; stop; rst -processor -clear-registers}
            after 300
            continue
        }
        if {$rv != [expr {0xB0000000 + $bit}]} {
            puts "  bit$bit [format 0x%08X $addr]: MISMATCH read [format 0x%08X $rv]"
            incr failures
        }
    }
}

if {$failures == 0} {
    puts "=== DDR ADDRESS LINES OK: no aliasing, no hangs ==="
} else {
    puts "=== DDR FAULTY: $failures failure(s) - reseat or replace the PS DDR4 SODIMM ==="
}
