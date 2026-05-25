export PLATFORM     = asap7
export DESIGN_NAME  = smem

# asap7-flavored sv2v output — sram_1rw instantiates fakeram7_256x32 under
# USE_ASAP7_FAKERAM (see mem/sram_1rw.sv and tech/sky130/Makefile target
# build/sv2v/smem_asap7.v).
export VERILOG_FILES = /work/build/sv2v/smem_asap7.v
export SDC_FILE      = /work/tech/asap7/orfs/smem.sdc

# ORFS-shipped FakeRAM macro (256 words × 32 bits, 1RW, active-high ce/we).
# Platform's config.mk sets GDS_ALLOW_EMPTY ?= fakeram.* so no GDS is
# required — the macro is a blackbox with LEF (placement abstract) + LIB
# (timing / interface). KLayout streamout fills it with the LEF stub.
#
# Inline the platform path rather than introducing a PLATFORM_DIR var here
# — that name is owned by ORFS's main Makefile and a local (non-exported)
# reassignment shadows it, breaking load.tcl which expects PLATFORM_DIR in
# the env.
export ADDITIONAL_LEFS = /OpenROAD-flow-scripts/flow/platforms/asap7/lef/fakeram7_256x32.lef
export ADDITIONAL_LIBS = /OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/fakeram7_256x32.lib

# 32 fakeram macros (8.36 × 42 µm each) in an explicit 8-col × 4-row grid.
# Auto-placement (rtl-mp) tried in a 350 × 700 die ran into unresolvable
# GRT congestion (>5 extra iterations) because the channels were too
# narrow for 32 bank-output buses to escape vertically. Explicit layout
# from scripts/gen_smem_floorplan.py uses 40 µm horizontal + 30 µm
# vertical channels, fits in 450 × 400 µm.
export FLOORPLAN_DEF =
export DIE_AREA  = 0 0 450 400
export CORE_AREA = 25 25 425 375
export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/smem.macro_placement.tcl

export MACRO_PLACE_HALO    = 5 5
export MACRO_PLACE_CHANNEL = 10 10

# Keep yosys from inlining the 32 sram_1rw wrappers — each wraps one
# fakeram macro and the macro placer needs them as discrete instances.
export SYNTH_HIERARCHICAL = 1

export PLACE_DENSITY     = 0.65

# smem.sv carries a verilator-public 32×128×32 = 128 Kib `bank_mem` shadow
# array for cocotb backdoor reads. It has no synthesizable readers, so
# yosys' opt pass should DCE it — but yosys' `memory` pass runs first and
# triggers SYNTH_MEMORY_MAX_BITS before opt gets a chance to remove it.
# Raise the cap above 131072 so the check passes; the array still gets
# eliminated downstream.
export SYNTH_MEMORY_MAX_BITS = 262144

export SKIP_LAST_GASP ?= 1
