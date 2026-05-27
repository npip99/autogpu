# smem_bank — one fakeram7_256x32 + per-output-dword gating logic.
#
# Hardened as a leaf macro so 32 instances inside smem.sv each carry
# their own internalized output gating. The 1024 bank_rdata wires that
# used to fan to a central mux are now consumed inside each bank;
# only the already-gated dword outputs leave the macro.
#
# Tiny block: 1 fakeram macro + ~50 stdcells of gating logic. Should
# harden in ~10 min.

export PLATFORM     = asap7
export DESIGN_NAME  = smem_bank

# sv2v output: just smem_bank.sv + sram_1rw.sv with USE_ASAP7_FAKERAM.
export VERILOG_FILES = /work/build/sv2v/smem_bank_asap7.v
export SDC_FILE      = /work/tech/asap7/orfs/smem_bank.sdc

# Single fakeram macro inside; pull in its LEF + LIB from the asap7
# platform.
ASAP7_PLAT = /OpenROAD-flow-scripts/flow/platforms/asap7
export ADDITIONAL_LEFS = $(ASAP7_PLAT)/lef/fakeram7_256x32.lef
export ADDITIONAL_LIBS = $(ASAP7_PLAT)/lib/NLDM/fakeram7_256x32.lib

# Die — fakeram is 8.36 × 42 µm; with ~50 stdcells of gating logic
# arranged on the side, ~60 × 80 µm leaves plenty of routing room.
export FLOORPLAN_DEF =
export DIE_AREA  = 0 0 60 80
export CORE_AREA = 5 5 55 75

# Explicit placement for the one fakeram (centered horizontally with
# the bank's perimeter pins on east/west edges).
export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/smem_bank.macro_placement.tcl

# Keep yosys from inlining the fakeram macro.
export SYNTH_HIERARCHICAL = 1

export MACRO_PLACE_HALO    = 2 2
export MACRO_PLACE_CHANNEL = 4 4

export SKIP_LAST_GASP ?= 1

# Skip LEC (the bank is small but yosys hierarchy preservation still
# blows kepler-formal sometimes).
export LEC_CHECK = 0
