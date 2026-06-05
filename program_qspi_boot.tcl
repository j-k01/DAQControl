set script_dir [file dirname [file normalize [info script]]]

proc usage {} {
    puts "Usage: xsct.bat program_qspi_boot.tcl ?options?"
    puts ""
    puts "Programs boot/qspi/BOOT.BIN into ZCU102 QSPI flash over JTAG."
    puts "Run make_qspi_boot.tcl first."
    puts ""
    puts "Options:"
    puts "  --boot-bin <path>    Boot image. Default: boot/qspi/BOOT.BIN"
    puts "  --fsbl <path>        FSBL used by program_flash. Default: boot/workspace/fsbl/Debug/fsbl.elf"
    puts "  --flash-type <type>  Default: qspi-x8-dual_parallel"
    puts "  --offset <offset>    Flash offset. Default: 0"
    puts "  --no-verify          Skip verify after programming"
    puts "  --blank-check        Run blank check before programming"
    puts "  --help               Show this help"
}

proc require_file {label path} {
    if {![file exists $path]} {
        error "$label not found: $path"
    }
}

proc require_boot_bin {path} {
    if {[file exists $path]} {
        return
    }

    puts stderr "BOOT.BIN not found: $path"
    puts stderr ""
    puts stderr "Create the boot image before programming QSPI:"
    puts stderr "  xsct.bat build_sw.tcl"
    puts stderr "  vivado.bat -mode batch -source build.tcl -tclargs --bake"
    puts stderr "  vivado.bat -mode batch -source create_zcu102_ps_boot_xsa.tcl"
    puts stderr "  xsct.bat make_qspi_boot.tcl"
    puts stderr ""
    puts stderr "If the baked bitstream was built somewhere else, pass it explicitly:"
    puts stderr "  xsct.bat make_qspi_boot.tcl --bit path/to/top.bit"
    puts stderr ""
    puts stderr "If BOOT.BIN already exists at another path, pass it explicitly:"
    puts stderr "  xsct.bat program_qspi_boot.tcl --boot-bin path/to/BOOT.BIN"
    error "BOOT.BIN not found"
}

set boot_bin [file join $script_dir boot qspi BOOT.BIN]
set fsbl_file [file join $script_dir boot workspace fsbl Debug fsbl.elf]
set flash_type "qspi-x8-dual_parallel"
set offset "0"
set verify 1
set blank_check 0

if {![info exists argv]} {
    set argv {}
}

set i 0
while {$i < [llength $argv]} {
    set arg [lindex $argv $i]
    switch -- $arg {
        "--boot-bin" {
            incr i
            set boot_bin [file normalize [lindex $argv $i]]
        }
        "--fsbl" {
            incr i
            set fsbl_file [file normalize [lindex $argv $i]]
        }
        "--flash-type" {
            incr i
            set flash_type [lindex $argv $i]
        }
        "--offset" {
            incr i
            set offset [lindex $argv $i]
        }
        "--no-verify" {
            set verify 0
        }
        "--blank-check" {
            set blank_check 1
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

require_boot_bin $boot_bin
require_file "FSBL ELF" $fsbl_file

puts "Programming QSPI boot image."
puts "BOOT.BIN:   $boot_bin"
puts "FSBL:       $fsbl_file"
puts "Flash type: $flash_type"
puts "Offset:     $offset"
puts ""
puts "This overwrites the boot image at the selected QSPI offset."

set cmd [list program_flash -f $boot_bin -offset $offset -flash_type $flash_type -fsbl $fsbl_file]
if {$blank_check} {
    lappend cmd -blank_check
}
if {$verify} {
    lappend cmd -verify
}

puts "Command: $cmd"
if {[catch {eval $cmd} result options]} {
    if {$result ne ""} {
        puts stderr $result
    }
    return -options $options $result
}

puts "QSPI programming complete."
puts "Set the ZCU102 boot mode switches for QSPI boot, then power-cycle the board."
