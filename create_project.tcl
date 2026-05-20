set project_name  "DAQ_LAUNCH"
set part          "xc7vx690tffg1761-2"
set board         "xilinx.com:vc709:part0:1.8"

set script_dir  [file dirname [file normalize [info script]]]
set project_dir [file join $script_dir project]
set fresh_ip    [expr {[lsearch $::argv "--fresh-ip"] >= 0}]

file mkdir $project_dir
create_project $project_name $project_dir -part $part -force
set_property board_part    $board   [current_project]
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

if {$fresh_ip} {
    set ip_dir $project_dir/${project_name}.srcs/sources_1/ip
    file mkdir $ip_dir

    create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name clk_wiz_0 -dir $ip_dir
    set_property -dict [list \
        CONFIG.PRIM_SOURCE                {Differential_clock_capable_pin} \
        CONFIG.PRIM_IN_FREQ               {200.000} \
        CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {200.000} \
        CONFIG.CLKOUT2_USED               {true} \
        CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {100.000} \
        CONFIG.USE_LOCKED                 {true} \
        CONFIG.USE_RESET                  {false} \
    ] [get_ips clk_wiz_0]

    generate_target all [get_ips]
} else {
    foreach xci [glob -nocomplain -directory $script_dir/ip {*/*.xci}] {
        import_ip $xci
    }
    foreach ip [get_ips -quiet] {
        if {[get_property IS_LOCKED $ip]} { upgrade_ip $ip }
    }
    if {[llength [get_ips -quiet]] > 0} {
        generate_target all [get_ips]
    }
}

foreach bd [glob -nocomplain -directory $script_dir/bd {*/*.bd}] {
    set bd_name [file rootname [file tail $bd]]
    import_files -fileset sources_1 $bd
    generate_target all [get_files ${bd_name}.bd]
    make_wrapper -files [get_files ${bd_name}.bd] -top
    set wrapper $project_dir/${project_name}.gen/sources_1/bd/${bd_name}/hdl/${bd_name}_wrapper.v
    import_files -fileset sources_1 $wrapper
}

puts "Project created: $project_dir/${project_name}.xpr"
