set script_dir [file dirname [file normalize [info script]]]
set frames 4096
set out_file [file join $script_dir .. adc_capture_jtag.csv]

if {[llength $argv] > 0} {
    set frames [expr {[lindex $argv 0]}]
}
if {[llength $argv] > 1} {
    set out_file [file normalize [lindex $argv 1]]
}

set chip_words [expr {$frames * 4}]

set REG_BASE  0x44A10000
set RW_REG1   [format "0x%08X" [expr {$REG_BASE + 0x04}]]
set RW_REG2   [format "0x%08X" [expr {$REG_BASE + 0x08}]]
set RW_REG3   [format "0x%08X" [expr {$REG_BASE + 0x0C}]]
set RO_REG3   [format "0x%08X" [expr {$REG_BASE + 0x2C}]]
set ADC0_CAPTURE_BRAM_BASE 0xC0100000
set ADC1_CAPTURE_BRAM_BASE 0xC0110000

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

puts [format "Capture complete, status=0x%08X. Reading %d frames from ADC0/ADC1 capture BRAMs..." $status $frames]
set adc0_samples [mrd -value [format "0x%08X" $ADC0_CAPTURE_BRAM_BASE] $chip_words]
set adc1_samples [mrd -value [format "0x%08X" $ADC1_CAPTURE_BRAM_BASE] $chip_words]

set fd [open $out_file w]
puts $fd "frame,source,word_hex,lo16,hi16,lo16_signed,hi16_signed"
for {set frame 0} {$frame < $frames} {incr frame} {
    for {set word_index 0} {$word_index < 4} {incr word_index} {
        set sample [lindex $adc0_samples [expr {$frame * 4 + $word_index}]]
        set word [expr {$sample & 0xffffffff}]
        set lo [expr {$word & 0xffff}]
        set hi [expr {($word >> 16) & 0xffff}]
        puts $fd [format "%d,%d,0x%08X,%d,%d,%d,%d" \
            $frame $word_index $word $lo $hi [signed16 $lo] [signed16 $hi]]
    }
    for {set word_index 0} {$word_index < 4} {incr word_index} {
        set sample [lindex $adc1_samples [expr {$frame * 4 + $word_index}]]
        set word [expr {$sample & 0xffffffff}]
        set lo [expr {$word & 0xffff}]
        set hi [expr {($word >> 16) & 0xffff}]
        puts $fd [format "%d,%d,0x%08X,%d,%d,%d,%d" \
            $frame [expr {$word_index + 4}] $word $lo $hi [signed16 $lo] [signed16 $hi]]
    }
}
close $fd

write32 $RW_REG2 $old_rw2
write32 $RW_REG1 $old_rw1

puts "$frames frames written to $out_file"
