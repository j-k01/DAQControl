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
set include_litejesd 0

for {set i 0} {$i < [llength $::argv]} {incr i} {
    set arg [lindex $::argv $i]
    switch -- $arg {
        "--with-staged-gt" {
            set include_staged_gt 1
        }
        "--with-litejesd" {
            set include_litejesd 1
        }
        default {
            error "Unknown create_project.tcl argument '$arg'. Supported arguments: --with-staged-gt, --with-litejesd."
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

proc safe_connect_bd_net {args} {
    if {[catch {connect_bd_net {*}$args} result]} {
        puts "INFO: connect_bd_net skipped: $result"
    }
}

proc safe_connect_bd_intf_net {args} {
    if {[catch {connect_bd_intf_net {*}$args} result]} {
        puts "INFO: connect_bd_intf_net skipped: $result"
    }
}

proc create_microblaze_bd {bd_name} {
    create_bd_design $bd_name
    current_bd_design $bd_name

    create_bd_port -dir I -type clk Clk
    set_property CONFIG.FREQ_HZ 200000000 [get_bd_ports Clk]

    create_bd_port -dir I -type rst reset
    set_property CONFIG.POLARITY ACTIVE_HIGH [get_bd_ports reset]

    create_bd_intf_port -mode Master -vlnv xilinx.com:interface:uart_rtl:1.0 rs232_uart

    foreach port_name {RW_REG0_0 RW_REG1_0 RW_REG2_0 RW_REG3_0} {
        create_bd_port -dir O -from 31 -to 0 $port_name
    }
    foreach port_name {RO_REG0_IN_0 RO_REG1_IN_0 RO_REG2_IN_0 RO_REG3_IN_0} {
        create_bd_port -dir I -from 31 -to 0 $port_name
    }
    foreach port_name {RO_REG0_WE_0 RO_REG1_WE_0 RO_REG2_WE_0 RO_REG3_WE_0} {
        create_bd_port -dir I $port_name
    }
    foreach port_name {RO_REG0_RDINT_0 RO_REG1_RDINT_0 RO_REG2_RDINT_0 RO_REG3_RDINT_0} {
        create_bd_port -dir O $port_name
    }

    create_bd_cell -type ip -vlnv xilinx.com:ip:microblaze:* microblaze_0
    set_property -dict [list \
        CONFIG.C_DEBUG_ENABLED {1} \
        CONFIG.C_USE_BARREL    {1} \
        CONFIG.C_USE_DIV       {1} \
        CONFIG.C_USE_HW_MUL    {1} \
    ] [get_bd_cells microblaze_0]

    set mb_auto_cfgs [list \
        [list axi_intc {0} axi_periph {Enabled} cache {None} clk {/Clk} debug_module {Debug Only} ecc {None} local_mem {128KB}] \
        [list axi_intc {0} axi_periph {Enabled} cache {None} clk {/Clk (200 MHz)} debug_module {Debug Only} ecc {None} local_mem {128KB}] \
        [list axi_periph {Enabled} cache {None} debug_module {Debug Only} ecc {None} local_mem {128KB}] \
    ]
    set mb_auto_done 0
    set mb_auto_error ""
    foreach mb_auto_cfg $mb_auto_cfgs {
        if {![catch {apply_bd_automation -rule xilinx.com:bd_rule:microblaze -config $mb_auto_cfg [get_bd_cells microblaze_0]} result]} {
            set mb_auto_done 1
            break
        }
        set mb_auto_error $result
    }
    if {!$mb_auto_done} {
        error "MicroBlaze block automation failed: $mb_auto_error"
    }

    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_uart16550:* axi_uart16550_0
    create_bd_cell -type ip -vlnv xilinx.com:user:AXI4_register_file:1.0 AXI4_register_file_0

    if {[llength [get_bd_cells -quiet microblaze_0_axi_periph]] == 0} {
        create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:* microblaze_0_axi_periph
        safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0/M_AXI_DP] [get_bd_intf_pins microblaze_0_axi_periph/S00_AXI]
    }
    set_property CONFIG.NUM_MI 2 [get_bd_cells microblaze_0_axi_periph]

    safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M00_AXI] [get_bd_intf_pins axi_uart16550_0/S_AXI]
    safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M01_AXI] [get_bd_intf_pins AXI4_register_file_0/S00_AXI]
    safe_connect_bd_intf_net [get_bd_intf_pins axi_uart16550_0/UART] [get_bd_intf_ports rs232_uart]

    foreach pin_name {ACLK S00_ACLK M00_ACLK M01_ACLK} {
        safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins microblaze_0_axi_periph/$pin_name]
    }
    foreach pin_name {s_axi_aclk} {
        safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins axi_uart16550_0/$pin_name]
    }
    safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins AXI4_register_file_0/s00_axi_aclk]

    set resetn_pin [get_bd_pins -quiet */peripheral_aresetn]
    if {[llength $resetn_pin] == 0} {
        error "MicroBlaze automation did not create a peripheral_aresetn reset output."
    }
    set resetn_pin [lindex $resetn_pin 0]
    foreach pin_name {ARESETN S00_ARESETN M00_ARESETN M01_ARESETN} {
        safe_connect_bd_net $resetn_pin [get_bd_pins microblaze_0_axi_periph/$pin_name]
    }
    safe_connect_bd_net $resetn_pin [get_bd_pins axi_uart16550_0/s_axi_aresetn]
    safe_connect_bd_net $resetn_pin [get_bd_pins AXI4_register_file_0/s00_axi_aresetn]

    foreach idx {0 1 2 3} {
        safe_connect_bd_net [get_bd_pins AXI4_register_file_0/RW_REG${idx}] [get_bd_ports RW_REG${idx}_0]
        safe_connect_bd_net [get_bd_ports RO_REG${idx}_IN_0] [get_bd_pins AXI4_register_file_0/RO_REG${idx}_IN]
        safe_connect_bd_net [get_bd_ports RO_REG${idx}_WE_0] [get_bd_pins AXI4_register_file_0/RO_REG${idx}_WE]
        safe_connect_bd_net [get_bd_pins AXI4_register_file_0/RO_REG${idx}_RDINT] [get_bd_ports RO_REG${idx}_RDINT_0]
    }

    assign_bd_address
    catch {set_property offset 0x44A00000 [get_bd_addr_segs microblaze_0/Data/SEG_axi_uart16550_0_Reg]}
    catch {set_property range 64K [get_bd_addr_segs microblaze_0/Data/SEG_axi_uart16550_0_Reg]}
    catch {set_property offset 0x44A10000 [get_bd_addr_segs microblaze_0/Data/SEG_AXI4_register_file_0_S00_AXI_reg]}
    catch {set_property range 64K [get_bd_addr_segs microblaze_0/Data/SEG_AXI4_register_file_0_S00_AXI_reg]}

    validate_bd_design
    save_bd_design
}

