set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file normalize [file join $script_dir ..]]

cd $root_dir

set common_sources [list \
    ../launch_stubs.v \
    ../../src/cdc_vector_sync.v \
    ../../src/clock_activity_monitor.v \
    ../../src/signal_activity_monitor.v \
    ../../src/hmc7044_init.v \
    ../../src/dac39j84_init.v \
    ../../src/ads54j60_init.v \
    ../../src/dataplane_bram_ip.v \
    ../../src/dac_bram_player.v \
    ../../src/adc_bram_capture.v \
    ../../src/jesd/dac39j84_sample_remap.v \
    ../../src/jesd/litejesd_dac_tx.v \
    ../../src/jesd/daq_litejesd_dac_tx_path.v \
    ../../src/jesd/litejesd_adc1_rx.v \
    ../../src/jesd/daq_litejesd_adc1_rx_path.v \
    ../../src/top.v \
]

proc run_compile {work_dir snapshot extra_defines sources} {
    file mkdir $work_dir
    cd $work_dir

    set xvlog_cmd [list xvlog.bat -sv -d DAQ_WITH_GTH -d DAQ_WITH_LITEJESD]
    foreach define $extra_defines {
        lappend xvlog_cmd -d $define
    }
    foreach source $sources {
        lappend xvlog_cmd $source
    }

    exec {*}$xvlog_cmd
    exec xelab.bat top -snapshot $snapshot
}

run_compile sim/work_litejesd top_litejesd_compile {} $common_sources
cd $root_dir
run_compile sim/work_litejesd_bram top_litejesd_bram_compile {DAQ_WITH_BRAM_DATAPLANE} $common_sources

puts "LiteJESD/GTH top compile checks passed."
