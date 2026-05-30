set script_dir [file dirname [file normalize [info script]]]
set project_name DAQ_LAUNCH
set bit_file [file join $script_dir project ${project_name}.runs impl_1 top.bit]
set probes_file [file join $script_dir hw ${project_name}.ltx]

open_hw_manager
connect_hw_server
open_hw_target
current_hw_device [lindex [get_hw_devices xczu9eg_0] 0]
refresh_hw_device [current_hw_device]

if {![file exists $bit_file]} {
    error "Bitstream not found: $bit_file"
}

set_property PROGRAM.FILE $bit_file [current_hw_device]

if {[file exists $probes_file]} {
    puts "Using debug probes: $probes_file"
    set_property PROBES.FILE $probes_file [current_hw_device]
    catch {set_property FULL_PROBES.FILE $probes_file [current_hw_device]}
} else {
    puts "WARNING: debug probes file not found: $probes_file"
    puts "WARNING: Hardware Manager will not have ILA probe names unless you load the matching .ltx manually."
}

program_hw_devices [current_hw_device]
close_hw_manager
