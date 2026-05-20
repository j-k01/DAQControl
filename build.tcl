set script_dir [file dirname [file normalize [info script]]]
set project_name "DAQ_LAUNCH"
set required_vivado "2024.1"

proc parse_vivado_version {version_string} {
    if {![regexp {^([0-9]+)\.([0-9]+)} $version_string -> major minor]} {
        error "Unable to parse Vivado version string '$version_string'."
    }
    return [list $major $minor]
}

proc vivado_version_at_least {actual required} {
    lassign [parse_vivado_version $actual] actual_major actual_minor
    lassign [parse_vivado_version $required] required_major required_minor

    if {$actual_major > $required_major} {
        return 1
    }
    if {$actual_major == $required_major && $actual_minor >= $required_minor} {
        return 1
    }
    return 0
}

proc ip_locked {ip} {
    set locked [get_property IS_LOCKED $ip]
    return [expr {$locked eq "1" || $locked eq "true"}]
}

proc validate_ips_unlocked {} {
    foreach ip [get_ips -quiet] {
        if {[ip_locked $ip]} {
            error "IP $ip is locked. Re-run create_project.tcl with the target Vivado release before building."
        }
    }
}

set actual_vivado [version -short]
if {![vivado_version_at_least $actual_vivado $required_vivado]} {
    error "This build flow targets Vivado $required_vivado or newer. Detected Vivado $actual_vivado."
}

open_project $script_dir/project/${project_name}.xpr

set bake_elf 0
set jobs 4
for {set i 0} {$i < [llength $::argv]} {incr i} {
    set arg [lindex $::argv $i]
    switch -- $arg {
        "--bake" {
            set bake_elf 1
        }
        "--jobs" {
            incr i
            if {$i >= [llength $::argv]} {
                error "--jobs requires an integer argument."
            }
            set jobs [lindex $::argv $i]
        }
        default {
            error "Unknown build.tcl argument '$arg'. Supported arguments: --bake --jobs <n>."
        }
    }
}

validate_ips_unlocked

if {$bake_elf} {
    set elf_file $script_dir/sw/workspace/firmware/Debug/firmware.elf
    if {![file exists $elf_file]} {
        error "Firmware ELF not found: $elf_file. Run build_sw.tcl after exporting the XSA."
    }
    if {[llength [get_files -quiet firmware.elf]] == 0} {
        add_files $elf_file
    }
    set_property SCOPED_TO_REF microblaze_bd [get_files firmware.elf]
    set_property SCOPED_TO_CELLS microblaze_0 [get_files firmware.elf]
}

reset_run synth_1
launch_runs synth_1 -jobs $jobs
wait_on_run synth_1

launch_runs impl_1 -jobs $jobs
wait_on_run impl_1

launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1

file mkdir $script_dir/hw
open_run impl_1
write_hw_platform -fixed -include_bit -force $script_dir/hw/${project_name}.xsa

puts "Bitstream: $script_dir/project/${project_name}.runs/impl_1/top.bit"
puts "Hardware:  $script_dir/hw/${project_name}.xsa"

close_project
