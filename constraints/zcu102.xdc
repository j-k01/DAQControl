# ZCU102 Rev 1.x board constraints
# Source: AMD/Xilinx UG1182 ZCU102 Evaluation Board User Guide.

# Programmable USER_SI570 clock, default 300 MHz, U42 -> PL bank 64 GC inputs.
set_property PACKAGE_PIN AL8 [get_ports SYSCLK_P]
set_property PACKAGE_PIN AL7 [get_ports SYSCLK_N]
set_property IOSTANDARD LVDS [get_ports {SYSCLK_P SYSCLK_N}]
create_clock -name USER_SI570_300 -period 3.333 [get_ports SYSCLK_P]

# CPU Reset, active-high.
set_property PACKAGE_PIN AM13 [get_ports CPU_RESET]
set_property IOSTANDARD LVCMOS33 [get_ports CPU_RESET]

# GPIO LEDs, active-high.
set_property PACKAGE_PIN AG14 [get_ports {GPIO_LED[0]}]
set_property PACKAGE_PIN AF13 [get_ports {GPIO_LED[1]}]
set_property PACKAGE_PIN AE13 [get_ports {GPIO_LED[2]}]
set_property PACKAGE_PIN AJ14 [get_ports {GPIO_LED[3]}]
set_property PACKAGE_PIN AJ15 [get_ports {GPIO_LED[4]}]
set_property PACKAGE_PIN AH13 [get_ports {GPIO_LED[5]}]
set_property PACKAGE_PIN AH14 [get_ports {GPIO_LED[6]}]
set_property PACKAGE_PIN AL12 [get_ports {GPIO_LED[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {GPIO_LED[*]}]

# CP2108 Channel 2 PL-side UART.
# E13 is USB-UART TX into FPGA RXD; F13 is FPGA TXD into USB-UART RX.
set_property PACKAGE_PIN E13 [get_ports UART_RXD]
set_property PACKAGE_PIN F13 [get_ports UART_TXD]
set_property IOSTANDARD LVCMOS18 [get_ports {UART_RXD UART_TXD}]
