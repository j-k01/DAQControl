# Incremental rebuild of the ps_eth_stream app without regenerating the BSP.
# Much faster than build_ps_eth_stream.tcl for app-source-only changes.
#
# Optional argument: a DAQ_HALT_STAGE value for the bring-up bisect barriers
# in main.c (see sw/ps_eth_stream/src/main.c). Omit or pass -1 for a normal
# build with no barrier.
#
#   xsct build_app_only.tcl        ;# normal build
#   xsct build_app_only.tcl 3      ;# spin after lwip_init, before xemac_add
set script_dir [file dirname [file normalize [info script]]]

if {![info exists argv]} {
    set argv {}
}

set ws [file join $script_dir sw ps_eth_workspace]
if {![file isdirectory $ws]} {
    error "Workspace not found: $ws\nRun 'xsct build_ps_eth_stream.tcl' once first."
}
setws $ws

file copy -force [file join $script_dir sw ps_eth_stream src main.c] \
    [file join $ws ps_eth_stream src main.c]

set stage -1
if {[llength $argv] > 0} {
    set stage [lindex $argv 0]
}

# Drop any stale DAQ_HALT_STAGE symbol, then add the requested one.
if {![catch {app config -name ps_eth_stream define-compiler-symbols} symbols]} {
    foreach sym $symbols {
        if {[string match "DAQ_HALT_STAGE=*" $sym]} {
            catch {app config -name ps_eth_stream -remove define-compiler-symbols $sym}
        }
    }
}
if {$stage >= 0} {
    app config -name ps_eth_stream -add define-compiler-symbols DAQ_HALT_STAGE=$stage
    puts "Building with DAQ_HALT_STAGE=$stage barrier."
} else {
    puts "Building without halt barrier."
}

app build -name ps_eth_stream

puts "ELF: $ws/ps_eth_stream/Debug/ps_eth_stream.elf"
