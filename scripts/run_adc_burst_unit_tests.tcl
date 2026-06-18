set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file normalize [file join $script_dir ..]]

cd $root_dir
file mkdir sim/work_adc_burst
cd sim/work_adc_burst

if {![info exists ::env(XILINX_VIVADO)]} {
    error "XILINX_VIVADO is not set; cannot locate glbl.v for XPM simulation"
}

exec xvlog.bat -sv \
    ../../src/adc_burst_capture.v \
    ../adc_burst_capture_tb.sv \
    [file join $::env(XILINX_VIVADO) data verilog src glbl.v]

exec xelab.bat -L xpm adc_burst_capture_tb glbl -snapshot adc_burst_capture_tb
exec xsim.bat adc_burst_capture_tb -runall

puts "ADC burst capture unit tests passed."
