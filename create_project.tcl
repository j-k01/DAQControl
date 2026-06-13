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
set include_gth_tx_ila 0
set include_bram_dataplane 0
set include_ps_ddr_dma 0
set izh_neuron_file_override ""

for {set i 0} {$i < [llength $::argv]} {incr i} {
    set arg [lindex $::argv $i]
    switch -- $arg {
        "--with-staged-gt" {
            set include_staged_gt 1
        }
        "--with-litejesd" {
            set include_litejesd 1
        }
        "--with-gth-tx-ila" {
            set include_gth_tx_ila 1
        }
        "--with-bram-dataplane" {
            set include_bram_dataplane 1
        }
        "--with-ps-ddr-dma" {
            set include_ps_ddr_dma 1
        }
        "--izh-neuron-file" {
            incr i
            if {$i >= [llength $::argv]} {
                error "--izh-neuron-file requires a path argument."
            }
            set izh_neuron_file_override [lindex $::argv $i]
        }
        default {
            error "Unknown create_project.tcl argument '$arg'. Supported arguments: --with-staged-gt, --with-litejesd, --with-gth-tx-ila, --with-bram-dataplane, --with-ps-ddr-dma, --izh-neuron-file <path>."
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

proc close_project_if_open {} {
    if {[catch {current_project} project] == 0 && $project ne ""} {
        close_project
    }
}

proc refresh_axi_register_file_ip_metadata {script_dir} {
    set ip_dir [file join $script_dir ip_repo AXI4_register_file_1_0]
    set component_xml [file join $ip_dir component.xml]
    if {![file exists $component_xml]} {
        error "AXI4_register_file component.xml not found at $component_xml"
    }

    set edit_dir [file join $script_dir project .ipx_refresh_AXI4_register_file]
    file delete -force $edit_dir

    puts "Refreshing AXI4_register_file IP metadata from HDL via Vivado IP packager..."
    ipx::edit_ip_in_project -upgrade true -name AXI4_register_file_refresh \
        -directory $edit_dir $component_xml

    set core [ipx::current_core]
    ipx::merge_project_changes ports $core
    ipx::merge_project_changes hdl_parameters $core

    ipx::update_checksums $core
    ipx::check_integrity $core
    ipx::save_core $core
    close_project_if_open
    file delete -force $edit_dir
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

proc export_bram_ctrl_port {pin_path port_name} {
    set pin [get_bd_intf_pins -quiet $pin_path]
    if {[llength $pin] != 1} {
        error "BRAM export failure: expected one pin '$pin_path', found [llength $pin]."
    }

    set before_ports [get_bd_intf_ports -quiet]
    make_bd_intf_pins_external $pin
    set ext_port {}
    foreach candidate [get_bd_intf_ports -quiet] {
        if {[lsearch -exact $before_ports $candidate] < 0} {
            lappend ext_port $candidate
        }
    }
    if {[llength $ext_port] != 1} {
        error "BRAM export failure: expected one new external interface for '$pin_path', found [llength $ext_port]."
    }

    set_property name $port_name [lindex $ext_port 0]
    set_property -dict [list \
        CONFIG.MASTER_TYPE {BRAM_CTRL} \
        CONFIG.READ_WRITE_MODE {READ_WRITE} \
    ] [get_bd_intf_ports $port_name]
}

proc assign_mb_addr_exact {space_path seg_path offset range} {
    set space [get_bd_addr_spaces -quiet $space_path]
    if {[llength $space] != 1} {
        error "Address assignment failure: expected one address space '$space_path', found [llength $space]."
    }

    set seg [get_bd_addr_segs -quiet $seg_path]
    if {[llength $seg] != 1} {
        error "Address assignment failure: expected one address segment '$seg_path', found [llength $seg]."
    }

    assign_bd_address -offset $offset -range $range \
        -target_address_space [lindex $space 0] [lindex $seg 0] -force
    puts "Assigned $seg_path at $offset range $range"
}

proc assign_bd_addr_if_exists {space_path seg_path offset range} {
    set space [get_bd_addr_spaces -quiet $space_path]
    set seg [get_bd_addr_segs -quiet $seg_path]
    if {[llength $space] != 1 || [llength $seg] != 1} {
        puts "INFO: address assignment skipped for '$space_path' -> '$seg_path' (space [llength $space], segment [llength $seg])."
        return
    }

    if {[catch {
        assign_bd_address -offset $offset -range $range \
            -target_address_space [lindex $space 0] [lindex $seg 0] -force
    } result]} {
        puts "INFO: address assignment skipped for '$space_path' -> '$seg_path': $result"
    } else {
        puts "Assigned $seg_path at $offset range $range"
    }
}

proc exclude_bd_addr_seg_if_exists {space_path seg_path {offset ""} {range ""}} {
    set space [get_bd_addr_spaces -quiet $space_path]
    set seg [get_bd_addr_segs -quiet $seg_path]
    if {[llength $space] != 1 || [llength $seg] != 1} {
        puts "INFO: address exclusion skipped for '$space_path' -> '$seg_path' (space [llength $space], segment [llength $seg])."
        return
    }

    set args [list -target_address_space [lindex $space 0]]
    if {$offset ne ""} {
        lappend args -offset $offset
    }
    if {$range ne ""} {
        lappend args -range $range
    }
    lappend args [lindex $seg 0]

    if {[catch {exclude_bd_addr_seg {*}$args} result]} {
        puts "INFO: address exclusion skipped for '$space_path' -> '$seg_path': $result"
    }
}

proc set_cell_properties_if_present {cell_name prop_values} {
    set cell [get_bd_cells -quiet $cell_name]
    if {[llength $cell] != 1} {
        puts "INFO: property assignment skipped for missing cell '$cell_name'."
        return
    }

    foreach {prop value} $prop_values {
        if {[catch {set_property $prop $value $cell} result]} {
            puts "INFO: property $prop on $cell_name skipped: $result"
        }
    }
}

proc create_axis_s2mm_port {port_name freq_hz} {
    create_bd_intf_port -mode Slave -vlnv xilinx.com:interface:axis_rtl:1.0 $port_name
    set_property -dict [list \
        CONFIG.FREQ_HZ $freq_hz \
        CONFIG.CLK_DOMAIN {gt_rx_usrclk_2} \
        CONFIG.HAS_TKEEP {1} \
        CONFIG.HAS_TLAST {1} \
        CONFIG.HAS_TREADY {1} \
        CONFIG.HAS_TSTRB {0} \
        CONFIG.LAYERED_METADATA {undef} \
        CONFIG.TDATA_NUM_BYTES {16} \
        CONFIG.TDEST_WIDTH {0} \
        CONFIG.TID_WIDTH {0} \
        CONFIG.TUSER_WIDTH {0} \
    ] [get_bd_intf_ports $port_name]
}

proc require_cell_property {cell prop expected} {
    set actual [get_property $prop [get_bd_cells $cell]]
    if {$actual ne "$expected"} {
        error "BD property contract failure: $cell $prop is '$actual', expected '$expected'."
    }
    puts "Verified BD cell $cell $prop = $actual"
}

proc require_ip_property {ip prop expected} {
    set actual [get_property $prop [get_ips $ip]]
    if {$actual ne "$expected"} {
        error "IP property contract failure: $ip $prop is '$actual', expected '$expected'."
    }
    puts "Verified IP $ip $prop = $actual"
}

proc verify_bram_dataplane_ips {} {
    foreach ch {0 1 2 3} {
        set ip dac${ch}_program_bram
        require_ip_property $ip CONFIG.Write_Width_A 32
        require_ip_property $ip CONFIG.Read_Width_A 32
        require_ip_property $ip CONFIG.Write_Width_B 64
        require_ip_property $ip CONFIG.Read_Width_B 64
        require_ip_property $ip CONFIG.Write_Depth_A 8192
        require_ip_property $ip CONFIG.Use_Byte_Write_Enable true
        require_ip_property $ip CONFIG.Register_PortA_Output_of_Memory_Primitives false
        require_ip_property $ip CONFIG.Register_PortA_Output_of_Memory_Core false
        require_ip_property $ip CONFIG.Register_PortB_Output_of_Memory_Primitives false
        require_ip_property $ip CONFIG.Register_PortB_Output_of_Memory_Core false
    }

    foreach chip {0 1} {
        set ip adc${chip}_capture_bram
        require_ip_property $ip CONFIG.Write_Width_A 32
        require_ip_property $ip CONFIG.Read_Width_A 32
        require_ip_property $ip CONFIG.Write_Width_B 128
        require_ip_property $ip CONFIG.Read_Width_B 128
        require_ip_property $ip CONFIG.Write_Depth_A 16384
        require_ip_property $ip CONFIG.Use_Byte_Write_Enable true
        require_ip_property $ip CONFIG.Register_PortA_Output_of_Memory_Primitives false
        require_ip_property $ip CONFIG.Register_PortA_Output_of_Memory_Core false
        require_ip_property $ip CONFIG.Register_PortB_Output_of_Memory_Primitives false
        require_ip_property $ip CONFIG.Register_PortB_Output_of_Memory_Core false
    }
}

proc create_microblaze_bd {bd_name include_bram_dataplane include_ps_ddr_dma} {
    set fabric_clk_hz 200000000
    set adc_dma_clk_hz 250000000

    create_bd_design $bd_name
    current_bd_design $bd_name

    create_bd_port -dir I -type clk Clk
    set_property CONFIG.FREQ_HZ $fabric_clk_hz [get_bd_ports Clk]

    create_bd_port -dir I -type rst reset
    set_property CONFIG.POLARITY ACTIVE_HIGH [get_bd_ports reset]

    if {$include_ps_ddr_dma} {
        create_bd_port -dir I -type clk gt_rx_usrclk_2
        set_property -dict [list \
            CONFIG.FREQ_HZ $adc_dma_clk_hz \
            CONFIG.CLK_DOMAIN {gt_rx_usrclk_2} \
            CONFIG.ASSOCIATED_BUSIF {S_AXIS_S2MM_0:S_AXIS_S2MM_1} \
        ] [get_bd_ports gt_rx_usrclk_2]

        create_bd_port -dir I -type rst reset_rtl
        set_property CONFIG.POLARITY ACTIVE_HIGH [get_bd_ports reset_rtl]

        create_axis_s2mm_port S_AXIS_S2MM_0 $adc_dma_clk_hz
        create_axis_s2mm_port S_AXIS_S2MM_1 $adc_dma_clk_hz
    }

    create_bd_intf_port -mode Master -vlnv xilinx.com:interface:uart_rtl:1.0 rs232_uart

    foreach port_name {RW_REG0_0 RW_REG1_0 RW_REG2_0 RW_REG3_0 RW_REG4_0 RW_REG5_0 RW_REG6_0 RW_REG7_0} {
        create_bd_port -dir O -from 31 -to 0 $port_name
    }
    foreach port_name {RO_REG0_IN_0 RO_REG1_IN_0 RO_REG2_IN_0 RO_REG3_IN_0 RO_REG4_IN_0 RO_REG5_IN_0 RO_REG6_IN_0 RO_REG7_IN_0} {
        create_bd_port -dir I -from 31 -to 0 $port_name
    }
    foreach port_name {RO_REG0_WE_0 RO_REG1_WE_0 RO_REG2_WE_0 RO_REG3_WE_0 RO_REG4_WE_0 RO_REG5_WE_0 RO_REG6_WE_0 RO_REG7_WE_0} {
        create_bd_port -dir I $port_name
    }
    foreach port_name {RO_REG0_RDINT_0 RO_REG1_RDINT_0 RO_REG2_RDINT_0 RO_REG3_RDINT_0 RO_REG4_RDINT_0 RO_REG5_RDINT_0 RO_REG6_RDINT_0 RO_REG7_RDINT_0} {
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

    foreach dbg_cell [get_bd_cells -quiet -hierarchical mdm_*] {
        catch {set_property CONFIG.C_S_AXI_ACLK_FREQ_HZ $fabric_clk_hz $dbg_cell}
    }

    set ext_reset_pins [get_bd_pins -quiet rst_Clk_200M/ext_reset_in]
    if {[llength $ext_reset_pins] == 0} {
        set ext_reset_pins [get_bd_pins -quiet -hierarchical */ext_reset_in]
    }
    if {[llength $ext_reset_pins] != 1} {
        error "MicroBlaze automation did not create exactly one proc_sys_reset ext_reset_in pin."
    }
    safe_connect_bd_net [get_bd_ports reset] [lindex $ext_reset_pins 0]

    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_uart16550:* axi_uart16550_0
    set_property CONFIG.C_S_AXI_ACLK_FREQ_HZ $fabric_clk_hz [get_bd_cells axi_uart16550_0]
    create_bd_cell -type ip -vlnv xilinx.com:user:AXI4_register_file:1.0 AXI4_register_file_0
    if {$include_bram_dataplane} {
        foreach ch {0 1 2 3} {
            create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:* dac${ch}_program_bram_ctrl
        }
        create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:* adc0_capture_bram_ctrl
        create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:* adc1_capture_bram_ctrl
        create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:* neuron_cfg_bram_ctrl
        foreach bram_ctrl {dac0_program_bram_ctrl dac1_program_bram_ctrl dac2_program_bram_ctrl dac3_program_bram_ctrl} {
            set_property -dict [list \
                CONFIG.SINGLE_PORT_BRAM {1} \
                CONFIG.PROTOCOL {AXI4} \
                CONFIG.DATA_WIDTH {32} \
                CONFIG.SUPPORTS_NARROW_BURST {1} \
            ] [get_bd_cells $bram_ctrl]
        }
        foreach bram_ctrl {adc0_capture_bram_ctrl adc1_capture_bram_ctrl neuron_cfg_bram_ctrl} {
            set_property -dict [list \
                CONFIG.SINGLE_PORT_BRAM {1} \
                CONFIG.PROTOCOL {AXI4} \
                CONFIG.DATA_WIDTH {32} \
                CONFIG.SUPPORTS_NARROW_BURST {1} \
            ] [get_bd_cells $bram_ctrl]
        }
        foreach ch {0 1 2 3} {
            export_bram_ctrl_port dac${ch}_program_bram_ctrl/BRAM_PORTA DAC${ch}_AXI_BRAM_PORTA
        }
        export_bram_ctrl_port adc0_capture_bram_ctrl/BRAM_PORTA ADC0_AXI_BRAM_PORTA
        export_bram_ctrl_port adc1_capture_bram_ctrl/BRAM_PORTA ADC1_AXI_BRAM_PORTA
        export_bram_ctrl_port neuron_cfg_bram_ctrl/BRAM_PORTA NEURON_CFG_AXI_BRAM_PORTA
    }
    if {$include_ps_ddr_dma} {
        create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0
        if {[catch {
            apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e \
                -config [list apply_board_preset {1}] [get_bd_cells zynq_ultra_ps_e_0]
        } result]} {
            puts "INFO: zynq_ultra_ps_e board automation skipped: $result"
        }

        set_cell_properties_if_present zynq_ultra_ps_e_0 [list \
            CONFIG.PSU__USE__M_AXI_GP0 {0} \
            CONFIG.PSU__USE__M_AXI_GP1 {0} \
            CONFIG.PSU__USE__M_AXI_GP2 {0} \
            CONFIG.PSU__USE__S_AXI_GP0 {0} \
            CONFIG.PSU__USE__S_AXI_GP2 {1} \
            CONFIG.PSU__USE__S_AXI_GP3 {1} \
            CONFIG.PSU__USE__S_AXI_GP4 {1} \
            CONFIG.PSU__SAXIGP2__DATA_WIDTH {128} \
            CONFIG.PSU__SAXIGP3__DATA_WIDTH {128} \
            CONFIG.PSU__SAXIGP4__DATA_WIDTH {32} \
            CONFIG.PSU__ENET3__PERIPHERAL__ENABLE {1} \
            CONFIG.PSU__ENET3__PERIPHERAL__IO {MIO 64 .. 75} \
            CONFIG.PSU__ENET3__GRP_MDIO__ENABLE {1} \
            CONFIG.PSU__ENET3__GRP_MDIO__IO {MIO 76 .. 77} \
            CONFIG.PSU__DDRC__DRAM_WIDTH {16 Bits} \
            CONFIG.PSU__DDRC__BG_ADDR_COUNT {1} \
            CONFIG.PSU__DDRC__DEVICE_CAPACITY {8192 MBits} \
            CONFIG.PSU__DDRC__ROW_ADDR_COUNT {16} \
        ]
        # This board's DDR4 SODIMM uses x16 devices (2 bank groups), not the
        # x8/4-bank-group module the stock ZCU102 preset assumes. With the
        # preset config the controller drives a bank-group bit the module
        # lacks, aliasing DDR addresses 16 KB apart (verified with
        # ddr_alias_probe.tcl) and corrupting every A53 ELF download.

        foreach dma {0 1} {
            create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:* axi_dma_${dma}
            # Scatter-gather (cyclic-capable) so continuous streaming has no
            # re-arm gaps. Status/control stream off; 26-bit lengths so one
            # descriptor can hold a full 128 KB ring chunk.
            set_property -dict [list \
                CONFIG.c_addr_width {32} \
                CONFIG.c_include_mm2s {0} \
                CONFIG.c_include_sg {1} \
                CONFIG.c_sg_include_stscntrl_strm {0} \
                CONFIG.c_m_axi_s2mm_data_width {128} \
                CONFIG.c_s_axis_s2mm_tdata_width {128} \
                CONFIG.c_sg_length_width {26} \
            ] [get_bd_cells axi_dma_${dma}]

            create_bd_cell -type ip -vlnv xilinx.com:ip:axi_clock_converter:* axi_clock_converter_${dma}
        }

        create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_gt_rx_usrclk2
        safe_connect_bd_intf_net [get_bd_intf_ports S_AXIS_S2MM_0] [get_bd_intf_pins axi_dma_0/S_AXIS_S2MM]
        safe_connect_bd_intf_net [get_bd_intf_ports S_AXIS_S2MM_1] [get_bd_intf_pins axi_dma_1/S_AXIS_S2MM]
    }

    if {[llength [get_bd_cells -quiet microblaze_0_axi_periph]] == 0} {
        create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:* microblaze_0_axi_periph
        safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0/M_AXI_DP] [get_bd_intf_pins microblaze_0_axi_periph/S00_AXI]
    }
    set axi_masters 2
    if {$include_bram_dataplane} {
        set axi_masters 8
    }
    if {$include_ps_ddr_dma} {
        set axi_masters 11
    }
    # The neuron config-bank controller takes the next free MI after whatever
    # the DAC/ADC BRAMs (M02-M07) and the optional DDR-DMA block (M08-M10) use,
    # so its index stays stable whether or not DDR-DMA is built.
    set neuron_cfg_mi -1
    if {$include_bram_dataplane} {
        set neuron_cfg_mi $axi_masters
        incr axi_masters
    }
    set_property CONFIG.NUM_MI $axi_masters [get_bd_cells microblaze_0_axi_periph]

    safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M00_AXI] [get_bd_intf_pins axi_uart16550_0/S_AXI]
    safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M01_AXI] [get_bd_intf_pins AXI4_register_file_0/S00_AXI]
    safe_connect_bd_intf_net [get_bd_intf_pins axi_uart16550_0/UART] [get_bd_intf_ports rs232_uart]
    if {$include_bram_dataplane} {
        foreach ch {0 1 2 3} mi {M02_AXI M03_AXI M04_AXI M05_AXI} {
            safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/$mi] [get_bd_intf_pins dac${ch}_program_bram_ctrl/S_AXI]
        }
        safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M06_AXI] [get_bd_intf_pins adc0_capture_bram_ctrl/S_AXI]
        safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M07_AXI] [get_bd_intf_pins adc1_capture_bram_ctrl/S_AXI]
        set neuron_cfg_mi_name [format "M%02d_AXI" $neuron_cfg_mi]
        safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/$neuron_cfg_mi_name] [get_bd_intf_pins neuron_cfg_bram_ctrl/S_AXI]
    }
    if {$include_ps_ddr_dma} {
        safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M08_AXI] [get_bd_intf_pins axi_clock_converter_0/S_AXI]
        safe_connect_bd_intf_net [get_bd_intf_pins axi_clock_converter_0/M_AXI] [get_bd_intf_pins axi_dma_0/S_AXI_LITE]
        safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M09_AXI] [get_bd_intf_pins axi_clock_converter_1/S_AXI]
        safe_connect_bd_intf_net [get_bd_intf_pins axi_clock_converter_1/M_AXI] [get_bd_intf_pins axi_dma_1/S_AXI_LITE]
        # SmartConnect per DMA merges M_AXI_S2MM + M_AXI_SG onto one HP port.
        foreach dma {0 1} {
            if {[llength [get_bd_cells -quiet axi_smc_dma${dma}]] == 0} {
                create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* axi_smc_dma${dma}
                set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1} CONFIG.NUM_CLKS {1}] \
                    [get_bd_cells axi_smc_dma${dma}]
            }
            safe_connect_bd_intf_net [get_bd_intf_pins axi_dma_${dma}/M_AXI_S2MM] [get_bd_intf_pins axi_smc_dma${dma}/S00_AXI]
            safe_connect_bd_intf_net [get_bd_intf_pins axi_dma_${dma}/M_AXI_SG] [get_bd_intf_pins axi_smc_dma${dma}/S01_AXI]
        }
        safe_connect_bd_intf_net [get_bd_intf_pins axi_smc_dma0/M00_AXI] [get_bd_intf_pins zynq_ultra_ps_e_0/S_AXI_HP0_FPD]
        safe_connect_bd_intf_net [get_bd_intf_pins axi_smc_dma1/M00_AXI] [get_bd_intf_pins zynq_ultra_ps_e_0/S_AXI_HP1_FPD]
        safe_connect_bd_intf_net [get_bd_intf_pins microblaze_0_axi_periph/M10_AXI] [get_bd_intf_pins zynq_ultra_ps_e_0/S_AXI_HP2_FPD]
    }

    set axi_clk_pins {ACLK S00_ACLK M00_ACLK M01_ACLK}
    if {$include_bram_dataplane} {
        lappend axi_clk_pins M02_ACLK M03_ACLK M04_ACLK M05_ACLK M06_ACLK M07_ACLK
    }
    if {$include_ps_ddr_dma} {
        lappend axi_clk_pins M08_ACLK M09_ACLK M10_ACLK
    }
    if {$include_bram_dataplane} {
        lappend axi_clk_pins [format "M%02d_ACLK" $neuron_cfg_mi]
    }
    foreach pin_name $axi_clk_pins {
        safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins microblaze_0_axi_periph/$pin_name]
    }
    foreach pin_name {s_axi_aclk} {
        safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins axi_uart16550_0/$pin_name]
    }
    safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins AXI4_register_file_0/s00_axi_aclk]
    if {$include_bram_dataplane} {
        foreach ch {0 1 2 3} {
            safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins dac${ch}_program_bram_ctrl/s_axi_aclk]
        }
        safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins adc0_capture_bram_ctrl/s_axi_aclk]
        safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins adc1_capture_bram_ctrl/s_axi_aclk]
        safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins neuron_cfg_bram_ctrl/s_axi_aclk]
    }
    if {$include_ps_ddr_dma} {
        foreach dma {0 1} {
            safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins axi_clock_converter_${dma}/s_axi_aclk]
            safe_connect_bd_net [get_bd_ports gt_rx_usrclk_2] [get_bd_pins axi_clock_converter_${dma}/m_axi_aclk]
            safe_connect_bd_net [get_bd_ports gt_rx_usrclk_2] [get_bd_pins axi_dma_${dma}/s_axi_lite_aclk]
            safe_connect_bd_net [get_bd_ports gt_rx_usrclk_2] [get_bd_pins axi_dma_${dma}/m_axi_s2mm_aclk]
            safe_connect_bd_net [get_bd_ports gt_rx_usrclk_2] [get_bd_pins axi_dma_${dma}/m_axi_sg_aclk]
            safe_connect_bd_net [get_bd_ports gt_rx_usrclk_2] [get_bd_pins axi_smc_dma${dma}/aclk]
        }
        safe_connect_bd_net [get_bd_ports gt_rx_usrclk_2] [get_bd_pins zynq_ultra_ps_e_0/saxihp0_fpd_aclk]
        safe_connect_bd_net [get_bd_ports gt_rx_usrclk_2] [get_bd_pins zynq_ultra_ps_e_0/saxihp1_fpd_aclk]
        safe_connect_bd_net [get_bd_ports Clk] [get_bd_pins zynq_ultra_ps_e_0/saxihp2_fpd_aclk]
        safe_connect_bd_net [get_bd_ports gt_rx_usrclk_2] [get_bd_pins rst_gt_rx_usrclk2/slowest_sync_clk]
    }

    set resetn_pin [get_bd_pins -quiet rst_Clk_200M/peripheral_aresetn]
    if {[llength $resetn_pin] == 0} {
        set resetn_pin [get_bd_pins -quiet */peripheral_aresetn]
    }
    if {[llength $resetn_pin] == 0} {
        error "MicroBlaze automation did not create a 200 MHz peripheral_aresetn reset output."
    }
    set resetn_pin [lindex $resetn_pin 0]
    set axi_rst_pins {ARESETN S00_ARESETN M00_ARESETN M01_ARESETN}
    if {$include_bram_dataplane} {
        lappend axi_rst_pins M02_ARESETN M03_ARESETN M04_ARESETN M05_ARESETN M06_ARESETN M07_ARESETN
    }
    if {$include_ps_ddr_dma} {
        lappend axi_rst_pins M08_ARESETN M09_ARESETN M10_ARESETN
    }
    if {$include_bram_dataplane} {
        lappend axi_rst_pins [format "M%02d_ARESETN" $neuron_cfg_mi]
    }
    foreach pin_name $axi_rst_pins {
        safe_connect_bd_net $resetn_pin [get_bd_pins microblaze_0_axi_periph/$pin_name]
    }
    safe_connect_bd_net $resetn_pin [get_bd_pins axi_uart16550_0/s_axi_aresetn]
    safe_connect_bd_net $resetn_pin [get_bd_pins AXI4_register_file_0/s00_axi_aresetn]
    if {$include_bram_dataplane} {
        foreach ch {0 1 2 3} {
            safe_connect_bd_net $resetn_pin [get_bd_pins dac${ch}_program_bram_ctrl/s_axi_aresetn]
        }
        safe_connect_bd_net $resetn_pin [get_bd_pins adc0_capture_bram_ctrl/s_axi_aresetn]
        safe_connect_bd_net $resetn_pin [get_bd_pins adc1_capture_bram_ctrl/s_axi_aresetn]
        safe_connect_bd_net $resetn_pin [get_bd_pins neuron_cfg_bram_ctrl/s_axi_aresetn]
    }
    if {$include_ps_ddr_dma} {
        safe_connect_bd_net [get_bd_ports reset_rtl] [get_bd_pins rst_gt_rx_usrclk2/ext_reset_in]
        set gt_resetn_pin [get_bd_pins -quiet rst_gt_rx_usrclk2/peripheral_aresetn]
        if {[llength $gt_resetn_pin] != 1} {
            error "PS DDR DMA reset failure: rst_gt_rx_usrclk2 did not expose one peripheral_aresetn pin."
        }
        foreach dma {0 1} {
            safe_connect_bd_net $resetn_pin [get_bd_pins axi_clock_converter_${dma}/s_axi_aresetn]
            safe_connect_bd_net [lindex $gt_resetn_pin 0] [get_bd_pins axi_clock_converter_${dma}/m_axi_aresetn]
            safe_connect_bd_net [lindex $gt_resetn_pin 0] [get_bd_pins axi_dma_${dma}/axi_resetn]
            safe_connect_bd_net [lindex $gt_resetn_pin 0] [get_bd_pins axi_smc_dma${dma}/aresetn]
        }
    }

    foreach idx {0 1 2 3 4 5 6 7} {
        safe_connect_bd_net [get_bd_pins AXI4_register_file_0/RW_REG${idx}] [get_bd_ports RW_REG${idx}_0]
        safe_connect_bd_net [get_bd_ports RO_REG${idx}_IN_0] [get_bd_pins AXI4_register_file_0/RO_REG${idx}_IN]
        safe_connect_bd_net [get_bd_ports RO_REG${idx}_WE_0] [get_bd_pins AXI4_register_file_0/RO_REG${idx}_WE]
        safe_connect_bd_net [get_bd_pins AXI4_register_file_0/RO_REG${idx}_RDINT] [get_bd_ports RO_REG${idx}_RDINT_0]
    }

    assign_mb_addr_exact microblaze_0/Data axi_uart16550_0/S_AXI/Reg 0x44A00000 0x00010000
    assign_mb_addr_exact microblaze_0/Data AXI4_register_file_0/S00_AXI/S00_AXI_reg 0x44A10000 0x00010000
    if {$include_bram_dataplane} {
        assign_mb_addr_exact microblaze_0/Data dac0_program_bram_ctrl/S_AXI/Mem0 0xC0000000 0x00008000
        assign_mb_addr_exact microblaze_0/Data dac1_program_bram_ctrl/S_AXI/Mem0 0xC0010000 0x00008000
        assign_mb_addr_exact microblaze_0/Data dac2_program_bram_ctrl/S_AXI/Mem0 0xC0020000 0x00008000
        assign_mb_addr_exact microblaze_0/Data dac3_program_bram_ctrl/S_AXI/Mem0 0xC0030000 0x00008000
        assign_mb_addr_exact microblaze_0/Data adc0_capture_bram_ctrl/S_AXI/Mem0 0xC0100000 0x00010000
        assign_mb_addr_exact microblaze_0/Data adc1_capture_bram_ctrl/S_AXI/Mem0 0xC0110000 0x00010000
        assign_mb_addr_exact microblaze_0/Data neuron_cfg_bram_ctrl/S_AXI/Mem0 0xC0040000 0x00008000
    }
    if {$include_ps_ddr_dma} {
        assign_mb_addr_exact microblaze_0/Data axi_dma_0/S_AXI_LITE/Reg 0x41E00000 0x00010000
        assign_mb_addr_exact microblaze_0/Data axi_dma_1/S_AXI_LITE/Reg 0x41E10000 0x00010000
        assign_bd_addr_if_exists axi_dma_0/Data_S2MM zynq_ultra_ps_e_0/SAXIGP2/HP0_DDR_LOW 0x00000000 0x80000000
        assign_bd_addr_if_exists axi_dma_1/Data_S2MM zynq_ultra_ps_e_0/SAXIGP3/HP1_DDR_LOW 0x00000000 0x80000000
        # MicroBlaze only needs a small DDR readback aperture over the DMA
        # capture buffers. Mapping all DDR_LOW at zero collides with local
        # memory and every existing peripheral in the MB address space.
        assign_bd_addr_if_exists microblaze_0/Data zynq_ultra_ps_e_0/SAXIGP4/HP2_DDR_LOW 0x10000000 0x00040000

        exclude_bd_addr_seg_if_exists axi_dma_0/Data_S2MM zynq_ultra_ps_e_0/SAXIGP2/HP0_DDR_HIGH
        exclude_bd_addr_seg_if_exists axi_dma_0/Data_S2MM zynq_ultra_ps_e_0/SAXIGP2/HP0_LPS_OCM 0xFF000000 0x01000000
        exclude_bd_addr_seg_if_exists axi_dma_0/Data_S2MM zynq_ultra_ps_e_0/SAXIGP2/HP0_PCIE_LOW 0xE0000000 0x10000000
        exclude_bd_addr_seg_if_exists axi_dma_0/Data_S2MM zynq_ultra_ps_e_0/SAXIGP2/HP0_QSPI 0xC0000000 0x20000000
        exclude_bd_addr_seg_if_exists axi_dma_1/Data_S2MM zynq_ultra_ps_e_0/SAXIGP3/HP1_DDR_HIGH 0x000800000000 0x000800000000
        exclude_bd_addr_seg_if_exists axi_dma_1/Data_S2MM zynq_ultra_ps_e_0/SAXIGP3/HP1_LPS_OCM 0xFF000000 0x01000000
        exclude_bd_addr_seg_if_exists axi_dma_1/Data_S2MM zynq_ultra_ps_e_0/SAXIGP3/HP1_PCIE_LOW 0xE0000000 0x10000000
        exclude_bd_addr_seg_if_exists axi_dma_1/Data_S2MM zynq_ultra_ps_e_0/SAXIGP3/HP1_QSPI 0xC0000000 0x20000000

        # SG descriptor-fetch masters need the same DDR view as the data side
        # (descriptor rings live in the MicroBlaze DDR window at 0x1003xxxx).
        assign_bd_addr_if_exists axi_dma_0/Data_SG zynq_ultra_ps_e_0/SAXIGP2/HP0_DDR_LOW 0x00000000 0x80000000
        assign_bd_addr_if_exists axi_dma_1/Data_SG zynq_ultra_ps_e_0/SAXIGP3/HP1_DDR_LOW 0x00000000 0x80000000
        exclude_bd_addr_seg_if_exists axi_dma_0/Data_SG zynq_ultra_ps_e_0/SAXIGP2/HP0_DDR_HIGH
        exclude_bd_addr_seg_if_exists axi_dma_0/Data_SG zynq_ultra_ps_e_0/SAXIGP2/HP0_LPS_OCM 0xFF000000 0x01000000
        exclude_bd_addr_seg_if_exists axi_dma_0/Data_SG zynq_ultra_ps_e_0/SAXIGP2/HP0_PCIE_LOW 0xE0000000 0x10000000
        exclude_bd_addr_seg_if_exists axi_dma_0/Data_SG zynq_ultra_ps_e_0/SAXIGP2/HP0_QSPI 0xC0000000 0x20000000
        exclude_bd_addr_seg_if_exists axi_dma_1/Data_SG zynq_ultra_ps_e_0/SAXIGP3/HP1_DDR_HIGH 0x000800000000 0x000800000000
        exclude_bd_addr_seg_if_exists axi_dma_1/Data_SG zynq_ultra_ps_e_0/SAXIGP3/HP1_LPS_OCM 0xFF000000 0x01000000
        exclude_bd_addr_seg_if_exists axi_dma_1/Data_SG zynq_ultra_ps_e_0/SAXIGP3/HP1_PCIE_LOW 0xE0000000 0x10000000
        exclude_bd_addr_seg_if_exists axi_dma_1/Data_SG zynq_ultra_ps_e_0/SAXIGP3/HP1_QSPI 0xC0000000 0x20000000
    }

    validate_bd_design

    set bd_clk_hz [get_property CONFIG.FREQ_HZ [get_bd_ports Clk]]
    if {$bd_clk_hz ne "$fabric_clk_hz"} {
        error "Clock contract failure: MicroBlaze BD Clk is $bd_clk_hz Hz, expected $fabric_clk_hz Hz."
    }

    set uart_clk_hz [get_property CONFIG.C_S_AXI_ACLK_FREQ_HZ [get_bd_cells axi_uart16550_0]]
    if {$uart_clk_hz ne "$fabric_clk_hz"} {
        error "Clock contract failure: AXI UART16550 clock is $uart_clk_hz Hz, expected $fabric_clk_hz Hz."
    }

    if {$include_bram_dataplane} {
        foreach bram_ctrl {dac0_program_bram_ctrl dac1_program_bram_ctrl dac2_program_bram_ctrl dac3_program_bram_ctrl adc0_capture_bram_ctrl adc1_capture_bram_ctrl} {
            require_cell_property $bram_ctrl CONFIG.DATA_WIDTH 32
            require_cell_property $bram_ctrl CONFIG.SINGLE_PORT_BRAM 1
        }
    }
    if {$include_ps_ddr_dma} {
        foreach dma {0 1} {
            require_cell_property axi_dma_${dma} CONFIG.c_include_mm2s 0
            require_cell_property axi_dma_${dma} CONFIG.c_include_sg 1
            require_cell_property axi_dma_${dma} CONFIG.c_sg_length_width 26
            require_cell_property axi_dma_${dma} CONFIG.c_m_axi_s2mm_data_width 128
            require_cell_property axi_dma_${dma} CONFIG.c_s_axis_s2mm_tdata_width 128
            require_cell_property axi_dma_${dma} CONFIG.c_addr_width 32
        }
    }

    save_bd_design
}

