set script_dir [file dirname [file normalize [info script]]]
set frames 4096
set out_file [file join $script_dir .. adc_capture_jtag.csv]

if {[llength $argv] > 0} {
    set frames [expr {[lindex $argv 0]}]
}
if {[llength $argv] > 1} {
    set out_file [file normalize [lindex $argv 1]]
}

set words [expr {$frames * 4}]

set REG_BASE  0x44A10000
set RW_REG1   [format "0x%08X" [expr {$REG_BASE + 0x04}]]
set RW_REG2   [format "0x%08X" [expr {$REG_BASE + 0x08}]]
set RW_REG3   [format "0x%08X" [expr {$REG_BASE + 0x0C}]]
set RO_REG3   [format "0x%08X" [expr {$REG_BASE + 0x1C}]]
set ADC_CAPTURE_BRAM_BASE 0xC0100000

proc read32 {addr} {
    return [expr {[lindex [mrd -value $addr] 0] & 0xffffffff}]
}

proc write32 {addr value} {
    mwr $addr [format "0x%08X" [expr {$value & 0xffffffff}]]
}

proc signed16 {value} {
    set value [expr {$value & 0xffff}]
    if {$value & 0x8000} {
        return [expr {$value - 0x10000}]
    }
    return $value
}

connect
targets -set -filter {name =~ "MicroBlaze #0"}

set old_rw1 [read32 $RW_REG1]
set old_rw2 [read32 $RW_REG2]
set old_rw3 [read32 $RW_REG3]

set capture_rw3 [expr {$old_rw3 & ~0x00000048}]
write32 $RW_REG3 [expr {$capture_rw3 | 0x00000008}]
write32 $RW_REG3 $capture_rw3

write32 $RW_REG1 31
write32 $RW_REG2 [expr {$old_rw2 | 0x80000000}]

set status 0
set done 0
for {set i 0} {$i < 1000000} {incr i} {
    set status [read32 $RO_REG3]
    if {(($status & 0xff000000) == 0xc4000000) && (($status & 0x00080000) != 0)} {
        set done 1
        break
    }
}

if {!$done} {
    write32 $RW_REG2 $old_rw2
    write32 $RW_REG1 $old_rw1
    error [format "ADC BRAM capture timed out, status=0x%08X" $status]
}

puts [format "Capture complete, status=0x%08X. Reading %d frames / %d words from ADC BRAM..." $status $frames $words]
set samples [mrd -value [format "0x%08X" $ADC_CAPTURE_BRAM_BASE] $words]

set fd [open $out_file w]
puts $fd "frame,source,word_hex,lo16,hi16,lo16_signed,hi16_signed"
set index 0
foreach sample $samples {
    set word [expr {$sample & 0xffffffff}]
    set lo [expr {$word & 0xffff}]
    set hi [expr {($word >> 16) & 0xffff}]
    puts $fd [format "%d,%d,0x%08X,%d,%d,%d,%d" \
        [expr {$index / 4}] [expr {$index % 4}] $word $lo $hi [signed16 $lo] [signed16 $hi]]
    incr index
}
close $fd

write32 $RW_REG2 $old_rw2
write32 $RW_REG1 $old_rw1

puts "$frames frames written to $out_file"
