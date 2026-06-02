export PLATFORM     = asap7
export DESIGN_NAME  = store

# Hierarchical: 4 × tile_buf_8row consumed as hardened LEF black boxes.
# WITHOUT this, yosys inlined the 4 × 8K-bit FF banks → 183K stdcells +
# 1298 IO pins + ~1hr DRT. See #28-followup / #43 for the build-system
# enforcement to keep this from regressing.
export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/store.sdc

ASAP7_RESULTS = /work/build/orfs/results/asap7
export ADDITIONAL_LEFS = $(ASAP7_RESULTS)/tile_buf_8row/base/tile_buf_8row.lef
export ADDITIONAL_LIBS = $(ASAP7_RESULTS)/tile_buf_8row/base/tile_buf_8row_typ.lib
export ADDITIONAL_GDS  = $(ASAP7_RESULTS)/tile_buf_8row/base/6_final.gds

# Keep yosys from flattening — the 4 tile_buf_8row instances must stay
# as discrete LEF references for the macro placer to bind them.
export SYNTH_HIERARCHICAL = 1

export CORE_UTILIZATION  = 50
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.70

export SKIP_LAST_GASP ?= 1
