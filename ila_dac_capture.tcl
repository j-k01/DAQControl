# Capture the DAC source/mux/preimage ILA (u_ila_gth_tx_debug) and dump the
# key probes to CSV. Run AFTER the FPGA is programmed and the DAC source is set
# (e.g. BRAM with a tone) over UART.
#
#   probe0 = dac_tx_control_debug   (includes source_modes select bits)
#   probe1 = dac_debug_bram_words   (mux BRAM input = program_word3..0)
#   probe2 = dac_debug_source_words (registered mux output -> preimage = src_converter3..0)
#   probe4 = dac_debug_preimage_words
#   probe8 = dac_debug_jesd_converter_words
#
# Usage: vivado -mode batch -source ila_dac_capture.tcl
set script_dir [file dirname [file normalize [info script]]]
set ltx [file join $script_dir hw DAQ_LAUNCH.ltx]
set out [file join $script_dir captures ila_dac.csv]
file mkdir [file join $script_dir captures]

open_hw_manager
connect_hw_server
open_hw_target
set dev [lindex [get_hw_devices] 0]
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
set_property PROBES.FILE $ltx $dev
set_property FULL_PROBES.FILE $ltx $dev
refresh_hw_device $dev

# Find the DAC tx-debug ILA (the one carrying the source words).
set ila ""
foreach i [get_hw_ilas] {
    if {[llength [get_hw_probes -quiet -of_objects $i *dac_debug_source_words*]] > 0 ||
        [llength [get_hw_probes -quiet -of_objects $i *source_words*]] > 0} {
        set ila $i
        break
    }
}
if {$ila eq ""} { set ila [lindex [get_hw_ilas] 0] }
puts "Using ILA: $ila"

# Trigger immediately: capture whatever is flowing.
set_property CONTROL.TRIGGER_POSITION 0 $ila
set_property CONTROL.CAPTURE_MODE BASIC $ila
set_property CONTROL.TRIGGER_MODE BASIC_ONLY $ila
# match-all trigger
foreach p [get_hw_probes -of_objects $ila] {
    catch {set_property TRIGGER_COMPARE_VALUE eq1'bX $p}
}
run_hw_ila $ila
wait_on_hw_ila -timeout 10 $ila
upload_hw_ila_data $ila

write_hw_ila_data -csv_file $out -force [current_hw_ila_data]
puts "ILA-CSV-WRITTEN: $out"
puts "PROBES:"
foreach p [get_hw_probes -of_objects $ila] { puts "  [get_property NAME $p]" }
