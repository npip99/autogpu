# tile_buf_8row pin placement — used inside store (4 banks placed by store's
# floorplan).
#
# Pre-v6 (auto-placed): rd_data[1024] + wr_data[1024] each scattered ~equal
# on all 4 edges. This forced store's internal muxing logic to be heavily
# buffered (13K resizer-added cells on the parent harden).
#
# Edge assignment:
#   W = wr_data (1024b in) + wr_en + wr_row
#   E = rd_data (1024b out) + rd_en + rd_row
#   N/S = nothing (clean abutment-style geometry; the bank's clk/reset
#         live on W with the write side since they're simple control)
#
# With wr_* contiguous on W and rd_* contiguous on E, store's internal
# mux fabric becomes a simple W→E flow per bank.

set_io_pin_constraint -region left:* -pin_names {
    wr_en  wr_row*  wr_data*  clk  reset
}

set_io_pin_constraint -region right:* -pin_names {
    rd_en  rd_row*  rd_data*
}
