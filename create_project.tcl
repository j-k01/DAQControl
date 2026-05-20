set required_vivado "2024.1"
set project_name    "DAQ_LAUNCH"
set part            "xczu9eg-ffvb1156-2-e"
set board_candidates [list \
    "xilinx.com:zcu102:part0:3.4" \
    "xilinx.com:zcu102:part0:3.3" \
    "xilinx.com:zcu102:part0:3.2" \
    "xilinx.com:zcu102:part0:3.1" \
]

set script_dir  [file dirname [file normalize [info script]]]
set project_dir [file join $script_dir project]
set report_dir  [file join $script_dir reports]
set include_staged_gt 0

for {set i 0} {$i < [llength $::argv]} {incr i} {
    set arg [lindex $::argv $i]
    switch -- $arg {
        "--with-staged-gt" {
            set include_staged_gt 1
        }
        default {
            error "Unknown create_project.tcl argument '$arg'. Supported arguments: --with-staged-gt."
        }
    }
}

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

proc require_vivado_version {required} {
    set actual [version -short]
    if {![vivado_version_at_least $actual $required]} {
        error "This project flow targets Vivado $required or newer. Detected Vivado $actual."
    }
    puts "Vivado version OK: $actual"
}

proc set_first_available_board_part {candidates} {
    foreach candidate $candidates {
        set board_parts [get_board_parts -quiet $candidate]
        if {[llength $board_parts] > 0} {
            set selected [lindex $board_parts 0]
            set_property board_part $selected [current_project]
            puts "Using board part: $selected"
            return
        }
    }

    puts "WARNING: No ZCU102 board part from the candidate list is installed."
    puts "WARNING: Continuing with the explicit FPGA part only."
}

proc ip_locked {ip} {
    set locked [get_property IS_LOCKED $ip]
    return [expr {$locked eq "1" || $locked eq "true"}]
}

proc upgrade_and_validate_ips {} {
    set ips [get_ips -quiet]
    if {[llength $ips] == 0} {
        return
    }

    foreach ip $ips {
        puts "Checking IP upgrade state: $ip"
        catch {upgrade_ip $ip} upgrade_result
    }

    foreach ip $ips {
        if {[ip_locked $ip]} {
            error "IP $ip is locked in this Vivado version; open the project with the target Vivado release and upgrade/regenerate the IP."
        }
    }
}

proc validate_ips_unlocked {} {
    foreach ip [get_ips -quiet] {
        if {[ip_locked $ip]} {
            error "IP $ip is locked in this Vivado version; use the target Vivado release or regenerate the tracked XCI."
        }
    }
}

proc upgrade_validate_and_save_bd {bd_file} {
    open_bd_design $bd_file

    set cells [get_bd_cells -quiet -hierarchical *]
    if {[llength $cells] > 0} {
        if {[catch {upgrade_bd_cells $cells} result]} {
            puts "INFO: upgrade_bd_cells reported: $result"
        }
    }

    upgrade_and_validate_ips
    validate_bd_design
    save_bd_design
}

require_vivado_version $required_vivado
if {$include_staged_gt} {
    puts "Build variant: simple bring-up plus staged GTH XCI import."
} else {
    puts "Build variant: simple bring-up only. Staged GTH/JESD files are not imported."
}

file mkdir $project_dir
file mkdir $report_dir
create_project $project_name $project_dir -part $part -force
set_first_available_board_part $board_candidates
set_property target_language Verilog [current_project]
set_property XPM_LIBRARIES {XPM_CDC} [current_project]
set_property ip_repo_paths [list $script_dir/ip_repo] [current_project]
update_ip_catalog

foreach ext {*.v *.sv *.vhd} {
    foreach f [glob -nocomplain -directory $script_dir/src $ext] {
        import_files -fileset sources_1 $f
    }
}

foreach f [glob -nocomplain -directory $script_dir/constraints *.xdc] {
    import_files -fileset constrs_1 $f
}

set ip_dir $project_dir/${project_name}.srcs/sources_1/ip
file mkdir $ip_dir

create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name clk_wiz_0 -dir $ip_dir
set_property -dict [list \
    CONFIG.PRIM_SOURCE                {Differential_clock_capable_pin} \
    CONFIG.PRIM_IN_FREQ               {300.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {200.000} \
    CONFIG.CLKOUT2_USED               {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {100.000} \
    CONFIG.CLKOUT3_USED               {true} \
    CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {125.000} \
    CONFIG.USE_LOCKED                 {true} \
    CONFIG.USE_RESET                  {false} \
] [get_ips clk_wiz_0]

if {$include_staged_gt} {
    foreach xci [glob -nocomplain -directory $script_dir/ip {*/*.xci}] {
        import_ip $xci
    }
}
upgrade_and_validate_ips
validate_ips_unlocked
if {[llength [get_ips -quiet]] > 0} {
    generate_target all [get_ips]
}

foreach bd [glob -nocomplain -directory $script_dir/bd {*/*.bd}] {
    set bd_name [file rootname [file tail $bd]]
    import_files -fileset sources_1 $bd
    set imported_bd [get_files ${bd_name}.bd]
    upgrade_validate_and_save_bd $imported_bd
    generate_target all $imported_bd
    make_wrapper -files $imported_bd -top
    set wrapper $project_dir/${project_name}.gen/sources_1/bd/${bd_name}/hdl/${bd_name}_wrapper.v
    import_files -fileset sources_1 $wrapper
}

report_ip_status -file [file join $report_dir ip_status_after_create.rpt]

puts "Project created: $project_dir/${project_name}.xpr"
