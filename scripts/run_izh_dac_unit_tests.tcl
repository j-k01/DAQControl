set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file normalize [file join $script_dir ..]]
set izh_neuron_file [file normalize [file join $root_dir .. .. IZH_neuron izh_neuron.v]]

cd $root_dir

if {![file exists $izh_neuron_file]} {
    error "IZH neuron source not found: $izh_neuron_file"
}

file mkdir sim/work_izh_dac
cd sim/work_izh_dac

exec xvlog.bat -sv \
    $izh_neuron_file \
    ../../src/izh_dac_channel.v \
    ../../src/izh_dac_bank.sv \
    ../izh_dac_integration_tb.sv

exec xelab.bat izh_dac_integration_tb -snapshot izh_dac_integration_tb
exec xsim.bat izh_dac_integration_tb -runall

puts "IZH DAC unit tests passed."
