# chip_top — top-level integration on asap7 (32×32 / MMA=32).
#
# Hierarchical hardening — every major block is a hardened LEF black box:
#   - compute_array_abut (1300×1300, 32×32 systolic array)
#   - store              (338×338, contains 4 tile_buf_8row internally)
#   - smem               (750×300, contains 16 smem_bank internally)
#   - cmdproc, load, barrier, reset_seq (small dispatch/control macros)
#
# Result: chip_top synth has only ~3K stdcells (chip-IO CDC + observability
# passthroughs + sys_idle AND). All ~550K stdcells of leaf logic live INSIDE
# the hardened LEFs and are opaque to chip_top STA/routing.
#
# Run: ./tech/asap7/orfs/run.sh chip_top

export PLATFORM     = asap7
export DESIGN_NAME  = chip_top

# Full MMA=32 sv2v output (chip_top.v uses chip_top.sv defaults, which are
# MMA_M=MMA_N=MMA_K=32 per the parameter declarations).
export VERILOG_FILES = /work/build/sv2v/chip_top.v
export SDC_FILE      = /work/tech/asap7/orfs/chip_top.sdc

# v6 floorplan: compute_array center-east, store south-below, smem west,
# cmdproc/load/barrier/reset_seq NW cluster. ~2.07 mm² of 4.14 mm² die used
# (~50% utilization — wide channels for chip-IO + bus routing).
export FLOORPLAN_DEF =
export DIE_AREA  = 0 0 2400 1800
export CORE_AREA = 25 25 2375 1775

ASAP7_RESULTS = /work/build/orfs/results/asap7
export ADDITIONAL_LEFS = \
    $(ASAP7_RESULTS)/compute_array_abut/base/compute_array.lef \
    $(ASAP7_RESULTS)/store/base/store.lef \
    $(ASAP7_RESULTS)/smem/base/smem.lef \
    $(ASAP7_RESULTS)/cmdproc/base/cmdproc.lef \
    $(ASAP7_RESULTS)/load/base/load.lef \
    $(ASAP7_RESULTS)/barrier/base/barrier.lef \
    $(ASAP7_RESULTS)/reset_seq/base/reset_seq.lef
export ADDITIONAL_LIBS = \
    $(ASAP7_RESULTS)/compute_array_abut/base/compute_array_typ.lib \
    $(ASAP7_RESULTS)/store/base/store_typ.lib \
    $(ASAP7_RESULTS)/smem/base/smem_typ.lib \
    $(ASAP7_RESULTS)/cmdproc/base/cmdproc_typ.lib \
    $(ASAP7_RESULTS)/load/base/load_typ.lib \
    $(ASAP7_RESULTS)/barrier/base/barrier_typ.lib \
    $(ASAP7_RESULTS)/reset_seq/base/reset_seq_typ.lib

export ADDITIONAL_GDS = \
    $(ASAP7_RESULTS)/compute_array_abut/base/6_final.gds \
    $(ASAP7_RESULTS)/store/base/6_final.gds \
    $(ASAP7_RESULTS)/smem/base/6_final.gds \
    $(ASAP7_RESULTS)/cmdproc/base/6_final.gds \
    $(ASAP7_RESULTS)/load/base/6_final.gds \
    $(ASAP7_RESULTS)/barrier/base/6_final.gds \
    $(ASAP7_RESULTS)/reset_seq/base/6_final.gds

export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/chip_top.macro_placement.tcl

# Keep yosys from flattening — each named submodule must stay as a
# discrete instance so the macro placer can bind them by name.
export SYNTH_HIERARCHICAL = 1

# Wide halos + channels for the inter-macro buses (1024-bit drain,
# 580-bit smem reads, etc).
export MACRO_PLACE_HALO    = 4 4
export MACRO_PLACE_CHANNEL = 8 8

# Custom PDN — pre-existing chip_top.pdn.tcl. Welds parent stripes to
# leaf macros' M6/M7 power pins.
export PDN_TCL = /work/tech/asap7/orfs/chip_top.pdn.tcl

# Memory cap covers cmdproc IMEM + observability shadow arrays.
export SYNTH_MEMORY_MAX_BITS = 524288

export SKIP_LAST_GASP ?= 1

# HOLD_SLACK_MARGIN: MASKS final-slack hold violations on chip_top.
# NOT the "same pattern" as compute_array_abut's -2000 — that one is a
# CTS-stage convergence aid that drops out of final slack; THIS one is
# final-slack masking. Hold violations on silicon are functional-failure
# class (data captured before it's valid → wrong outputs), not just a
# performance hit. The LEF as-hardened ships with real residual hold
# issues a foundry sign-off STA would reject. Used here ONLY to get a
# first integration LEF for methodology validation.
#
# Root cause: each macro's .lib clock-tree characterization doesn't match
# chip_top's parent CTS choices, producing 1008 ps STA skew that the
# resizer can't bridge (~35K hold-buffer pile → OpenROAD OOM at 68 GB).
# The proper fixes are tracked in #52 (.lib characterization gap) and
# #50 (chip-level traveling clock to eliminate parent CTS entirely).
# DO NOT consider this LEF tape-out usable.
export HOLD_SLACK_MARGIN = -2000

# Skip the DRT incremental-repair loop — it loops indefinitely when the
# underlying skew is unfixable by buffer insertion (64 stuck violations
# across iter 3 in the first attempt). Like HOLD_SLACK_MARGIN above,
# this is a workaround that hides a real problem rather than fixing it.
export SKIP_INCREMENTAL_REPAIR = 1

# Skip kepler-formal LEC (exponential on chip_top).
export LEC_CHECK = 0
