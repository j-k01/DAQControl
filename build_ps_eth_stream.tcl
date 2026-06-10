set script_dir [file dirname [file normalize [info script]]]

set ws $script_dir/sw/ps_eth_workspace
if {[file exists $ws]} {
    file delete -force $ws
}
setws $ws

set xsa_file $script_dir/hw/DAQ_LAUNCH.xsa
if {![file exists $xsa_file]} {
    error "Hardware XSA not found: $xsa_file\nRun the Vivado build first."
}

platform create -name ps_eth_platform -hw $xsa_file -out $ws
domain create -name ps_lwip -os standalone -proc psu_cortexa53_0

# lwIP library name in Vitis 2024.1 is lwip220. Keep this explicit so a
# missing BSP library fails during build instead of producing a silent no-net app.
bsp setlib -name lwip220
catch {bsp config api_mode RAW_API}
catch {bsp config dhcp_does_arp_check false}
catch {bsp config lwip_dhcp false}
catch {bsp config mem_size 262144}
platform generate

app create -name ps_eth_stream -platform ps_eth_platform -domain ps_lwip -template {Empty Application(C)}
importsources -name ps_eth_stream -path $script_dir/sw/ps_eth_stream/src

# The generated linker script can map psu_ddr_0 from address 0x0. XSCT then
# tries to download the A53 ELF at 0x0, which is fragile and can fail depending
# on the PS/debug state. Keep this app in a known DDR window below the ADC DMA
# buffers at 0x10000000 and 0x10020000.
set lscript_file [file join $ws ps_eth_stream src lscript.ld]
if {[file exists $lscript_file]} {
    set fd [open $lscript_file r]
    set lscript [read $fd]
    close $fd

    set lscript [string map [list \
        "psu_ddr_0_MEM_0 : ORIGIN = 0x0, LENGTH = 0x7FF00000" \
        "psu_ddr_0_MEM_0 : ORIGIN = 0x01000000, LENGTH = 0x0E000000" \
    ] $lscript]

    set fd [open $lscript_file w]
    puts -nonewline $fd $lscript
    close $fd
} else {
    error "Generated linker script not found: $lscript_file"
}

app build -name ps_eth_stream

puts "ELF: $script_dir/sw/ps_eth_workspace/ps_eth_stream/Debug/ps_eth_stream.elf"
