set script_dir [file dirname [file normalize [info script]]]

set create_args {}
set build_args {}

for {set i 0} {$i < [llength $::argv]} {incr i} {
    set arg [lindex $::argv $i]
    switch -- $arg {
        "--with-staged-gt" -
        "--with-litejesd" {
            lappend create_args $arg
        }
        "--bake" {
            lappend build_args $arg
        }
        "--jobs" {
            incr i
            if {$i >= [llength $::argv]} {
                error "--jobs requires an integer argument."
            }
            lappend build_args --jobs [lindex $::argv $i]
        }
        default {
            error "Unknown rebuild.tcl argument '$arg'. Supported arguments: --with-staged-gt, --with-litejesd, --bake, --jobs <n>."
        }
    }
}

puts "=== DAQ_LAUNCH rebuild: create_project.tcl [join $create_args { }] ==="
set ::argv $create_args
source [file join $script_dir create_project.tcl]
close_project

puts "=== DAQ_LAUNCH rebuild: build.tcl [join $build_args { }] ==="
set ::argv $build_args
source [file join $script_dir build.tcl]
