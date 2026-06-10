# Read the PS Ethernet app debug mailbox (see sw/ps_eth_stream/README.md)
# without disturbing the running A53. Uses the PSU/DAP target for memory
# access. Mailbox base: 0x0F000000, 8 u32 words.
connect
after 1000
targets -set -filter {name =~ "*PSU*"}
puts "PS Ethernet mailbox at 0x0F000000:"
puts [mrd 0x0F000000 8]
puts "progress legend: DA000001 main, DA000002 gic, DA000003 lwip_init,"
puts "  DA000004 emac_added, DA000005 netif_up, DA000006 udp_ready, DA0000FF loop"
puts "  DAE000xx = error (1 emac_add, 2 udp_new, 3 udp_bind)"
