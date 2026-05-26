current_design compute_array

# A2 Solution 3: 2.5 ns clock + reversed useful-skew SDC.
# The prior useful_skew attempt asserted that outer macros have HIGHER
# source latency (their clock arrives LATER). That's setup-friendly but
# hold-hostile. This reversed version asserts outer macros get clock
# EARLIER (NEGATIVE source latency), which is hold-friendly.
set clk_name    core_clock
set clk_port    clk
set clk_period  2500
set clk_io_pct  0.2

create_clock -name $clk_name -period $clk_period [get_ports $clk_port]

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]

source /work/tech/asap7/orfs/compute_array_tiny.useful_skew_rev.sdc
