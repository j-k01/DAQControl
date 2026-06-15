set script_dir [file dirname [file normalize [info script]]]

if {![info exists argv]} {
    set argv {}
}

set elf_file [file join $script_dir sw ps_eth_workspace ps_eth_stream Debug ps_eth_stream.elf]
set run_ps_init 0
set explicit_elf 0

foreach arg $argv {
    if {$arg eq "--init-ps"} {
        set run_ps_init 1
    } else {
        set elf_file [file normalize $arg]
        set explicit_elf 1
    }
}

if {![file exists $elf_file] && !$explicit_elf} {
    # Vivado-only / fresh-clone PCs have no Vitis workspace; fall back to the
    # tracked prebuilt A53 ELF (mirrors program_and_load.tcl's MB fallback).
    set prebuilt_elf [file join $script_dir prebuilt ps_eth_stream.elf]
    if {[file exists $prebuilt_elf]} {
        puts "Workspace ELF not found; using prebuilt A53 ELF: $prebuilt_elf"
        set elf_file $prebuilt_elf
    }
}

if {![file exists $elf_file]} {
    error "PS Ethernet ELF not found: $elf_file\nRun 'xsct build_ps_eth_stream.tcl' first, or pass the ELF path."
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

proc run_psu_init_if_requested {script_dir run_ps_init} {
    if {!$run_ps_init} {
        puts "Skipping PS init. Use --init-ps only when the PS/DDR was not already initialized."
        return
    }

    set psu_init_file ""
    foreach root [list [file join $script_dir hw] [file join $script_dir sw workspace] [file join $script_dir sw ps_eth_workspace]] {
        set psu_init_file [find_first_named_file $root psu_init.tcl]
        if {$psu_init_file ne ""} {
            break
        }
    }

    if {$psu_init_file eq ""} {
        puts "INFO: no psu_init.tcl found; skipping PS init."
        return
    }

    puts "Running PS init: $psu_init_file"
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

proc select_a53_0_target {} {
    puts "Available JTAG targets:"
    catch {targets}

    set filters [list \
        {name =~ "Cortex-A53 #0"} \
        {name =~ "*Cortex-A53 #0*"} \
        {name =~ "*A53*#0*"} \
    ]

    foreach filter $filters {
        if {![catch {targets -set -filter $filter} result]} {
            puts "Selected A53 target with filter: $filter"
            return $filter
        }
    }

    error "No Cortex-A53 #0 target found."
}

puts "Connecting to hw_server..."
connect

run_psu_init_if_requested $script_dir $run_ps_init

set target_id [select_a53_0_target]
puts "Selected A53 target: $target_id"

puts "Stopping A53..."
catch {stop} stop_result

puts "Resetting A53..."
catch {rst -processor -clear-registers} rst_result

puts "Downloading ELF: $elf_file"
dow $elf_file

puts "Starting PS Ethernet streamer..."
con

puts "PS Ethernet streamer loaded and running."
