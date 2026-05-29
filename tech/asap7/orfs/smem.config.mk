export PLATFORM     = asap7
export DESIGN_NAME  = smem

# asap7-flavored sv2v output — smem.sv instantiates smem_bank macros
# under USE_ASAP7_FAKERAM (see smem/smem.sv and tech/sky130/Makefile
# target build/sv2v/smem_asap7.v).
export VERILOG_FILES = /work/build/sv2v/smem_asap7.v
export SDC_FILE      = /work/tech/asap7/orfs/smem.sdc

# Hardened smem_bank macros — see tech/asap7/orfs/smem_bank.config.mk.
# Each smem_bank wraps one fakeram7_256x32 + per-output-dword gating
# logic. 16 instances inside smem.sv (2 regions × 8 banks) replace the
# prior centralized mux pattern; bank_rdata fanout is consumed inside
# each hardened bank so only already-gated outputs cross the smem-level
# routing channels. See tech/asap7/problems/B1_smem_bank_rdata_congestion.md.
ASAP7_RESULTS = /work/build/orfs/results/asap7
export ADDITIONAL_LEFS = $(ASAP7_RESULTS)/smem_bank/base/smem_bank.lef
export ADDITIONAL_LIBS = $(ASAP7_RESULTS)/smem_bank/base/smem_bank_typ.lib
export ADDITIONAL_GDS  = $(ASAP7_RESULTS)/smem_bank/base/6_final.gds

# 16 smem_bank macros (60 × 80 µm each) in an explicit 8-col × 2-row
# grid. The per-bank output-gating reduces the bank_rdata wire density
# that previously needed wide channels — modest channels suffice.
# DIE/CORE values come from gen_smem_floorplan.py.
export FLOORPLAN_DEF =
export DIE_AREA  = 0 0 750 300
export CORE_AREA = 20 20 730 280
export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/smem.macro_placement.tcl

export MACRO_PLACE_HALO    = 5 5
export MACRO_PLACE_CHANNEL = 10 10

# Custom PDN that welds parent stripes to smem_bank M6 power pins.
# ORFS default BLOCKS_grid_strategy doesn't include macro_grid welding,
# which leaves smem_bank power pins floating (PSM-0069 / PDN-0233).
export PDN_TCL = /work/tech/asap7/orfs/smem.pdn.tcl

# Keep yosys from inlining the 16 smem_bank wrappers — each is its own
# hardened LEF and the macro placer needs them as discrete instances.
export SYNTH_HIERARCHICAL = 1

export PLACE_DENSITY     = 0.65

# smem.sv carries a verilator-public 16×128×32 = 64 Kib `bank_mem` shadow
# array for cocotb backdoor reads. It has no synthesizable readers, so
# yosys' opt pass should DCE it — but yosys' `memory` pass runs first and
# triggers SYNTH_MEMORY_MAX_BITS before opt gets a chance to remove it.
# Raise the cap so the check passes; the array still gets eliminated
# downstream.
export SYNTH_MEMORY_MAX_BITS = 262144

export SKIP_LAST_GASP ?= 1

# B2: CTS-stage convergence aid, NOT a final-slack compromise. At CTS,
# repair_timing plateaus at hold WNS ~-340 ps and won't converge; this
# -400 ps margin lets CTS exit promptly. It does NOT appear in final
# slack: at the 2500 ps SDC, post-route repair closes hold to +58 ps
# (the relaxed clock gives the resizer setup headroom to insert hold
# buffers). Removing the margin entirely is untested — it may reintroduce
# the CTS churn — so it's kept as a fast-exit aid. See
# tech/asap7/problems/B2_smem_hold_timing.md.
export HOLD_SLACK_MARGIN = -400
