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

exec xvlog.bat -sv \
    $izh_neuron_file \
    ../../src/izh_spike_trapezoid.v \
    ../../src/izh_dac_channel.v \
    ../../src/izh_dac_bank.sv \
    ../izh_dac_integration_tb.sv

exec xelab.bat izh_dac_integration_tb -snapshot izh_dac_integration_tb
exec xsim.bat izh_dac_integration_tb -runall

puts "IZH DAC unit tests passed."
