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

exec xvlog.bat -sv \
    ../../src/jesd/dac_source_to_converter_preimage.v \
    ../dac_source_to_converter_preimage_tb.sv

exec xelab.bat dac_source_to_converter_preimage_tb -snapshot dac_source_to_converter_preimage_tb
exec xsim.bat dac_source_to_converter_preimage_tb -runall

puts "Dataplane/preimage unit tests passed."
