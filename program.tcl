set script_dir [file dirname [file normalize [info script]]]
set project_name DAQ_LAUNCH
set bit_file [file join $script_dir project ${project_name}.runs impl_1 top.bit]
set probes_file [file join $script_dir hw ${project_name}.ltx]

if {![info exists argv]} {
    set argv {}
}

if {[llength $argv] > 0} {
    set bit_file [file normalize [lindex $argv 0]]
}
if {[llength $argv] > 1} {
    set probes_file [file normalize [lindex $argv 1]]
}

proc safe_hw_property {prop obj} {
    if {[catch {get_property $prop $obj} value]} {
        return ""
    }
    return $value
}

proc print_hw_devices {} {
    set devices [get_hw_devices -quiet]
    if {[llength $devices] == 0} {
        puts "  <none>"
        return
    }

    foreach device $devices {
        set name [safe_hw_property NAME $device]
        set part [safe_hw_property PART $device]
        puts "  $device name='$name' part='$part'"
    }
}

proc select_zcu102_device {} {
    set devices [get_hw_devices -quiet]
    if {[llength $devices] == 0} {
        puts "Available hardware devices:"
        print_hw_devices
        error "No hardware devices found. Check JTAG cable, board power, and hw_server target."
    }

    foreach pattern {*xczu9eg* *xczu9* *zu9*} {
        foreach device $devices {
            set haystack "$device [safe_hw_property NAME $device] [safe_hw_property PART $device]"
            if {[string match -nocase $pattern $haystack]} {
                return $device
            }
        }
    }

    if {[llength $devices] == 1} {
        puts "WARNING: no xczu9/zu9 device name matched; using the only hardware device found."
        return [lindex $devices 0]
    }

    puts "Available hardware devices:"
    print_hw_devices
    error "Could not uniquely select the ZCU102 FPGA device."
}

open_hw_manager
connect_hw_server
open_hw_target
set hw_device [select_zcu102_device]
puts "Using hardware device: $hw_device name='[safe_hw_property NAME $hw_device]' part='[safe_hw_property PART $hw_device]'"
current_hw_device $hw_device
refresh_hw_device $hw_device

if {![file exists $bit_file]} {
    error "Bitstream not found: $bit_file"
}

set_property PROGRAM.FILE $bit_file $hw_device

if {[file exists $probes_file]} {
    puts "Using debug probes: $probes_file"
    set_property PROBES.FILE $probes_file $hw_device
    catch {set_property FULL_PROBES.FILE $probes_file $hw_device}
} else {
    puts "WARNING: debug probes file not found: $probes_file"
    puts "WARNING: Hardware Manager will not have ILA probe names unless you load the matching .ltx manually."
}

program_hw_devices $hw_device
close_hw_manager