require_vivado_version $required_vivado
if {$include_staged_gt} {
    set include_litejesd 1
}
if {$include_ps_ddr_dma} {
    set include_bram_dataplane 1
    set include_staged_gt 1
    set include_litejesd 1
}
if {$include_bram_dataplane} {
    set include_staged_gt 1
    set include_litejesd 1
}
if {$include_staged_gt} {
    puts "Build variant: staged GTH plus LiteJESD startup triangle."
} else {
    puts "Build variant: simple bring-up without staged GTH XCI import."
}
if {$include_litejesd} {
    if {!$include_staged_gt} {
        error "--with-litejesd requires --with-staged-gt so the JESD TX block has a GTH PHY to drive."
    }
    puts "LiteJESD204B generated RTL import enabled."
}

file mkdir $project_dir
file mkdir $report_dir
create_project $project_name $project_dir -part $part -force
refresh_axi_register_file_ip_metadata $script_dir
close_project_if_open
open_project [file join $project_dir ${project_name}.xpr]
set_first_available_board_part $board_candidates
set_property target_language Verilog [current_project]
set_property XPM_LIBRARIES {XPM_CDC XPM_FIFO XPM_MEMORY} [current_project]
set verilog_defines {}
if {$include_staged_gt} {
    lappend verilog_defines DAQ_WITH_GTH=1
}
if {$include_litejesd} {
    lappend verilog_defines DAQ_WITH_LITEJESD=1
}
if {$include_gth_tx_ila} {
    if {!$include_litejesd} {
        error "--with-gth-tx-ila requires --with-staged-gt/--with-litejesd so the TX ILA has a GTH TX user clock."
    }
    lappend verilog_defines DAQ_WITH_GTH_TX_ILA=1
}
if {$include_bram_dataplane} {
    lappend verilog_defines DAQ_WITH_BRAM_DATAPLANE=1
}
if {$include_ps_ddr_dma} {
    lappend verilog_defines DAQ_WITH_PS_DDR_DMA=1
}
if {[llength $verilog_defines] > 0} {
    set_property verilog_define $verilog_defines [current_fileset]
}
set_property ip_repo_paths [list $script_dir/ip_repo] [current_project]
update_ip_catalog

