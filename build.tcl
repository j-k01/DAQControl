set script_dir [file dirname [file normalize [info script]]]
set project_name "DAQ_LAUNCH"

open_project $script_dir/project/${project_name}.xpr

if {[lsearch $::argv "--bake"] >= 0} {
    set elf_file $script_dir/sw/workspace/firmware/Debug/firmware.elf
    add_files $elf_file
    set_property SCOPED_TO_REF microblaze_bd [get_files firmware.elf]
    set_property SCOPED_TO_CELLS microblaze_0 [get_files firmware.elf]
}

set jobs 4

reset_run synth_1
launch_runs synth_1 -jobs $jobs
wait_on_run synth_1

launch_runs impl_1 -jobs $jobs
wait_on_run impl_1

launch_runs impl_1 -to_step write_bitstream -jobs $jobs
wait_on_run impl_1

file mkdir $script_dir/hw
open_run impl_1
write_hw_platform -fixed -include_bit -force $script_dir/hw/${project_name}.xsa

puts "Bitstream: $script_dir/project/${project_name}.runs/impl_1/top.bit"
puts "Hardware:  $script_dir/hw/${project_name}.xsa"

close_project
