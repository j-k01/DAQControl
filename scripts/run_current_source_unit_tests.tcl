set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file normalize [file join $script_dir ..]]
set izh_neuron_file_override ""

for {set i 0} {$i < [llength $::argv]} {incr i} {
    set arg [lindex $::argv $i]
    switch -- $arg {
        "--izh-neuron-file" {
            incr i
            if {$i >= [llength $::argv]} {
                error "--izh-neuron-file requires a path argument."
            }
            set izh_neuron_file_override [lindex $::argv $i]
        }
        default {
            error "Unknown run_current_source_unit_tests.tcl argument '$arg'. Supported arguments: --izh-neuron-file <path>."
        }
    }
}

cd $root_dir
source [file join $script_dir resolve_izh_neuron.tcl]
set izh_neuron_file [resolve_izh_neuron_file $root_dir $izh_neuron_file_override]

proc run_tb {snapshot sources {libs {}}} {
    set need_glbl [expr {[lsearch -exact $libs xpm] >= 0}]
    set xvlog_cmd [list xvlog.bat -sv]
    foreach source $sources {
        lappend xvlog_cmd $source
    }
    if {$need_glbl} {
        if {![info exists ::env(XILINX_VIVADO)]} {
            error "XILINX_VIVADO is not set; cannot locate glbl.v for XPM simulation"
        }
        lappend xvlog_cmd [file join $::env(XILINX_VIVADO) data verilog src glbl.v]
    }
    exec {*}$xvlog_cmd

    set xelab_cmd [list xelab.bat]
    foreach lib $libs {
        lappend xelab_cmd -L $lib
    }
    lappend xelab_cmd $snapshot
    if {$need_glbl} {
        lappend xelab_cmd glbl
    }
    lappend xelab_cmd -snapshot $snapshot
    exec {*}$xelab_cmd

    set out [exec xsim.bat $snapshot -runall]
    puts $out
    if {[string first "TB_RESULT: PASS" $out] < 0} {
        error "$snapshot did not report TB_RESULT: PASS"
    }
}

file mkdir sim/work_current_source
cd sim/work_current_source

run_tb izh_current_player_tb [list \
    ../../src/izh_current_player.v \
    ../izh_current_player_tb.sv \
]

run_tb dac_source_crossbar_tb [list \
    ../../src/jesd/dac_source_crossbar.v \
    ../dac_source_crossbar_tb.sv \
]

run_tb cur_monitor_cdc_tb [list \
    ../../src/jesd/cur_monitor_cdc.v \
    ../cur_monitor_cdc_tb.sv \
] {xpm}

run_tb izh_monitor_integ_tb [list \
    $izh_neuron_file \
    ../../src/izh_current_player.v \
    ../../src/izh_dac_bank.sv \
    ../../src/jesd/cur_monitor_cdc.v \
    ../izh_monitor_integ_tb.sv \
] {xpm}

puts "Current-source unit tests passed."
