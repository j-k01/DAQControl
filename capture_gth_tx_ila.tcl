# Headless capture of the GTH-TX debug ILA (u_ila_gth_tx_debug).
# Assumes the device is already programmed with a --with-gth-tx-ila bitstream and
# the board state (NSRC/CURP) was set over UART first.  Triggers immediately and
# writes all samples x probes to a CSV for offline parsing.
#
# Run:  ~/bin/with_xilinx_2024_1 vivado -mode batch -source capture_gth_tx_ila.tcl

set script_dir [file dirname [file normalize [info script]]]
set csv_out [file join $script_dir ila_gth_tx.csv]

open_hw_manager
connect_hw_server -url localhost:3121
open_hw_target
set dev [lindex [get_hw_devices] 0]
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev

set ltx [glob -nocomplain [file join $script_dir hw *.ltx]]
if {[llength $ltx] > 0} {
    set_property PROBES.FILE      [lindex $ltx 0] $dev
    set_property FULL_PROBES.FILE [lindex $ltx 0] $dev
    refresh_hw_device $dev
    puts "Loaded probes: [lindex $ltx 0]"
}

# pick the GTH-TX ILA (the one whose cell name mentions gth_tx); else the last
set ila ""
foreach i [get_hw_ilas] {
    puts "found ILA: [get_property CELL_NAME $i]"
    if {[string match -nocase *gth_tx* [get_property CELL_NAME $i]]} { set ila $i }
}
if {$ila eq ""} { set ila [lindex [get_hw_ilas] end] }
puts "Using ILA: [get_property CELL_NAME $ila]"

# capture the whole window immediately (state is static), no trigger condition
catch { set_property CONTROL.TRIGGER_POSITION 0 $ila }
run_hw_ila -trigger_now $ila
wait_on_hw_ila $ila
set data [upload_hw_ila_data $ila]
write_hw_ila_data -csv_file -force $csv_out $data
puts "WROTE $csv_out"
close_hw_manager
