set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file normalize [file join $script_dir ..]]

cd $root_dir

file mkdir sim/work_litejesd
cd sim/work_litejesd

exec xvlog.bat -sv -d DAQ_WITH_GTH -d DAQ_WITH_LITEJESD \
    ../launch_stubs.v \
    ../../src/cdc_vector_sync.v \
    ../../src/clock_activity_monitor.v \
    ../../src/signal_activity_monitor.v \
    ../../src/hmc7044_init.v \
    ../../src/dac39j84_init.v \
    ../../src/ads54j60_init.v \
    ../../src/dac_bram_player.v \
    ../../src/adc_bram_capture.v \
    ../../src/jesd/litejesd_dac_tx.v \
    ../../src/jesd/daq_litejesd_dac_tx_path.v \
    ../../src/jesd/litejesd_adc1_rx.v \
    ../../src/jesd/daq_litejesd_adc1_rx_path.v \
    ../../src/top.v

exec xelab.bat top -snapshot top_litejesd_compile

puts "LiteJESD/GTH top compile check passed."
