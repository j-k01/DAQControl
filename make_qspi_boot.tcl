set script_dir [file dirname [file normalize [info script]]]

proc usage {} {
    puts "Usage: xsct.bat make_qspi_boot.tcl ?options?"
    puts ""
    puts "Creates a ZynqMP BOOT.BIN that configures PL from the baked top.bit."
    puts "The MicroBlaze firmware must already be baked into the bitstream:"
    puts "  xsct.bat build_sw.tcl"
    puts "  vivado.bat -mode batch -source build.tcl -tclargs --bake"
    puts ""
    puts "Options:"
    puts "  --xsa <path>       Hardware XSA. Default: hw/DAQ_LAUNCH.xsa"
    puts "  --bit <path>       Baked PL bitstream. Default: project/DAQ_LAUNCH.runs/impl_1/top.bit"
    puts "  --out <dir>        Output directory. Default: boot/qspi"
    puts "  --ws <dir>         Temporary Vitis workspace. Default: boot/workspace"
    puts "  --fsbl <path>      Existing ZynqMP FSBL ELF to use"
    puts "  --pmufw <path>     Existing ZynqMP PMU firmware ELF to use"
    puts "  --no-build-fw      Do not generate FSBL/PMUFW; require --fsbl and --pmufw"
    puts "  --help             Show this help"
}

proc require_file {label path} {
    if {![file exists $path]} {
        error "$label not found: $path"
    }
}

proc bif_path {path} {
    return [string map {\\ /} [file normalize $path]]
}

proc build_boot_firmware {xsa ws} {
    if {[file exists $ws]} {
        file delete -force $ws
    }
    file mkdir $ws

    setws $ws
    platform create -name boot_platform -hw $xsa -out $ws

    # These processor names are the standard Zynq UltraScale+ MPSoC names used
    # by Vitis for ZCU102-class hardware platforms.
    domain create -name fsbl_domain -os standalone -proc psu_cortexa53_0
    domain create -name pmufw_domain -os standalone -proc psu_pmu_0
    platform generate

    app create -name fsbl -platform boot_platform -domain fsbl_domain -template {Zynq MP FSBL}
    app create -name pmufw -platform boot_platform -domain pmufw_domain -template {ZynqMP PMU Firmware}

    app build -name fsbl
    app build -name pmufw

    set fsbl [file join $ws fsbl Debug fsbl.elf]
    set pmufw [file join $ws pmufw Debug pmufw.elf]
    require_file "Generated FSBL ELF" $fsbl
    require_file "Generated PMUFW ELF" $pmufw

    return [list $fsbl $pmufw]
}

set xsa_file [file join $script_dir hw DAQ_LAUNCH.xsa]
set bit_file [file join $script_dir project DAQ_LAUNCH.runs impl_1 top.bit]
set out_dir [file join $script_dir boot qspi]
set ws_dir [file join $script_dir boot workspace]
set fsbl_file ""
set pmufw_file ""
set build_fw 1

if {![info exists argv]} {
    set argv {}
}

set i 0
while {$i < [llength $argv]} {
    set arg [lindex $argv $i]
    switch -- $arg {
        "--xsa" {
            incr i
            set xsa_file [file normalize [lindex $argv $i]]
        }
        "--bit" {
            incr i
            set bit_file [file normalize [lindex $argv $i]]
        }
        "--out" {
            incr i
            set out_dir [file normalize [lindex $argv $i]]
        }
        "--ws" {
            incr i
            set ws_dir [file normalize [lindex $argv $i]]
        }
        "--fsbl" {
            incr i
            set fsbl_file [file normalize [lindex $argv $i]]
        }
        "--pmufw" {
            incr i
            set pmufw_file [file normalize [lindex $argv $i]]
        }
        "--no-build-fw" {
            set build_fw 0
        }
        "--help" {
            usage
            exit 0
        }
        default {
            usage
            error "Unknown argument '$arg'"
        }
    }
    incr i
}

require_file "XSA" $xsa_file
require_file "Bitstream" $bit_file

if {$fsbl_file eq "" || $pmufw_file eq ""} {
    if {!$build_fw} {
        error "--no-build-fw requires both --fsbl and --pmufw"
    }

    puts "Generating ZynqMP FSBL and PMUFW from: $xsa_file"
    if {[catch {build_boot_firmware $xsa_file $ws_dir} generated options]} {
        puts stderr ""
        puts stderr "Failed to generate FSBL/PMUFW from the XSA."
        puts stderr "This PL/MicroBlaze design may not contain a ZynqMP PS hardware platform."
        puts stderr "If so, create or reuse a ZCU102 FSBL/PMUFW and pass them explicitly:"
        puts stderr "  xsct.bat make_qspi_boot.tcl --fsbl path/to/fsbl.elf --pmufw path/to/pmufw.elf"
        return -options $options $generated
    }
    set fsbl_file [lindex $generated 0]
    set pmufw_file [lindex $generated 1]
}

require_file "FSBL ELF" $fsbl_file
require_file "PMUFW ELF" $pmufw_file

file mkdir $out_dir
set bif_file [file join $out_dir boot_qspi.bif]
set boot_bin [file join $out_dir BOOT.BIN]

set fh [open $bif_file w]
puts $fh "the_ROM_image:"
puts $fh "{"
puts $fh "    \[bootloader, destination_cpu=a53-0\] \"[bif_path $fsbl_file]\""
puts $fh "    \[pmufw_image\] \"[bif_path $pmufw_file]\""
puts $fh "    \[destination_device=pl\] \"[bif_path $bit_file]\""
puts $fh "}"
close $fh

puts "BIF:      $bif_file"
puts "FSBL:     $fsbl_file"
puts "PMUFW:    $pmufw_file"
puts "Bitstream:$bit_file"
puts "BOOT.BIN: $boot_bin"

if {[catch {exec bootgen -arch zynqmp -image $bif_file -w -o $boot_bin} result options]} {
    if {$result ne ""} {
        puts stderr $result
    }
    return -options $options $result
}

require_file "BOOT.BIN" $boot_bin
puts "Created QSPI boot image: $boot_bin"
puts ""
puts "To flash it over JTAG:"
puts "  xsct.bat program_qspi_boot.tcl"
