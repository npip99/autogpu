# cmdproc pin placement — v6 chip_top floorplan adjacency.
#
# Geometry context: cmdproc sits NW of compute_array with ~200 µm gap.
# Load is directly south. Barrier is south+east. Store is far SE
# (reached via channel south of compute_array).
#
# Edge assignment:
#   N = push_instr[255:0] (chip IO from top), push_en, reset
#   E = mma_* outputs (130 bits → compute_array.W, short east hop)
#       + store_* outputs (66 bits → store, routes via south channel)
#       + barrier_wait_done in
#   S = load_* outputs (129b → load.N) + init/query_* (83b → barrier.N)
#       + status from engines back in (busy/done/accept)
#   W = clk (chip IO from west), idle + observability status (chip outputs)
#
# Pre-v6 (auto-placed): cmdproc had 48% of pins on east edge, push_instr[256]
# smeared across all 4 edges, forcing long traces from chip pin to each bit.

# North edge — chip-IO inputs and chip-clk reset
set_io_pin_constraint -region top:* -pin_names {
    push_instr*  push_en  reset
}

# East edge — to compute_array (mma_*) + to store (store_*) + return from barrier
set_io_pin_constraint -region right:* -pin_names {
    mma_start  mma_a_smem_offset*  mma_b_smem_offset*  mma_d_tmem_slot*
    mma_accum  mma_bar_id*
    store_issue_en  store_tmem_slot*  store_gmem_ptr*  store_dtype
    barrier_wait_done
}

# South edge — to load (load_*) and barrier (init/query_*) and status returns
set_io_pin_constraint -region bottom:* -pin_names {
    load_issue_en  load_gmem_ptr*  load_smem_ptr*  load_bytes_n*  load_bar_id*
    init_en  init_bar_id*  init_count*  query_bar_id*  query_expected_phase
    load_busy  load_done  load_accept
    store_busy  store_done
    mma_busy  mma_done
}

# West edge — chip clk + status outputs
set_io_pin_constraint -region left:* -pin_names {clk idle}
