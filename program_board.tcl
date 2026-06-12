# program_board.tcl -- one-shot ZCU102 program over JTAG from committed prebuilt
# artifacts. This is a VOLATILE load (config SRAM + processor download); nothing
# is written to QSPI/SD flash, so it is gone on power-cycle.
#
# Run on whatever machine has the board's JTAG (a running hw_server), from a
# fresh clone -- no Vivado/Vitis workspace or build needed. Works from ANY
# Xilinx shell; if it isn't already an xsct shell it relaunches itself under
# xsct automatically:
#
#     xsct program_board.tcl
#     xsdb program_board.tcl
#     vivado -mode batch -source program_board.tcl     # relaunches under xsct
#
# (On the capitolpeak build host: ~/bin/with_xilinx_2024_1 xsct program_board.tcl)
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

# ---- run-anywhere shim --------------------------------------------------
# The actual bring-up needs xsct/xsdb commands (connect/fpga/dow/con). If this
# script was sourced from a different terminal -- Vivado batch
# (`vivado -mode batch -source program_board.tcl`), plain tclsh, etc. -- those
# commands don't exist, so we locate xsct and relaunch ourselves under it. That
# way the SAME one command works from any shell.
proc find_xsct_command {} {
    if {[info exists ::env(XSCT)] && [file exists $::env(XSCT)]} {
        return [list $::env(XSCT)]
    }
    set roots {}
    if {[info exists ::env(XILINX_VITIS)]} { lappend roots $::env(XILINX_VITIS) }
    if {[info exists ::env(XILINX_VIVADO)]} {
        set ver [file tail $::env(XILINX_VIVADO)]
        lappend roots [file join [file dirname [file dirname $::env(XILINX_VIVADO)]] Vitis $ver]
    }
    foreach root $roots {
        foreach name {xsct xsct.bat} {
            set c [file join $root bin $name]
            if {[file exists $c]} { return [list $c] }
        }
    }
    # Vivado-only installs ship xsdb in Vivado/bin and it supports all we use.
    if {[info exists ::env(XILINX_VIVADO)]} {
        foreach name {xsdb xsdb.bat} {
            set c [file join $::env(XILINX_VIVADO) bin $name]
            if {[file exists $c]} { return [list $c] }
        }
    }
    foreach name {xsct xsct.bat xsdb xsdb.bat} {
        set r [auto_execok $name]
        if {$r ne ""} { return $r }
    }
    return {}
}

if {[llength [info commands connect]] == 0 || [llength [info commands fpga]] == 0
    || [llength [info commands dow]] == 0} {
    set xsct [find_xsct_command]
    if {$xsct eq ""} {
        error "Not an xsct/xsdb shell and could not find xsct.\nRun: xsct program_board.tcl  (or set \$XSCT / source the Vitis settings64)."
    }
    puts "Not an xsct shell; relaunching under xsct: $xsct"
    exec {*}$xsct [file normalize [info script]] >@ stdout 2>@ stderr
    return
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
