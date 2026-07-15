set script_dir [file dirname [file normalize [info script]]]

if {![info exists argv]} {
    set argv {}
}

# The normal FPGA-programming path may initialize the PS before loading the
# MicroBlaze.  program_board.{ps1,sh} also reloads the MicroBlaze *after* the
# optional Ethernet/PS bring-up so a failed psu_init cannot leave UART dead.
# That final reload must not run psu_init again or it could disturb the A53 app.
set elf_file [file join $script_dir sw workspace firmware Debug firmware.elf]
set explicit_elf 0
set run_ps_init 1
foreach arg $argv {
    if {$arg eq "--no-ps-init"} {
        set run_ps_init 0
    } elseif {[string match "--*" $arg]} {
        error "Unknown option: $arg\nUsage: xsdb load_mb_firmware.tcl \[firmware.elf\] \[--no-ps-init\]"
    } elseif {$explicit_elf} {
        error "Only one MicroBlaze ELF may be specified."
    } else {
        set elf_file [file normalize $arg]
        set explicit_elf 1
    }
}

if {
    [llength [info commands connect]] == 0 ||
    [llength [info commands targets]] == 0 ||
    [llength [info commands dow]] == 0 ||
    [llength [info commands con]] == 0
} {
    error "load_mb_firmware.tcl must run under XSCT/Vitis Tcl, not Vivado Tcl.\nUse 'xsct.bat load_mb_firmware.tcl', or use 'vivado.bat -mode batch -source program_and_load.tcl' to program the FPGA and then load firmware."
}

if {![file exists $elf_file]} {
    error "MicroBlaze ELF not found: $elf_file\nRun 'xsct.bat build_sw.tcl' first, or pass an ELF path: xsct.bat load_mb_firmware.tcl path/to/firmware.elf \[--no-ps-init\]"
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

proc find_first_named_file {root file_name} {
    if {![file isdirectory $root]} {
        return ""
    }

    foreach candidate [glob -nocomplain -directory $root $file_name] {
        if {[file exists $candidate]} {
            return [file normalize $candidate]
        }
    }

    foreach child [glob -nocomplain -type d -directory $root *] {
        set found [find_first_named_file $child $file_name]
        if {$found ne ""} {
            return $found
        }
    }

    return ""
}

proc run_psu_init_if_available {script_dir} {
    set search_roots [list \
        [file join $script_dir hw] \
        [file join $script_dir sw workspace] \
    ]

    set psu_init_file ""
    foreach root $search_roots {
        set psu_init_file [find_first_named_file $root psu_init.tcl]
        if {$psu_init_file ne ""} {
            break
        }
    }

    if {$psu_init_file eq ""} {
        puts "INFO: no psu_init.tcl found; skipping PS DDR initialization."
        return
    }

    puts "Running PS DDR init: $psu_init_file"
    catch {targets -set -filter {name =~ "*PSU*"}} target_result
    if {[catch {uplevel #0 [list source $psu_init_file]} result]} {
        puts "WARNING: source psu_init.tcl failed: $result"
        return
    }
    foreach proc_name {psu_init psu_ps_pl_isolation_removal psu_ps_pl_reset_config} {
        if {[llength [info commands $proc_name]] > 0} {
            if {[catch {$proc_name} proc_result]} {
                puts "WARNING: $proc_name failed: $proc_result"
            }
        }
    }
}

puts "Connecting to hw_server..."
connect

if {$run_ps_init} {
    run_psu_init_if_available $script_dir
} else {
    puts "Skipping PS init for MicroBlaze-only UART restore."
}

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
