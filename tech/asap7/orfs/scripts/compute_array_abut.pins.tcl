# IO pin region constraints for compute_array_abut (#40).
#
# Pins compute_array's perimeter pins by chip-top adjacency intent
# (gen_chip_top_floorplan.py: smem-W, store-S, cmdproc-NW, load-NW).
# Result:
#   - WEST edge: SMEM read iface + cmdproc command/status + scrub_en
#   - SOUTH edge: drain interface (all drain_*)
#   - EAST edge: clk, reset (just chip clk/reset, no chip-top adjacency)
#   - NORTH edge: empty
#
# This keeps every chip-IO net to a trivial straight or short-jog route at
# both block (compute_array) and chip-top levels. Removes the
# drain_row_data L-shape east-then-north wrap-around that caused the
# east-strip congestion the abut 32×32 build hit at GRT iter 15+.
#
# Sourced via IO_CONSTRAINTS in the config.mk.

# West edge — SMEM read interfaces + cmdproc command/status interfaces
set_io_pin_constraint -region left:* -pin_names {
    rd_a_en  rd_a_addr*  rd_a_data*  rd_a_valid  rd_a_stall_in
    rd_b_en  rd_b_addr*  rd_b_data*  rd_b_valid  rd_b_stall_in
    mma_issue  mma_slot*  mma_accum  mma_bar_id*
    issue_a_off*  issue_a_stride*  issue_b_off*  issue_b_stride*
    mma_busy  mma_done  arrive_en  arrive_bar_id*
    scrub_en
}

# South edge — drain interface (1024-bit drain bus + drain status)
set_io_pin_constraint -region bottom:* -pin_names {
    drain_issue  drain_slot*
    drain_busy  drain_done  drain_row_valid  drain_row_idx*  drain_last
    drain_row_data*
}

# East edge — chip clk + reset only (nothing else)
set_io_pin_constraint -region right:* -pin_names {clk reset}

# (North edge intentionally has no constraints → no pins. ORFS won't
# auto-place pins on north because all named ports are constrained above.)
