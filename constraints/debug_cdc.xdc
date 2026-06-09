# Debug and status CDC paths intentionally terminate at explicit two-flop
# synchronizers.  Collect pins by object property rather than by name-filtered
# get_pins calls; Vivado can rename vector flops and leave the old filter empty.
proc daq_get_clocks_by_regexp {pattern} {
    return [get_clocks -quiet -regexp $pattern]
}

proc daq_get_cell_pins_by_ref_name {cells ref_name} {
    set matched_pins {}
    foreach cell $cells {
        foreach pin [get_pins -quiet -of_objects $cell] {
            if {[get_property REF_PIN_NAME $pin] eq $ref_name} {
                lappend matched_pins $pin
            }
        }
    }
    return $matched_pins
}

# The launch design deliberately samples board-health and GT-status signals
# into the 200 MHz fabric/debug domain.  None of those CDC paths are functional
# timing paths; they terminate in explicit synchronizers or debug-only status
# logic.  Cut the clock domains at the clock level so first-stage CDC flops do
# not dominate route timing when Vivado renames or replicates the registers.
set fabric_debug_clks [daq_get_clocks_by_regexp {^(USER_SI570_300|clk_out[0-9]+_clk_wiz_0_1)$}]
set gth_tx_user_clks  [daq_get_clocks_by_regexp {.*gtwiz_userclk_tx.*}]
set gth_rx_user_clks  [daq_get_clocks_by_regexp {.*gtwiz_userclk_rx.*}]
set sampled_fmc_clks  [daq_get_clocks_by_regexp {^DAQ_CLK_FMC$}]

if {[llength $fabric_debug_clks] > 0 && [llength $gth_tx_user_clks] > 0} {
    set_clock_groups -asynchronous -group $fabric_debug_clks -group $gth_tx_user_clks
}
if {[llength $fabric_debug_clks] > 0 && [llength $gth_rx_user_clks] > 0} {
    set_clock_groups -asynchronous -group $fabric_debug_clks -group $gth_rx_user_clks
}
if {[llength $gth_tx_user_clks] > 0 && [llength $gth_rx_user_clks] > 0} {
    set_clock_groups -asynchronous -group $gth_tx_user_clks -group $gth_rx_user_clks
}
if {[llength $fabric_debug_clks] > 0 && [llength $sampled_fmc_clks] > 0} {
    set_clock_groups -asynchronous -group $fabric_debug_clks -group $sampled_fmc_clks
}

set async_reg_cells [get_cells -hier -quiet -filter {ASYNC_REG == TRUE}]
set async_reg_d_pins [daq_get_cell_pins_by_ref_name $async_reg_cells D]
if {[llength $async_reg_d_pins] > 0} {
    set_false_path -to $async_reg_d_pins
}

# The placed timing reports showed these first-stage CDC flops still being
# timed in some Vivado runs, despite the RTL ASYNC_REG attributes.  Keep an
# instance-name fallback for the explicit debug/status synchronizers.
set explicit_async_meta_pins [get_pins -hier -quiet -regexp {.*(u_fabric_debug_sync|u_adc1_rx_debug_sync|u_gth_rx_userclk_monitor|u_gth_tx_userclk_monitor|u_clk_fmc_monitor|u_sysref_monitor)/(meta_reg(\[[0-9]+\])?|gray_meta_reg(\[[0-9]+\])?|signal_meta_reg)/D$}]
if {[llength $explicit_async_meta_pins] > 0} {
    set_false_path -to $explicit_async_meta_pins
}

# LiteJESD's generated reset synchronizers are emitted as FDPE instances instead
# of ASYNC_REG-attributed Verilog regs.  They are reset synchronizers, not data
# paths, so the async preset timing must not dominate implementation.
set litejesd_reset_sync_cells [get_cells -hier -quiet -regexp {.*(u_litejesd_dac_tx_path/u_litejesd_dac_tx|u_adc_frontend/u_adc[01]_rx_path/u_litejesd_adc1_rx)/FDPE_[0-9]+$}]
if {[llength $litejesd_reset_sync_cells] > 0} {
    set_property ASYNC_REG TRUE $litejesd_reset_sync_cells
    set litejesd_reset_sync_pre_pins [daq_get_cell_pins_by_ref_name $litejesd_reset_sync_cells PRE]
    if {[llength $litejesd_reset_sync_pre_pins] > 0} {
        set_false_path -to $litejesd_reset_sync_pre_pins
    }
}
