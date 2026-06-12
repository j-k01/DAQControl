set required_vivado "2023.1"
set project_name "zcu102_ps_boot"
set design_name "zcu102_ps_boot_bd"
set part "xczu9eg-ffvb1156-2-e"
set board_candidates [list \
    "xilinx.com:zcu102:part0:3.4" \
    "xilinx.com:zcu102:part0:3.3" \
    "xilinx.com:zcu102:part0:3.2" \
    "xilinx.com:zcu102:part0:3.1" \
]

set script_dir [file dirname [file normalize [info script]]]
set out_dir [file join $script_dir boot zcu102_ps]
set project_dir [file join $out_dir project]
set xsa_file [file join $out_dir zcu102_ps_boot.xsa]

proc usage {} {
    puts "Usage: vivado.bat -mode batch -source create_zcu102_ps_boot_xsa.tcl ?-tclargs options?"
    puts ""
    puts "Creates a minimal ZCU102 ZynqMP PS hardware handoff for FSBL/PMUFW generation."
    puts "The exported XSA is not the DAQ PL design; make_qspi_boot.tcl adds the DAQ bitstream separately."
    puts ""
    puts "Options:"
    puts "  --out <path>        Output XSA. Default: boot/zcu102_ps/zcu102_ps_boot.xsa"
    puts "  --project <dir>     Temporary Vivado project directory. Default: boot/zcu102_ps/project"
    puts "  --help              Show this help"
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
        error "This boot-XSA flow targets Vivado $required or newer. Detected Vivado $actual."
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

    error "No ZCU102 board part from the candidate list is installed. Install the ZCU102 board files or pass known-good FSBL/PMUFW ELFs to make_qspi_boot.tcl."
}

for {set i 0} {$i < [llength $::argv]} {incr i} {
    set arg [lindex $::argv $i]
    switch -- $arg {
        "--out" {
            incr i
            if {$i >= [llength $::argv]} {
                error "--out requires a path argument."
            }
            set xsa_file [file normalize [lindex $::argv $i]]
            set out_dir [file dirname $xsa_file]
        }
        "--project" {
            incr i
            if {$i >= [llength $::argv]} {
                error "--project requires a directory argument."
            }
            set project_dir [file normalize [lindex $::argv $i]]
        }
        "--help" {
            usage
            exit 0
        }
        default {
            usage
            error "Unknown create_zcu102_ps_boot_xsa.tcl argument '$arg'."
        }
    }
}

require_vivado_version $required_vivado

file mkdir $out_dir
file mkdir $project_dir

create_project -force $project_name $project_dir -part $part
set_first_available_board_part $board_candidates

create_bd_design $design_name
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} $ps

# Rev 1.1-era ZCU102 kits (0432055-05 onward) ship a 1Rx16 SODIMM
# (MTA4ATF51264HZ-2G6E1); the board preset still encodes the original x8
# module and drives a bank-group bit the x16 module lacks, aliasing DDR
# 16 KB apart (Xilinx AR 71961, verified with ddr_alias_probe.tcl). The
# SPD-reading FSBL self-corrects at boot, but keep the static config
# truthful for psu_init/JTAG flows.
set_property -dict [list \
    CONFIG.PSU__DDRC__DRAM_WIDTH {16 Bits} \
    CONFIG.PSU__DDRC__BG_ADDR_COUNT {1} \
    CONFIG.PSU__DDRC__DEVICE_CAPACITY {8192 MBits} \
    CONFIG.PSU__DDRC__ROW_ADDR_COUNT {16} \
] $ps

set pl_clk0 [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]
foreach hpm_clk [get_bd_pins -quiet zynq_ultra_ps_e_0/maxihpm*_fpd_aclk] {
    if {[llength [get_bd_nets -quiet -of_objects $hpm_clk]] == 0} {
        connect_bd_net $pl_clk0 $hpm_clk
    }
}

validate_bd_design
save_bd_design

set bd_file [get_files -quiet ${design_name}.bd]
if {[llength $bd_file] == 0} {
    error "Could not find generated block design file for $design_name."
}

generate_target all $bd_file
make_wrapper -files $bd_file -top
set wrapper_file [file join $project_dir ${project_name}.gen sources_1 bd $design_name hdl ${design_name}_wrapper.v]
if {![file exists $wrapper_file]} {
    error "Could not find generated block design wrapper: $wrapper_file"
}
add_files -norecurse $wrapper_file
update_compile_order -fileset sources_1

write_hw_platform -fixed -force -file $xsa_file
puts "Created ZCU102 PS boot XSA: $xsa_file"
puts ""
puts "Next:"
puts "  xsct.bat make_qspi_boot.tcl --xsa $xsa_file"

close_project
