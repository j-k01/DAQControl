set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file normalize [file join $script_dir ..]]

cd $root_dir
file mkdir sim/work_dataplane
cd sim/work_dataplane

exec xvlog.bat -sv \
    ../../src/dac_bram_player.v \
    ../../src/adc_bram_capture.v \
    ../dataplane_bram_tb.sv

exec xelab.bat dataplane_bram_tb -snapshot dataplane_bram_tb
exec xsim.bat dataplane_bram_tb -runall

puts "Dataplane BRAM unit tests passed."
