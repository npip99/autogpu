# store pin placement — v6 chip_top floorplan adjacency.
#
# Geometry context: store sits centered below compute_array at chip_top
# (compute_array on N, chip-IO pads on S, cmdproc reach on W via channel).
#
# Edge assignment:
#   N = drain interface from compute_array (1024-bit drain_row_data + ctrl)
#   S = mc_wr_* to off-chip memory controller (chip pins)
#   W = store dispatch from cmdproc (issue_en + tmem_slot + gmem_ptr +
#       dtype) + status return (busy, done) + clk + reset
#   E = empty (no chip-top adjacency on east)
#
# Pin density check:
#   1024 drain bits on store's 338µm N edge = 0.33 µm/pin pitch.
#   M5 IO pin pitch in asap7 is ~0.04 µm minimum; typical IO assignment
#   uses ~0.2-0.4 µm. 0.33 µm is borderline but should be feasible with
#   ORFS's multi-layer pin stacking. If DRT spins, fall back to spreading
#   drain across N + E.

set_io_pin_constraint -region top:* -pin_names {
    drain_row_data*
    drain_row_valid  drain_row_idx*  drain_last
    drain_issue  drain_slot*  drain_done
}

set_io_pin_constraint -region bottom:* -pin_names {
    wr_en  wr_addr*  wr_data*
}

set_io_pin_constraint -region left:* -pin_names {
    issue_en  tmem_slot*  gmem_ptr*  dtype  busy  done
    clk  reset
}
