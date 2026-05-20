set script_dir [file dirname [file normalize [info script]]]

set ws $script_dir/sw/workspace
if {[file exists $ws]} {
    file delete -force $ws
}
setws $ws

platform create -name hw_platform -hw $script_dir/hw/DAQ_LAUNCH.xsa
domain create -name standalone -os standalone -proc microblaze_0
platform generate

app create -name firmware -platform hw_platform -domain standalone -template {Empty Application(C)}
importsources -name firmware -path $script_dir/sw/src
app build -name firmware

puts "ELF: $script_dir/sw/workspace/firmware/Debug/firmware.elf"
