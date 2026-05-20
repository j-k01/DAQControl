# VC709 FMC HPC launch constraints for FMC-ADC500-CD / SE120 board-health test
# No JESD data lanes are constrained here. This file covers only sideband,
# SPI, fabric-visible clocks, SYSREF, and optional MGT reference-clock monitors.

# Presence / power-good sideband
set_property PACKAGE_PIN AL32 [get_ports FMC_C2M_PG_LS]
set_property IOSTANDARD LVCMOS18 [get_ports FMC_C2M_PG_LS]

set_property PACKAGE_PIN AN34 [get_ports FMC1_HPC_PG_M2C_LS]
set_property IOSTANDARD LVCMOS18 [get_ports FMC1_HPC_PG_M2C_LS]

set_property PACKAGE_PIN AM31 [get_ports FMC1_HPC_PRSNT_M2C_B_LS]
set_property IOSTANDARD LVCMOS18 [get_ports FMC1_HPC_PRSNT_M2C_B_LS]

# HMC7044 CLK_FMC -> FMC CLK0_M2C
set_property PACKAGE_PIN L39 [get_ports FMC1_HPC_CLK0_M2C_P]
set_property PACKAGE_PIN L40 [get_ports FMC1_HPC_CLK0_M2C_N]
set_property IOSTANDARD LVDS [get_ports {FMC1_HPC_CLK0_M2C_P FMC1_HPC_CLK0_M2C_N}]
set_property DIFF_TERM TRUE [get_ports {FMC1_HPC_CLK0_M2C_P FMC1_HPC_CLK0_M2C_N}]
create_clock -name DAQ_CLK_FMC -period 10.000 [get_ports FMC1_HPC_CLK0_M2C_P]

# HMC7044 SYSREF_FMC -> LA00_CC
set_property PACKAGE_PIN K39 [get_ports DAQ_SYSREF_P]
set_property PACKAGE_PIN K40 [get_ports DAQ_SYSREF_N]
set_property IOSTANDARD LVDS [get_ports {DAQ_SYSREF_P DAQ_SYSREF_N}]
set_property DIFF_TERM TRUE [get_ports {DAQ_SYSREF_P DAQ_SYSREF_N}]

# DAC_SYNC -> LA01_CC
set_property PACKAGE_PIN J40 [get_ports DAC_SYNC_P]
set_property PACKAGE_PIN J41 [get_ports DAC_SYNC_N]
set_property IOSTANDARD LVDS [get_ports {DAC_SYNC_P DAC_SYNC_N}]
set_property DIFF_TERM TRUE [get_ports {DAC_SYNC_P DAC_SYNC_N}]

# DAC39J84 SPI / sideband pins
# DAC_SCLK  -> LA02_P
# DAC_SDIN  -> LA02_N
# DAC_SDOUT -> LA03_P
# DAC_CS_N  -> LA04_N
# DAC_TXEN  -> LA04_P
# DAC_RESET -> LA07_P, active-low in this launch design
# DAC_ALARM -> LA07_N
set_property PACKAGE_PIN P41 [get_ports DAC_SCLK]
set_property PACKAGE_PIN N41 [get_ports DAC_SDIN]
set_property PACKAGE_PIN M42 [get_ports DAC_SDOUT]
set_property PACKAGE_PIN H41 [get_ports DAC_CS_N]
set_property PACKAGE_PIN H40 [get_ports DAC_TXEN]
set_property PACKAGE_PIN G41 [get_ports DAC_RESET_N]
set_property PACKAGE_PIN G42 [get_ports DAC_ALARM]
set_property IOSTANDARD LVCMOS18 [get_ports {DAC_SCLK DAC_SDIN DAC_SDOUT DAC_CS_N DAC_TXEN DAC_RESET_N DAC_ALARM}]

# ADC resets kept controllable but deasserted by default.
# ADC1_RESET -> LA03_N
# ADC2_RESET -> LA05_N
set_property PACKAGE_PIN L42 [get_ports ADC1_RESET]
set_property PACKAGE_PIN L41 [get_ports ADC2_RESET]
set_property IOSTANDARD LVCMOS18 [get_ports {ADC1_RESET ADC2_RESET}]

# HMC7044 SPI / reset pins
# HMC_CLK_RESET -> LA13_N
# HMC_CLK_SDIO  -> LA14_P
# HMC_CLK_SCLK  -> LA14_N
# HMC_CLK_CS_N  -> LA15_P
set_property PACKAGE_PIN G39 [get_ports HMC_CLK_RESET]
set_property PACKAGE_PIN N39 [get_ports HMC_CLK_SDIO]
set_property PACKAGE_PIN N40 [get_ports HMC_CLK_SCLK]
set_property PACKAGE_PIN M36 [get_ports HMC_CLK_CS_N]
set_property IOSTANDARD LVCMOS18 [get_ports {HMC_CLK_RESET HMC_CLK_SDIO HMC_CLK_SCLK HMC_CLK_CS_N}]

# FMC MGT reference clocks. These are monitored with IBUFDS_GTE2 ODIV2 only;
# no GTH data channels or JESD lanes are instantiated in this launch design.
set_property PACKAGE_PIN G10 [get_ports FMC1_HPC_GBTCLK0_M2C_C_P]
set_property PACKAGE_PIN G9  [get_ports FMC1_HPC_GBTCLK0_M2C_C_N]
create_clock -name DAQ_GBTCLK0 -period 8.000 [get_ports FMC1_HPC_GBTCLK0_M2C_C_P]

set_property PACKAGE_PIN E10 [get_ports FMC1_HPC_GBTCLK1_M2C_C_P]
set_property PACKAGE_PIN E9  [get_ports FMC1_HPC_GBTCLK1_M2C_C_N]
create_clock -name DAQ_GBTCLK1 -period 8.000 [get_ports FMC1_HPC_GBTCLK1_M2C_C_P]
