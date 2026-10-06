create_clock -name clk27 -period 37.037 [get_ports {clk27}]
create_generated_clock -name ddr_ck_96mhz -source [get_ports {clk27}] -divide_by 9 -multiply_by 32 [get_pins {rPLL/CLKOUT}]
create_generated_clock -name sys2x_clk -source [get_nets {crg_clkout}] -divide_by 1 -multiply_by 1 [get_pins {DHCEN/CLKOUT}]
create_generated_clock -name sys_clk -source [get_nets {sys2x_clk}] -divide_by 2 -multiply_by 1 [get_pins {CLKDIV/CLKOUT}]
set_false_path -from [get_clocks {clk27}] -to [get_clocks {ddr_ck_96mhz}]
set_false_path -from [get_clocks {clk27}] -to [get_clocks {sys_clk}]
set_false_path -to [get_pins {ddrphy/DQS/RESET ddrphy/DQS_1/RESET ddrphy/OSER4/RESET ddrphy/OSER4_*/RESET ddrphy/OSER4_MEM/RESET ddrphy/OSER4_MEM_*/RESET}] -setup