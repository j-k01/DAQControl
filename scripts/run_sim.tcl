set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file normalize [file join $script_dir ..]]

cd $root_dir

file mkdir sim/work
cd sim/work

exec xvlog.bat -sv \
    ../launch_stubs.v \
    ../../src/cdc_vector_sync.v \
    ../../src/clock_activity_monitor.v \
    ../../src/signal_activity_monitor.v \
    ../../src/hmc7044_init.v \
    ../../src/top.v \
    ../top_tb.sv

exec xelab.bat top_tb -snapshot top_tb
exec xsim.bat top_tb -runall

exec xvlog.bat -sv \
    ../../src/hmc7044_init.v \
    ../hmc7044_init_tb.sv

exec xelab.bat hmc7044_init_tb -snapshot hmc7044_init_tb
exec xsim.bat hmc7044_init_tb -runall
