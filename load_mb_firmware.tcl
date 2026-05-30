set script_dir [file dirname [file normalize [info script]]]

if {[llength $argv] > 0} {
    set elf_file [file normalize [lindex $argv 0]]
} else {
    set elf_file [file join $script_dir sw workspace firmware Debug firmware.elf]
}

if {![file exists $elf_file]} {
    error "MicroBlaze ELF not found: $elf_file\nRun 'xsct.bat build_sw.tcl' first, or pass an ELF path: xsct.bat load_mb_firmware.tcl path/to/firmware.elf"
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
    error "No MicroBlaze target found. Program the FPGA bitstream first, then rerun this script."
}

puts "Connecting to hw_server..."
connect

set target_id [select_microblaze_target]
puts "Selected MicroBlaze target: $target_id"

puts "Stopping processor..."
catch {stop}
catch {rst -processor}

puts "Downloading ELF: $elf_file"
dow $elf_file

puts "Starting processor..."
con

puts "MicroBlaze firmware loaded and running."
