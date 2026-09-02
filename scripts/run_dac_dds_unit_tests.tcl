set script_dir [file dirname [file normalize [info script]]]
set root_dir [file normalize [file join $script_dir ..]]

cd $root_dir
file mkdir sim/work_dac_dds
cd sim/work_dac_dds

exec xvlog.bat -sv     ../../src/jesd/dac_source_crossbar.v     ../../src/jesd/dac_source_to_converter_preimage.v     ../../src/jesd/dac39j84_physical_mapper.v     ../../src/jesd/dac39j84_sample_remap.v     ../../src/jesd/litejesd_dac_tx.v     ../../src/jesd/daq_litejesd_dac_tx_path.v     ../dac_dds_restart_tb.sv     [file join $::env(XILINX_VIVADO) data verilog src glbl.v]

exec xelab.bat -L unisims_ver dac_dds_restart_tb glbl -snapshot dac_dds_restart_tb
set out [exec xsim.bat dac_dds_restart_tb -runall]
puts $out
if {[string first "TB_RESULT: PASS" $out] < 0} {
    error "dac_dds_restart_tb did not report TB_RESULT: PASS"
}

puts "DAC DDS unit tests passed."