# Source a Tcl script with Vivado/XSCT command-echo suppressed.
#
# Vivado/XSCT `source` echoes every command to the console as it executes unless
# `-notrace` is given, so a normal `-source program_and_load.tcl` dumps the whole
# script (all the proc bodies) to the terminal on every run. This wrapper sources
# the real script with -notrace so a successful programming run stays quiet.
#
# Usage:
#   vivado -mode batch -source quiet.tcl -tclargs <script.tcl> [args...]
#   xsct   quiet.tcl <script.tcl> [args...]
#   xsdb   quiet.tcl <script.tcl> [args...]

if {![info exists argv]} { set argv {} }
if {[llength $argv] < 1} {
    puts "quiet.tcl: usage: quiet.tcl <script.tcl> \[args...\]"
    exit 1
}

set _target [lindex $argv 0]
if {![file exists $_target]} {
    set _alt [file join [file dirname [file normalize [info script]]] $_target]
    if {[file exists $_alt]} { set _target $_alt }
}

# Pass the remaining args through to the target as its own argv/argc.
set argv [lrange $argv 1 end]
set argc [llength $argv]

source $_target
