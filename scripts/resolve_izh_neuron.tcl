proc resolve_izh_neuron_file {root_dir {explicit_path ""}} {
    set candidates [list]

    if {$explicit_path ne ""} {
        lappend candidates $explicit_path
    }

    if {[info exists ::env(IZH_NEURON_FILE)] && $::env(IZH_NEURON_FILE) ne ""} {
        lappend candidates $::env(IZH_NEURON_FILE)
    }

    if {[info exists ::env(IZH_NEURON_DIR)] && $::env(IZH_NEURON_DIR) ne ""} {
        lappend candidates [file join $::env(IZH_NEURON_DIR) izh_neuron.v]
        lappend candidates [file join $::env(IZH_NEURON_DIR) IZH_neuron.srcs sources_1 new izh_neuron.v]
    }

    lappend candidates [file join $root_dir vendor izh_neuron.v]
    lappend candidates [file join $root_dir src vendor izh_neuron.v]
    lappend candidates [file join $root_dir IZH_neuron izh_neuron.v]
    lappend candidates [file join $root_dir .. IZH_neuron izh_neuron.v]
    lappend candidates [file join $root_dir .. .. IZH_neuron izh_neuron.v]
    lappend candidates [file join $root_dir .. .. IZH_neuron IZH_neuron.srcs sources_1 new izh_neuron.v]

    set tried [list]
    foreach candidate $candidates {
        set normalized [file normalize $candidate]
        if {[lsearch -exact $tried $normalized] >= 0} {
            continue
        }
        lappend tried $normalized
        if {[file exists $normalized]} {
            puts "Using IZH neuron source: $normalized"
            return $normalized
        }
    }

    error "IZH neuron source not found. Set IZH_NEURON_FILE to the exact izh_neuron.v path, pass --izh-neuron-file <path>, or keep the vendored fallback at vendor/izh_neuron.v. Tried:\n  [join $tried \"\n  \"]"
}
