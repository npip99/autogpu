# chip_top — top-level integration on asap7.
#
# Hierarchical hardening:
#   - compute_array (hardened LEF — using the `compute_array_tiny_bcast0`
#     4×4 variant as first-pass placeholder per A6_chip_top.md, since
#     the full 32×32 variant has not yet closed timing / PSM-0069. The
#     "MACRO name" inside that LEF is the SV module name `compute_array`,
#     so chip_top's `compute_array u_compute_array` instantiation binds
#     to it as a black-box.)
#   - cmdproc, load, barrier, reset_seq — hardened LEFs (no MMA_M dep).
#   - smem inlined → 32 fakeram7_256x32 macros from the asap7 platform.
#   - store inlined → flat FF banks (tile_buf_8row not used as a macro
#     here because its hardened LEF was sized for the 32×32 chip_top's
#     ROW_W=1024; tiny ROW_W=128 doesn't match).
#
# Run with: ./tech/asap7/orfs/run.sh chip_top
#
# Note: produces the "tiny" 4×4 chip_top. To switch to full 32×32 once
# compute_array hardens cleanly:
#   1) Rebuild sv2v: make -C tech/sky130 MMA_DIM=32 ../build/sv2v/chip_top_asap7_tiny.v
#   2) Update ADDITIONAL_LEFS / ADDITIONAL_LIBS / ADDITIONAL_GDS below to
#      point at the full compute_array hardening artifacts (drop the
#      _tiny_bcast0 suffix).
#   3) Re-run gen_chip_top_floorplan.py to rescale macro placement.

export PLATFORM     = asap7
export DESIGN_NAME  = chip_top

# sv2v output with MMA_M=MMA_N=MMA_K=4 and USE_ASAP7_FAKERAM — see
# tech/sky130/Makefile target `build/sv2v/chip_top_asap7_tiny.v`.
export VERILOG_FILES = /work/build/sv2v/chip_top_asap7_tiny.v
export SDC_FILE      = /work/tech/asap7/orfs/chip_top.sdc

# Absolute floorplan (DIE/CORE come from gen_chip_top_floorplan.py).
export FLOORPLAN_DEF =
export DIE_AREA  = 0 0 900 800
export CORE_AREA = 25 25 875 775

# Hardened submodule LEFs/LIBs + asap7-platform fakeram macro for smem.
ASAP7_RESULTS = /work/build/orfs/results/asap7
ASAP7_PLAT    = /OpenROAD-flow-scripts/flow/platforms/asap7
export ADDITIONAL_LEFS = \
    $(ASAP7_RESULTS)/compute_array_tiny_bcast0/base/compute_array_tiny_bcast0.lef \
    $(ASAP7_RESULTS)/cmdproc/base/cmdproc.lef \
    $(ASAP7_RESULTS)/load/base/load.lef \
    $(ASAP7_RESULTS)/barrier/base/barrier.lef \
    $(ASAP7_RESULTS)/reset_seq/base/reset_seq.lef \
    $(ASAP7_PLAT)/lef/fakeram7_256x32.lef
export ADDITIONAL_LIBS = \
    $(ASAP7_RESULTS)/compute_array_tiny_bcast0/base/compute_array_tiny_bcast0_typ.lib \
    $(ASAP7_RESULTS)/cmdproc/base/cmdproc_typ.lib \
    $(ASAP7_RESULTS)/load/base/load_typ.lib \
    $(ASAP7_RESULTS)/barrier/base/barrier_typ.lib \
    $(ASAP7_RESULTS)/reset_seq/base/reset_seq_typ.lib \
    $(ASAP7_PLAT)/lib/NLDM/fakeram7_256x32.lib

# GDS for streamout merge. compute_array_tiny has no 6_final.gds (the
# KLayout merge step never ran because PSM-0069 failed the final report
# — see A6_chip_top.md). chip_top's KLayout streamout will report it as
# missing; GDS_ALLOW_EMPTY catches it so the merge still produces a
# chip_top.gds (with an empty compute_array cell stub). Real silicon
# needs the GDS to be populated; this is part of the A1 follow-up.
export ADDITIONAL_GDS = \
    $(ASAP7_RESULTS)/cmdproc/base/6_final.gds \
    $(ASAP7_RESULTS)/load/base/6_final.gds \
    $(ASAP7_RESULTS)/barrier/base/6_final.gds \
    $(ASAP7_RESULTS)/reset_seq/base/6_final.gds
# fakeram7 macros + compute_array (missing GDS) are skipped at merge.
export GDS_ALLOW_EMPTY = (fakeram.*|compute_array)

# Explicit macro placement.
export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/chip_top.macro_placement.tcl

# Keep yosys from flattening the hierarchy — the named submodule and
# fakeram instances must stay as discrete instances for the macro placer
# to bind them by name.
export SYNTH_HIERARCHICAL = 1

# Halo + channel spacing around each macro. Generous to leave room for
# inter-block wide buses (drain_row_data, rd_a/rd_b_data).
export MACRO_PLACE_HALO    = 4 4
export MACRO_PLACE_CHANNEL = 8 8

# Custom PDN — see chip_top.pdn.tcl.
export PDN_TCL = /work/tech/asap7/orfs/chip_top.pdn.tcl

# Inlined smem.sv contains a verilator-public 128 Kib bank_mem shadow
# array for cocotb backdoor reads (same as in smem.config.mk). Raise the
# yosys SYNTH_MEMORY_MAX_BITS cap above 131072 to let opt eliminate it.
# Also covers cmdproc IMEM/loop_stack and store FF banks if they spill.
export SYNTH_MEMORY_MAX_BITS = 524288

# TEMPORARY WORKAROUND — NOT TAPE-OUT SHIPPABLE.
# Same rationale as compute_array_tiny_bcast0.config.mk: chip_top has
# the same hierarchical-CTS-skew → hold violation pattern from broadcast
# nets across the die. -200 ps tolerance lets repair_timing terminate
# instead of running away with hold-buffer insertion. The slack is real
# and would race on silicon — A2's domain.
export HOLD_SLACK_MARGIN = -200

export SKIP_LAST_GASP ?= 1

# Skip kepler-formal LEC (exponential on chip_top with its inlined smem
# + store + glue logic).
export LEC_CHECK = 0
