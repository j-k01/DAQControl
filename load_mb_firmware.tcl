set script_dir [file dirname [file normalize [info script]]]

if {![info exists argv]} {
    set argv {}
}

if {
    [llength [info commands connect]] == 0 ||
    [llength [info commands targets]] == 0 ||
    [llength [info commands dow]] == 0 ||
    [llength [info commands con]] == 0
} {
    error "load_mb_firmware.tcl must run under XSCT/Vitis Tcl, not Vivado Tcl.\nUse 'xsct.bat load_mb_firmware.tcl', or use 'vivado.bat -mode batch -source program_and_load.tcl' to program the FPGA and then load firmware."
}

if {[llength $argv] > 0} {
    set elf_file [file normalize [lindex $argv 0]]
} else {
    set elf_file [file join $script_dir sw workspace firmware Debug firmware.elf]
}

if {![file exists $elf_file]} {
    error "MicroBlaze ELF not found: $elf_file\nRun 'xsct.bat build_sw.tcl' first, or pass an ELF path: xsct.bat load_mb_firmware.tcl path/to/firmware.elf"
}

proc select_microblaze_target {} {
    puts "Available JTAG targets:"
    catch {targets}

    set filters [list \
        {name =~ "*microblaze*"} \
        {name =~ "*microblaze_0*"} \
        {name =~ "MicroBlaze #*"} \
        {name =~ "*MicroBlaze #*"} \
        {name =~ "*MicroBlaze*"} \
    ]

    foreach filter $filters {
        if {![catch {targets -set -filter $filter} result]} {
            puts "Selected MicroBlaze target with filter: $filter"
            return $filter
        }
    }

    error "No MicroBlaze target found. Program the FPGA bitstream first, then rerun this script."
}

puts "Connecting to hw_server..."
connect

set target_id [select_microblaze_target]
puts "Selected MicroBlaze target: $target_id"

puts "Stopping processor..."
if {[catch {stop} stop_result]} {
    puts "WARNING: stop failed: $stop_result"
}

puts "Resetting processor..."
rst -processor

puts "Downloading ELF: $elf_file"
dow $elf_file

puts "Starting processor..."
con

puts "Targets after start:"
catch {targets}

puts "MicroBlaze firmware loaded and running."
