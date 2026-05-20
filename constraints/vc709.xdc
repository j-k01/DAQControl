# VC709 board constraints

set_property CFGBVS                     GND   [current_design]
set_property CONFIG_VOLTAGE             1.8   [current_design]
set_property CONFIG_MODE                BPI16 [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE  [current_design]

# 200 MHz system clock
set_property PACKAGE_PIN H19 [get_ports SYSCLK_P]
set_property PACKAGE_PIN G18 [get_ports SYSCLK_N]
set_property IOSTANDARD DIFF_SSTL15 [get_ports {SYSCLK_P SYSCLK_N}]
create_clock -name SYSCLK_200 -period 5.000 [get_ports SYSCLK_P]

# CPU Reset, active-high
set_property PACKAGE_PIN AV40     [get_ports CPU_RESET]
set_property IOSTANDARD  LVCMOS18 [get_ports CPU_RESET]

# GPIO LEDs
set_property PACKAGE_PIN AM39     [get_ports {GPIO_LED[0]}]
set_property PACKAGE_PIN AN39     [get_ports {GPIO_LED[1]}]
set_property PACKAGE_PIN AR37     [get_ports {GPIO_LED[2]}]
set_property PACKAGE_PIN AT37     [get_ports {GPIO_LED[3]}]
set_property PACKAGE_PIN AR35     [get_ports {GPIO_LED[4]}]
set_property PACKAGE_PIN AP41     [get_ports {GPIO_LED[5]}]
set_property PACKAGE_PIN AP42     [get_ports {GPIO_LED[6]}]
set_property PACKAGE_PIN AU39     [get_ports {GPIO_LED[7]}]
set_property IOSTANDARD  LVCMOS18 [get_ports {GPIO_LED[*]}]

# USB UART
set_property PACKAGE_PIN AU33     [get_ports UART_RXD]
set_property PACKAGE_PIN AU36     [get_ports UART_TXD]
set_property IOSTANDARD  LVCMOS18 [get_ports {UART_RXD UART_TXD}]
