set script_dir [file dirname [file normalize [info script]]]

set bit_file [file join $script_dir project DAQ_LAUNCH.runs impl_1 top.bit]
set elf_file [file join $script_dir sw workspace firmware Debug firmware.elf]
set probes_file [file join $script_dir hw DAQ_LAUNCH.ltx]

if {![info exists argv]} {
    set argv {}
}

if {[llength $argv] > 0} {
    set bit_file [file normalize [lindex $argv 0]]
}
if {[llength $argv] > 1} {
    set elf_file [file normalize [lindex $argv 1]]
}

if {![file exists $bit_file]} {
    error "Bitstream not found: $bit_file\nRun 'vivado.bat -mode batch -source build.tcl' first, or pass a bitstream path."
}

if {![file exists $elf_file] && [llength $argv] <= 1} {
    # Fresh clones have no Vitis workspace; fall back to the tracked prebuilt ELF.
    set prebuilt_elf [file join $script_dir prebuilt firmware.elf]
    if {[file exists $prebuilt_elf]} {
        puts "Workspace ELF not found; using prebuilt MicroBlaze ELF: $prebuilt_elf"
        set elf_file $prebuilt_elf
    }
}

if {![file exists $elf_file]} {
    error "MicroBlaze ELF not found: $elf_file\nRun 'xsct.bat build_sw.tcl' first, or pass an ELF path as the second argument."
}

proc have_vivado_hw_manager {} {
    expr {[llength [info commands open_hw_manager]] > 0}
}

proc have_xsct_debugger {} {
    expr {
        [llength [info commands connect]] > 0 &&
        [llength [info commands targets]] > 0 &&
        [llength [info commands dow]] > 0 &&
        [llength [info commands con]] > 0
    }
}

proc first_existing_command {commands} {
    foreach command $commands {
        set resolved [auto_execok $command]
        if {$resolved ne ""} {
            return $resolved
        }
    }
    return {}
}

proc find_xsct_command {} {
    if {[info exists ::env(XSCT)] && [file exists $::env(XSCT)]} {
        return [list $::env(XSCT)]
    }
    if {[info exists ::env(XILINX_VITIS)]} {
        foreach name {xsct.bat xsct} {
            set candidate [file join $::env(XILINX_VITIS) bin $name]
            if {[file exists $candidate]} {
                return [list $candidate]
            }
        }
    }
    if {[info exists ::env(XILINX_VIVADO)]} {
        set version [file tail $::env(XILINX_VIVADO)]
        set xilinx_root [file dirname [file dirname $::env(XILINX_VIVADO)]]
        foreach name {xsct.bat xsct} {
            set candidate [file join $xilinx_root Vitis $version bin $name]
            if {[file exists $candidate]} {
                return [list $candidate]
            }
        }
        # Vivado-only installs have no Vitis/XSCT; xsdb ships with Vivado and
        # supports everything load_mb_firmware.tcl uses (connect/targets/dow/con).
        foreach name {xsdb.bat xsdb} {
            set candidate [file join $::env(XILINX_VIVADO) bin $name]
            if {[file exists $candidate]} {
                return [list $candidate]
            }
        }
    }

    set resolved [first_existing_command {xsct.bat xsct xsdb.bat xsdb}]
    if {$resolved ne ""} {
        return $resolved
    }

    error "Could not find XSCT or XSDB. Run this from a Xilinx command shell, or set XSCT to the full path of xsct.bat/xsdb.bat."
}

proc find_vivado_command {} {
    if {[info exists ::env(VIVADO)] && [file exists $::env(VIVADO)]} {
        return [list $::env(VIVADO)]
    }
    if {[info exists ::env(XILINX_VIVADO)]} {
        foreach name {vivado.bat vivado} {
            set candidate [file join $::env(XILINX_VIVADO) bin $name]
            if {[file exists $candidate]} {
                return [list $candidate]
            }
        }
    }
    if {[info exists ::env(XILINX_VITIS)]} {
        set version [file tail $::env(XILINX_VITIS)]
        set xilinx_root [file dirname [file dirname $::env(XILINX_VITIS)]]
        foreach name {vivado.bat vivado} {
            set candidate [file join $xilinx_root Vivado $version bin $name]
            if {[file exists $candidate]} {
                return [list $candidate]
            }
        }
    }

    set resolved [first_existing_command {vivado.bat vivado}]
    if {$resolved ne ""} {
        return $resolved
    }

    error "Could not find Vivado. Run this from a Xilinx/Vivado command shell, or set VIVADO to the full path of vivado.bat."
}

proc run_checked {description command} {
    puts $description
    if {[catch {exec -- {*}$command} result options]} {
        if {$result ne ""} {
            puts $result
        }
        return -options $options $result
    }
    if {$result ne ""} {
        puts $result
    }
}

proc program_bitstream {script_dir bit_file probes_file} {
    if {[have_vivado_hw_manager]} {
        puts "Programming FPGA from Vivado Tcl..."
        set had_argv [info exists ::argv]
        if {$had_argv} {
            set saved_argv $::argv
        }
        set ::argv [list $bit_file $probes_file]
        if {[catch {uplevel #0 [list source [file join $script_dir program.tcl]]} result options]} {
            if {$had_argv} {
                set ::argv $saved_argv
            } else {
                unset ::argv
            }
            return -options $options $result
        }
        if {$had_argv} {
            set ::argv $saved_argv
        } else {
            unset ::argv
        }
        return
    }

    set vivado_cmd [find_vivado_command]
    run_checked "Programming FPGA through Vivado subprocess..." \
        [concat $vivado_cmd [list -mode batch -source [file join $script_dir program.tcl] -tclargs $bit_file $probes_file]]
}

proc load_firmware {script_dir elf_file} {
    if {[have_xsct_debugger]} {
        puts "Loading MicroBlaze firmware from XSCT..."
        set had_argv [info exists ::argv]
        if {$had_argv} {
            set saved_argv $::argv
        }
        set ::argv [list $elf_file]
        if {[catch {uplevel #0 [list source [file join $script_dir load_mb_firmware.tcl]]} result options]} {
            if {$had_argv} {
                set ::argv $saved_argv
            } else {
                unset ::argv
            }
            return -options $options $result
        }
        if {$had_argv} {
            set ::argv $saved_argv
        } else {
            unset ::argv
        }
        return
    }

    set xsct_cmd [find_xsct_command]
    run_checked "Loading MicroBlaze firmware through XSCT subprocess..." \
        [concat $xsct_cmd [list [file join $script_dir load_mb_firmware.tcl] $elf_file]]
}

program_bitstream $script_dir $bit_file $probes_file
load_firmware $script_dir $elf_file

puts "FPGA programmed and MicroBlaze firmware loaded."
