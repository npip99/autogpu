# V1 for #40: characterize a 32-stage clock feedthrough chain.
#
# Long narrow die forces the placer to spread the 32 BUFx2 cells along
# the x axis ≈ 34.56 µm pitch (matches abutted-mac-tile spacing from
# PR #36). The chain is functionally a no-op — all we want is the
# physical wire+buffer delay through 32 stages, and the corresponding
# clock uncertainty (OCV) at the stage-31 endpoint.

export PLATFORM      = asap7
export DESIGN_NAME   = clk_chain_32
export DESIGN_NICKNAME = clk_chain_32

export VERILOG_FILES = /work/build/sv2v/clk_chain_32.v
export SDC_FILE      = /work/tech/asap7/orfs/clk_chain_32.sdc

# 1200 × 50 µm long-narrow die. 32 BUFx2 cells (~0.5 µm² each) + 32 FFs
# (~3 µm² each) = ~110 µm² of cells, total area utilization ~0.2% — the
# placer is going to lay them down in a single row across the die,
# giving ~35-37 µm between buffers. Close enough to the 34.56 µm tile
# pitch for the chain timing to be representative.
export FLOORPLAN_DEF =
export DIE_AREA   = 0 0 1200 50
export CORE_AREA  = 5 5 1195 45

# Cap routing at M6 (matches mac_tmem_cell_tile's MAX_ROUTING_LAYER).
export MAX_ROUTING_LAYER = M6

# Skip last_gasp — this is a characterization run, we want the natural
# STA report not the post-repair view.
export SKIP_LAST_GASP ?= 1
