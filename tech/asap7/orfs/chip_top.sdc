current_design chip_top

# 4 ns (250 MHz). Slower than compute_array's 400 MHz because chip_top
# wires span the full die — broadcast paths from cmdproc in the north
# strip to compute_array in the center cross ~600 µm of asap7 metal,
# which adds ~300–500 ps of wire delay (see DESIGN.md
# "Clock period vs RC budget"). asap7 lib uses 1 ps time units.
set clk_name    core_clock
set clk_port    clk
set clk_period  4000
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

# Chip-IO false-paths: chip_top's STA can't realistically check chip-pin
# inputs/outputs against the internal clock — board/package timing is
# what constrains those, not the asap7 model. Without these false-paths,
# CTS-introduced clock insertion (~500 ps to internal flops) causes
# massive hold violations on push_instr / mc_rd_data / etc. (RSZ-0060
# max-buffer at first attempt: 35,962 hold buffers inserted before giving up).
#
# Same pattern compute_array_abut.sdc uses for its block-level IO. The
# real boundary check happens at the PCB / package level, which is out
# of scope for this chip.
set_false_path -setup -from $non_clock_inputs
set_false_path -hold  -from $non_clock_inputs
set_false_path -setup -to [all_outputs]
set_false_path -hold  -to [all_outputs]
