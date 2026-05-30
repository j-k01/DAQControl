# Debug and status CDC paths intentionally terminate at two-flop synchronizers.
set async_reg_cells [get_cells -hier -filter {ASYNC_REG == TRUE}]
if {[llength $async_reg_cells] > 0} {
    set_false_path -to [get_pins -of_objects $async_reg_cells -filter {REF_PIN_NAME == D}]
}
