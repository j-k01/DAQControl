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
            error "Unknown run_izh_dac_unit_tests.tcl argument '$arg'. Supported arguments: --izh-neuron-file <path>."
        }
    }
}

cd $root_dir

source [file join $script_dir resolve_izh_neuron.tcl]
set izh_neuron_file [resolve_izh_neuron_file $root_dir $izh_neuron_file_override]

file mkdir sim/work_izh_dac
cd sim/work_izh_dac

proc run_tb {snapshot sources} {
    set xvlog_cmd [list xvlog.bat -sv]
    foreach source $sources {
        lappend xvlog_cmd $source
    }
    exec {*}$xvlog_cmd
    exec xelab.bat -L xpm $snapshot -snapshot $snapshot
    set out [exec xsim.bat $snapshot -runall]
    puts $out
    if {[string first "TB_RESULT: PASS" $out] < 0} {
        error "$snapshot did not report TB_RESULT: PASS"
    }
}

run_tb izh_spike_shaper_tb [list \
    ../../src/izh_spike_shaper.v \
    ../izh_spike_shaper_tb.sv \
]

run_tb spike_shape_bram_integration_tb [list \
    ../../src/izh_spike_shaper.v \
    ../../src/spike_shape_bram_bank.sv \
    ../spike_shape_bram_integration_tb.sv \
]

run_tb izh_dac_integration_tb [list \
    $izh_neuron_file \
    ../../src/izh_dac_bank.sv \
    ../izh_dac_integration_tb.sv \
]

puts "IZH DAC unit tests passed."
