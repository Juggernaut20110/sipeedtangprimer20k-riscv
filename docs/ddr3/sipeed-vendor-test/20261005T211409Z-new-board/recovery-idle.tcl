set_device -name GW2A-18C GW2A-LV18PG256C8/I7
add_file idle.v
add_file idle.cst
set_option -top_module ddr_idle
set_option -output_base_name ddr_idle
run all
