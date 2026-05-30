set script_dir [file dirname [file normalize [info script]]]

set bit_file [file join $script_dir project DAQ_LAUNCH.runs impl_1 top.bit]
set elf_file [file join $script_dir sw workspace firmware Debug firmware.elf]

if {[llength $argv] > 0} {
    set bit_file [file normalize [lindex $argv 0]]
}
if {[llength $argv] > 1} {
    set elf_file [file normalize [lindex $argv 1]]
}

if {![file exists $bit_file]} {
    error "Bitstream not found: $bit_file\nRun 'vivado.bat -mode batch -source build.tcl' first, or pass a bitstream path."
}

if {![file exists $elf_file]} {
    error "MicroBlaze ELF not found: $elf_file\nRun 'xsct.bat build_sw.tcl' first, or pass an ELF path as the second argument."
}

proc select_microblaze_target {} {
    set filters [list \
        {name =~ "*MicroBlaze*"} \
        {name =~ "*microblaze*"} \
        {name =~ "*microblaze_0*"} \
    ]

    foreach filter $filters {
        if {![catch {set matches [targets -filter $filter]} result] && [llength $matches] > 0} {
            set target_id [lindex $matches 0]
            targets $target_id
            return $target_id
        }
    }

    puts "Available JTAG targets:"
    catch {targets}
    error "No MicroBlaze target found after programming the FPGA."
}

puts "Connecting to hw_server..."
connect

puts "Selecting Zynq UltraScale+ device target..."
if {[catch {targets -set -filter {name =~ "*xczu9eg*"}}]} {
    catch {targets -set -filter {name =~ "*PSU*"}}
}

puts "Programming bitstream: $bit_file"
fpga -file $bit_file

after 1000

set target_id [select_microblaze_target]
puts "Selected MicroBlaze target: $target_id"

puts "Stopping processor..."
catch {stop}
catch {rst -processor}

puts "Downloading ELF: $elf_file"
dow $elf_file

puts "Starting processor..."
con

puts "FPGA programmed and MicroBlaze firmware loaded."
