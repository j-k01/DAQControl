cmake_minimum_required(VERSION 3.13)

include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(zcu102_usb_led C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

add_executable(zcu102_usb_led
    main.c
    usb_descriptors.c
)

pico_set_binary_type(zcu102_usb_led no_flash)
target_link_libraries(zcu102_usb_led pico_stdlib pico_multicore)
target_compile_definitions(zcu102_usb_led PRIVATE
    PICO_ENABLE_USB_RESET_VIA_BAUD_RATE=1
    PICO_ENABLE_USB_RESET_VIA_VENDOR_INTERFACE=1
    PICO_STDIO_USB_ENABLE_RESET_VIA_BAUD_RATE=1
    PICO_STDIO_USB_USE_DEFAULT_DESCRIPTORS=0
)
pico_enable_stdio_usb(zcu102_usb_led 1)
pico_enable_stdio_uart(zcu102_usb_led 0)
pico_add_extra_outputs(zcu102_usb_led)
