# Reset the whole PS through XSCT. Use when the A53 ELF download fails with
# "Memory write error ... Cannot flush CPU cache. Cortex-A53 #0: EDITR timeout".
# After this reset the PL is cleared as well: reprogram the FPGA and reload
# the MicroBlaze firmware (program_and_load.tcl) before loading any PS app.
connect
after 1000
targets -set -filter {name =~ "*PSU*"}
rst -system
after 5000
puts "Targets after PS system reset:"
targets