require_vivado_version $required_vivado
if {$include_staged_gt} {
    puts "Build variant: simple bring-up plus staged GTH XCI import."
} else {
    puts "Build variant: simple bring-up without staged GTH XCI import."
}
if {$include_litejesd} {
    puts "LiteJESD204B generated RTL import enabled."
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

if {$include_litejesd} {
    set litejesd_dir [file join $script_dir src jesd]
    set litejesd_rtl [glob -nocomplain -directory $litejesd_dir *.v]
    if {[llength $litejesd_rtl] == 0} {
        error "LiteJESD import requested, but no generated RTL was found under $litejesd_dir."
    }
    foreach f $litejesd_rtl {
        import_files -fileset sources_1 $f
    }
    foreach f [glob -nocomplain -directory $litejesd_dir *.init] {
        import_files -fileset sources_1 $f
        set imported [get_files -quiet [file tail $f]]
        if {[llength $imported] > 0} {
            set_property file_type {Memory Initialization Files} $imported
        }
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

set bd_name microblaze_bd
create_microblaze_bd $bd_name
set bd_file [get_files ${bd_name}.bd]
generate_target all $bd_file
make_wrapper -files $bd_file -top
set wrapper $project_dir/${project_name}.gen/sources_1/bd/${bd_name}/hdl/${bd_name}_wrapper.v
import_files -fileset sources_1 $wrapper

report_ip_status -file [file join $report_dir ip_status_after_create.rpt]

puts "Project created: $project_dir/${project_name}.xpr"