foreach ext {*.v *.sv *.vhd} {
    foreach f [glob -nocomplain -directory $script_dir/src $ext] {
        add_files -fileset sources_1 -norecurse $f
    }
}

source [file join $script_dir scripts resolve_izh_neuron.tcl]
set izh_neuron_file [resolve_izh_neuron_file $script_dir $izh_neuron_file_override]
add_files -fileset sources_1 -norecurse $izh_neuron_file

if {$include_litejesd} {
    set litejesd_dir [file join $script_dir src jesd]
    set litejesd_rtl [glob -nocomplain -directory $litejesd_dir *.v]
    if {[llength $litejesd_rtl] == 0} {
        error "LiteJESD import requested, but no generated RTL was found under $litejesd_dir."
    }
    foreach f $litejesd_rtl {
        add_files -fileset sources_1 -norecurse $f
    }
    foreach f [glob -nocomplain -directory $litejesd_dir *.init] {
        add_files -fileset sources_1 -norecurse $f
        set imported [get_files -quiet [file tail $f]]
        if {[llength $imported] > 0} {
            set_property file_type {Memory Initialization Files} $imported
        }
    }
}

foreach f [glob -nocomplain -directory $script_dir/constraints *.xdc] {
    add_files -fileset constrs_1 -norecurse $f
}

