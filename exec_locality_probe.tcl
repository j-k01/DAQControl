# Where can the A53 execute without wedging?
#
# Phase OCM: flat-asm spin in OCM (0xFFFC0000, LPD path -- instruction
#            fetches never cross the FPD/CCI route to DDR).
#            Counter -> 0xFFFC0100/0x104.
# Phase DDR: same loop in DDR (0x01000000), counter -> 0x10000000/4 --
#            inside the chip0 DMA buffer, which the MicroBlaze can read with
#            "DDRD 0 0 2" over COM10 even while JTAG says the core is
#            unhaltable. Signature 0xDA00C0DE at +0, counter at +4:
#            frozen counter  = core genuinely stalled;
#            advancing       = core alive, only debug/halt path broken.
#
# Both programs: MMU/caches off, EL3, no BSP. Halted-core accesses to both
# regions are known-good, so any wedge here is from free-running execution.

connect
after 1000
catch {targets -set -nocase -filter {name =~ "DAP*"}}
catch {rst -dap}
after 1000

proc revive {} {
    targets -set -filter {name =~ "Cortex-A53 #0"}
    catch {stop}
    if {[catch {rst -processor -clear-registers} e]} {
        puts "  rst-proc: $e"
    }
    after 300
}

proc load_and_run {name base prog clear0 wait_s} {
    revive
    set a $base
    foreach w $prog {
        mwr $a $w
        set a [expr {$a + 4}]
    }
    mwr $clear0 0
    mwr [expr {$clear0 + 4}] 0
    rwr pc $base
    puts "$name: running from [format 0x%08X $base] for ${wait_s}s..."
    con
    after [expr {$wait_s * 1000}]
    if {[catch {stop} e]} {
        puts "$name: HALT FAILED: $e"
        return 0
    }
    puts "$name: halt OK"
    catch {puts "  pc: [rrd pc]"}
    if {[catch {mrd $clear0 2} w]} {
        puts "  result read failed: $w"
    } else {
        puts "  $w"
    }
    return 1
}

# movz x1,#HI,lsl16 / movz w2,#0xC0DE / movk w2,#0xDA00,lsl16 /
# str w2,[x1,#off] / movz w3,#0 / add w3,w3,#1 / str w3,[x1,#off+4] / b .-8

# OCM: x1=0xFFFC0000, stores at +0x100/+0x104 (clear of the code)
set ocm_prog {0xD2BFFF81 0x52981BC2 0x72BB4002 0xB9010022 0x52800003 0x11000463 0xB9010423 0x17FFFFFE}

# DDR: code at 0x01000000, x1=0x10000000 (chip0 DMA buffer), stores at +0/+4
set ddr_prog {0xD2A20001 0x52981BC2 0x72BB4002 0xB9000022 0x52800003 0x11000463 0xB9000423 0x17FFFFFE}

puts "=== PHASE OCM ==="
set ocm_ok [load_and_run OCM 0xFFFC0000 $ocm_prog 0xFFFC0100 10]

puts "=== PHASE DDR ==="
set ddr_ok [load_and_run DDR 0x01000000 $ddr_prog 0x10000000 10]

puts "=== VERDICT ==="
puts "OCM execution survives: $ocm_ok"
puts "DDR execution survives: $ddr_ok"
if {$ocm_ok && !$ddr_ok} {
    puts ">>> kill is specific to instruction fetch from DDR (FPD/CCI path)"
    puts ">>> now read the counter over COM10: DDRD 0 0 2 (twice, few seconds apart)"
} elseif {!$ocm_ok} {
    puts ">>> even OCM execution wedges -- free-running the core at all is the trigger"
}
