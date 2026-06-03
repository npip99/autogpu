# store pin placement — v6 chip_top floorplan adjacency.
#
# Geometry context: store sits centered below compute_array at chip_top
# (compute_array on N, chip-IO pads on S, cmdproc reach on W via channel,
# E free).
#
# Edge assignment:
#   N = drain interface from compute_array (1024-bit drain_row_data + ctrl)
#   S = mc_wr_* to off-chip memory controller (chip pins)
#   W = store dispatch from cmdproc (issue_en + tmem_slot + gmem_ptr + dtype)
#       + status return (busy, done)
#   E = clk + reset only (nothing else needed)
#
# Pre-v6 (auto-placed): drain_row_data was scattered E:76 N:127 S:409 W:412,
# forcing 1024-wire criss-cross routing at chip_top. This file fixes it.

set_io_pin_constraint -region top:* -pin_names {
    drain_row_data*  drain_row_valid  drain_row_idx*  drain_last
    drain_issue  drain_slot*  drain_done
}

set_io_pin_constraint -region bottom:* -pin_names {
    wr_en  wr_addr*  wr_data*
}

set_io_pin_constraint -region left:* -pin_names {
    issue_en  tmem_slot*  gmem_ptr*  dtype  busy  done
}

set_io_pin_constraint -region right:* -pin_names {clk reset}