# Unmanaged Tcl constraints: managed .xdc files reject proc/if (Designutils
# 20-1307), which silently disabled every constraint in the old
# debug_cdc.xdc.  Run the .tcl constraints LATE so the GT/IP generated clocks
# they reference already exist.
foreach f [glob -nocomplain -directory $script_dir/constraints *.tcl] {
    add_files -fileset constrs_1 -norecurse $f
    set f_obj [get_files -of_objects [get_filesets constrs_1] $f]
    set_property USED_IN {synthesis implementation} $f_obj
    set_property PROCESSING_ORDER LATE $f_obj
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
    CONFIG.CLKOUT4_USED               {true} \
    CONFIG.CLKOUT4_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.USE_LOCKED                 {true} \
    CONFIG.USE_RESET                  {false} \
] [get_ips clk_wiz_0]

create_ip -name ila -vendor xilinx.com -library ip -module_name ila_fabric_debug -dir $ip_dir
set fabric_ila_props [list \
    CONFIG.C_DATA_DEPTH    {2048} \
    CONFIG.C_NUM_OF_PROBES {24} \
]
set fabric_ila_widths [list 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32 32]
for {set i 0} {$i < [llength $fabric_ila_widths]} {incr i} {
    lappend fabric_ila_props CONFIG.C_PROBE${i}_WIDTH [lindex $fabric_ila_widths $i]
}
set_property -dict $fabric_ila_props [get_ips ila_fabric_debug]

