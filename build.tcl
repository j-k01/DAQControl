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

proc validate_clock_contract {} {
    set expected_clk_hz 200000000
    set bd_files [get_files -quiet microblaze_bd.bd]

    if {[llength $bd_files] == 0} {
        error "Clock contract failure: microblaze_bd.bd was not found in the project. Re-run create_project.tcl."
    }

    open_bd_design [lindex $bd_files 0]

    set bd_clk_hz [get_property CONFIG.FREQ_HZ [get_bd_ports Clk]]
    if {$bd_clk_hz ne "$expected_clk_hz"} {
        error "Clock contract failure: MicroBlaze BD Clk is $bd_clk_hz Hz, expected $expected_clk_hz Hz."
    }

    set uart_clk_hz [get_property CONFIG.C_S_AXI_ACLK_FREQ_HZ [get_bd_cells axi_uart16550_0]]
    if {$uart_clk_hz ne "$expected_clk_hz"} {
        error "Clock contract failure: AXI UART16550 clock is $uart_clk_hz Hz, expected $expected_clk_hz Hz."
    }
}

proc validate_source_contract {script_dir} {
    set expected_top [file normalize [file join $script_dir src top.v]]
    set project_top_files [get_files -quiet top.v]

    if {[llength $project_top_files] == 0} {
        error "Source contract failure: top.v is not in the project. Re-run create_project.tcl."
    }

    set project_top [file normalize [lindex $project_top_files 0]]
    if {$project_top ne $expected_top} {
        error "Source contract failure: project top.v is '$project_top', but repo top.v is '$expected_top'. Re-run create_project.tcl so Vivado does not build a stale imported source copy."
    }
}

proc copy_debug_probes {script_dir project_name} {
    set impl_dir [file join $script_dir project ${project_name}.runs impl_1]
    set hw_dir [file join $script_dir hw]
    file mkdir $hw_dir

    set fabric_ila_cells [get_cells -hier -quiet *u_ila_fabric_debug*]
    if {[llength $fabric_ila_cells] == 0} {
        error "Debug contract failure: implemented design does not contain u_ila_fabric_debug. Re-run create_project.tcl and build the regenerated project."
    }

    set gth_tx_ila_cells [get_cells -hier -quiet *u_ila_gth_tx_debug*]
    if {[llength $gth_tx_ila_cells] == 0} {
        puts "WARNING: implemented design does not contain u_ila_gth_tx_debug. This is expected only for non-LiteJESD builds."
    }

    set ltx_files [glob -nocomplain -directory $impl_dir *.ltx]
    if {[llength $ltx_files] == 0} {
        puts "WARNING: no debug probes .ltx file found in $impl_dir."
        return
    }

    set ltx_file [lindex $ltx_files 0]
    set stable_ltx [file join $hw_dir ${project_name}.ltx]
    file copy -force $ltx_file $stable_ltx
    puts "Debug probes: $stable_ltx"
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
validate_source_contract $script_dir
validate_clock_contract

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
copy_debug_probes $script_dir $project_name
write_hw_platform -fixed -include_bit -force $script_dir/hw/${project_name}.xsa

puts "Bitstream: $script_dir/project/${project_name}.runs/impl_1/top.bit"
puts "Hardware:  $script_dir/hw/${project_name}.xsa"

close_project
