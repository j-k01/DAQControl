# Flat-asm survival test: does the A53 wedge because it RUNS CODE from DDR,
# or because of what the BSP startup does (MMU + cache enable -> coherent
# traffic through CCI)?
#
# Hand-assembled 8-instruction program written straight into DDR through the
# halted core (no toolchain, no BSP, no ELF). Runs at EL3 with MMU and
# caches OFF -- the same access regime as the debugger, which we have proven
# stays healthy for 7+ minutes.
#
#   0x01000000: movz x1, #0x0F00, lsl #16   ; x1 = 0x0F000000 (mailbox)
#   0x01000004: movz w2, #0xC0DE
#   0x01000008: movk w2, #0xDA00, lsl #16   ; w2 = 0xDA00C0DE
#   0x0100000C: str  w2, [x1]               ; mailbox[0] = signature
#   0x01000010: movz w3, #0
#   0x01000014: add  w3, w3, #1             ; loop:
#   0x01000018: str  w3, [x1, #4]           ; mailbox[1] = counter
#   0x0100001C: b    0x01000014
#
# stage-1 BSP build died within 6 s of con. If this survives 10+ s, halts
# cleanly, and the counter advances between two halts, the BSP startup
# (cache/MMU enable) is conclusively the killer.

connect
after 1000
catch {targets -set -nocase -filter {name =~ "DAP*"}}
catch {rst -dap}
after 1000

targets -set -filter {name =~ "Cortex-A53 #0"}
catch {stop}
if {[catch {rst -processor -clear-registers} e]} {
    puts "rst-proc: $e"
}
after 300

set base 0x01000000
set prog {0xD2A1E001 0x52981BC2 0x72BB4002 0xB9000022 0x52800003 0x11000463 0xB9000423 0x17FFFFFE}
set a $base
foreach w $prog {
    mwr $a $w
    set a [expr {$a + 4}]
}
puts "program verify:"
puts [mrd $base 8]

mwr 0x0F000000 0
mwr 0x0F000004 0

rwr pc $base
puts "PC=[rrd pc]"
puts "running flat-asm spin (no BSP, MMU/caches off)..."
con
after 10000

if {[catch {stop} e]} {
    puts "MINI HALT FAILED after 10s: $e"
    puts ">>> even flat-asm DDR execution wedges -- NOT a BSP cache/MMU issue"
} else {
    puts "MINI halt OK after 10s"
    catch {puts "  pc: [rrd pc]"}
    if {[catch {mrd 0x0F000000 2} w]} {
        puts "  mailbox read failed: $w"
    } else {
        puts "  $w"
    }
    con
    after 5000
    if {[catch {stop} e2]} {
        puts "MINI 2nd HALT FAILED: $e2"
    } else {
        puts "MINI 2nd halt OK (counter should have advanced)"
        catch {puts "  pc: [rrd pc]"}
        if {[catch {mrd 0x0F000000 2} w2]} {
            puts "  mailbox read failed: $w2"
        } else {
            puts "  $w2"
        }
    }
}
