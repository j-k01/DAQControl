# flash_board.tcl -- one-shot ZCU102 reprogram from committed prebuilt artifacts.
#
# Run on whatever machine has the board's JTAG (a running hw_server), from a
# fresh clone -- no Vivado/Vitis workspace or build needed:
#
#     xsct flash_board.tcl
#
# (On the capitolpeak build host: ~/bin/with_xilinx_2024_1 xsct flash_board.tcl)
#
# It does the whole bring-up in ONE xsct session:
#   1. connect + recover the PS/DAP if it is in the wedged state
#   2. program the FPGA bitstream  (xsct `fpga`, no Vivado hw_manager needed)
#   3. run psu_init (PS clocks + DDR)
#   4. load + start the MicroBlaze firmware
#   5. load + start the A53 PS-Ethernet streamer
#
# All artifacts come from prebuilt/ so a `git pull` is all a remote device needs.
# These must be kept current -- see refresh-prebuilt instructions in the repo.

set script_dir [file dirname [file normalize [info script]]]
proc P {name} { return [file join $::script_dir prebuilt $name] }

set bit     [P top.bit]
set psu     [P psu_init.tcl]
set mb_elf  [P firmware.elf]
set a53_elf [P ps_eth_stream.elf]

foreach {label f} [list bitstream $bit "MicroBlaze ELF" $mb_elf "A53 ELF" $a53_elf] {
    if {![file exists $f]} {
        error "missing $label: $f\n(prebuilt artifacts not committed? see repo README)"
    }
}

if {[llength [info commands connect]] == 0 || [llength [info commands dow]] == 0} {
    error "flash_board.tcl must run under XSCT/XSDB (xsct flash_board.tcl), not Vivado Tcl."
}

puts "== connecting to hw_server =="
connect

# ---- 1. recover the PS/DAP if wedged (clears the AXI-AP sticky error and
#         resets the PS to a clean state so psu_init takes). Harmless if healthy;
#         rst -system also wipes the PL, so this MUST precede programming. ----
puts "== resetting PS (recover DAP if wedged) =="
if {![catch {targets -set -nocase -filter {name =~ "*PSU*"}}]} {
    catch {rst -system}
} elseif {![catch {targets -set -nocase -filter {name =~ "DAP*"}}]} {
    puts "   PSU missing; resetting through DAP then clearing it"
    catch {rst -system}
    after 3000
    catch {rst -dap}
} else {
    puts "   WARNING: no PSU/DAP target; continuing"
}
after 3000

# ---- 2. program the FPGA ----
puts "== programming FPGA: $bit =="
if {[catch {fpga -file $bit} err]} {
    # some xsct builds need the jtag PL device selected first
    catch {targets -set -nocase -filter {name =~ "*xczu9*" || name =~ "*PL*"}}
    fpga -file $bit
}
after 1000

# ---- 3. psu_init (PS clocks + DDR) ----
if {[file exists $psu]} {
    puts "== psu_init: $psu =="
    catch {targets -set -nocase -filter {name =~ "*PSU*"}}
    if {[catch {uplevel #0 [list source $psu]} err]} {
        puts "   WARNING: source psu_init failed: $err"
    }
    foreach p {psu_init psu_ps_pl_isolation_removal psu_ps_pl_reset_config} {
        if {[llength [info commands $p]] > 0} {
            if {[catch {$p} e]} { puts "   WARNING: $p: $e" }
        }
    }
} else {
    puts "== psu_init skipped (no prebuilt/psu_init.tcl) =="
}

# ---- 4. MicroBlaze firmware ----
puts "== loading MicroBlaze firmware: $mb_elf =="
set mb_ok 0
foreach filt {{name =~ "MicroBlaze #*"} {name =~ "*MicroBlaze*"} {name =~ "*microblaze*"}} {
    if {![catch {targets -set -filter $filt}]} { set mb_ok 1; break }
}
if {!$mb_ok} { error "No MicroBlaze target after programming -- FPGA program failed?" }
catch {stop}
catch {rst -processor}
dow $mb_elf
con
puts "   MicroBlaze running."

# ---- 5. A53 PS-Ethernet streamer ----
puts "== loading A53 PS-eth streamer: $a53_elf =="
set a53_ok 0
foreach filt {{name =~ "Cortex-A53 #0"} {name =~ "*Cortex-A53 #0*"} {name =~ "*A53*#0*"}} {
    if {![catch {targets -set -filter $filt}]} { set a53_ok 1; break }
}
if {!$a53_ok} { error "No Cortex-A53 #0 target -- PS/DAP not recovered?" }
catch {stop}
catch {rst -processor -clear-registers}
dow $a53_elf
con
puts "   A53 PS-Ethernet streamer running."

puts "== DONE: FPGA + MicroBlaze + A53 all programmed and running. =="
