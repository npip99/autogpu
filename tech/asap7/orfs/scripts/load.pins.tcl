# load pin placement — v6 chip_top floorplan adjacency.
#
# Geometry context: load sits in the NW cluster, directly south of cmdproc.
# smem is far SW. barrier is east-of-load. mc_rd_* (off-chip DRAM read) goes
# to chip-IO pad on west die edge.
#
# Edge assignment:
#   N = dispatch from cmdproc (issue_en + ptrs + bytes_n + bar_id, ~163b)
#       + status back to cmdproc (busy/done/accept)
#   S = smem_wr_* (162b → smem.N, smem below load)
#   E = barrier tx/arrive (163b → barrier.W)
#   W = gmem_* to chip IO (mc_rd_en/addr/data, 162b) + clk + reset

set_io_pin_constraint -region top:* -pin_names {
    issue_en  gmem_ptr*  smem_ptr*  bytes_n*  bar_id*
    busy  done  accept
}

set_io_pin_constraint -region bottom:* -pin_names {
    smem_wr_en  smem_wr_addr*  smem_wr_data*  smem_wr_stall_in
}

set_io_pin_constraint -region right:* -pin_names {
    add_tx_en  add_tx_bar_id*  add_tx_bytes*
    sub_tx_en  sub_tx_bar_id*  sub_tx_bytes*
    arrive_en  arrive_bar_id*
}

set_io_pin_constraint -region left:* -pin_names {
    gmem_rd_en  gmem_rd_addr*  gmem_rd_data*  gmem_rd_valid
    clk  reset
}