if {$include_gth_tx_ila} {
    create_ip -name ila -vendor xilinx.com -library ip -module_name ila_gth_tx_debug -dir $ip_dir
    set gth_tx_ila_props [list \
        CONFIG.C_DATA_DEPTH    {2048} \
        CONFIG.C_NUM_OF_PROBES {16} \
    ]
    set gth_tx_ila_widths [list \
        64 \
        256 256 256 256 256 256 256 256 256 256 \
        32 32 32 32 32 \
    ]
    for {set i 0} {$i < [llength $gth_tx_ila_widths]} {incr i} {
        lappend gth_tx_ila_props CONFIG.C_PROBE${i}_WIDTH [lindex $gth_tx_ila_widths $i]
    }
    set_property -dict $gth_tx_ila_props [get_ips ila_gth_tx_debug]
}

if {$include_bram_dataplane} {
    foreach ch {0 1 2 3} {
        create_ip -name blk_mem_gen -vendor xilinx.com -library ip -module_name dac${ch}_program_bram -dir $ip_dir
        set_property -dict [list \
            CONFIG.Memory_Type            {True_Dual_Port_RAM} \
            CONFIG.Interface_Type         {Native} \
            CONFIG.Use_Byte_Write_Enable  {true} \
            CONFIG.Byte_Size              {8} \
            CONFIG.Write_Width_A          {32} \
            CONFIG.Read_Width_A           {32} \
            CONFIG.Write_Width_B          {64} \
            CONFIG.Read_Width_B           {64} \
            CONFIG.Write_Depth_A          {8192} \
            CONFIG.Assume_Synchronous_Clk {false} \
            CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
            CONFIG.Register_PortA_Output_of_Memory_Core {false} \
            CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
            CONFIG.Register_PortB_Output_of_Memory_Core {false} \
        ] [get_ips dac${ch}_program_bram]
    }

    foreach chip {0 1} {
        create_ip -name blk_mem_gen -vendor xilinx.com -library ip -module_name adc${chip}_capture_bram -dir $ip_dir
        set_property -dict [list \
            CONFIG.Memory_Type            {True_Dual_Port_RAM} \
            CONFIG.Interface_Type         {Native} \
            CONFIG.Use_Byte_Write_Enable  {true} \
            CONFIG.Byte_Size              {8} \
            CONFIG.Write_Width_A          {32} \
            CONFIG.Read_Width_A           {32} \
            CONFIG.Write_Width_B          {128} \
            CONFIG.Read_Width_B           {128} \
            CONFIG.Write_Depth_A          {16384} \
            CONFIG.Assume_Synchronous_Clk {false} \
            CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
            CONFIG.Register_PortA_Output_of_Memory_Core {false} \
            CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
            CONFIG.Register_PortB_Output_of_Memory_Core {false} \
        ] [get_ips adc${chip}_capture_bram]
    }
}

if {$include_staged_gt} {
    foreach xci [glob -nocomplain -directory $script_dir/ip_repo {*/*.xci}] {
        import_ip $xci
    }
}
upgrade_and_validate_ips
validate_ips_unlocked
if {[llength [get_ips -quiet]] > 0} {
    generate_target all [get_ips]
}
if {$include_bram_dataplane} {
    verify_bram_dataplane_ips
}

set bd_name microblaze_bd
create_microblaze_bd $bd_name $include_bram_dataplane $include_ps_ddr_dma
set bd_file [get_files ${bd_name}.bd]
generate_target all $bd_file
make_wrapper -files $bd_file -top
set wrapper $project_dir/${project_name}.gen/sources_1/bd/${bd_name}/hdl/${bd_name}_wrapper.v
import_files -fileset sources_1 $wrapper

report_ip_status -file [file join $report_dir ip_status_after_create.rpt]

puts "Project created: $project_dir/${project_name}.xpr"
