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
            error "Unknown run_litejesd_compile.tcl argument '$arg'. Supported arguments: --izh-neuron-file <path>."
        }
    }
}

cd $root_dir

source [file join $script_dir resolve_izh_neuron.tcl]
set izh_neuron_file [resolve_izh_neuron_file $root_dir $izh_neuron_file_override]

set common_sources [list \
    ../launch_stubs.v \
    $izh_neuron_file \
    ../../src/cdc_vector_sync.v \
    ../../src/clock_activity_monitor.v \
    ../../src/signal_activity_monitor.v \
    ../../src/hmc7044_init.v \
    ../../src/dac39j84_init.v \
    ../../src/ads54j60_init.v \
    ../../src/dataplane_bram_ip.v \
    ../../src/dac_bram_player.v \
    ../../src/adc_bram_capture.v \
    ../../src/izh_spike_trapezoid.v \
    ../../src/izh_dac_channel.v \
    ../../src/izh_dac_bank.sv \
    ../../src/jesd/dac39j84_sample_remap.v \
    ../../src/jesd/dac39j84_physical_mapper.v \
    ../../src/jesd/dac_channel_source_mux.v \
    ../../src/jesd/dac_source_to_converter_preimage.v \
    ../../src/jesd/litejesd_dac_tx.v \
    ../../src/jesd/daq_litejesd_dac_tx_path.v \
    ../../src/jesd/adc1_sundance_halfbeat.v \
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
