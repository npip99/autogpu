# Phase B abutment harness: 4×4 of abutment-ready mac_tmem_cell_tile
# macros, placed touching with NO inter-macro channels. Tests whether the
# tile boundary spec (pin abutment-symmetry, M4/M5 edge pin tracks,
# followpin alignment) actually forms continuous nets when the tiles abut.
#
# Same RTL as the existing mac_array_small (4×4 grid of mac_tmem_cells,
# neighbor-to-neighbor wiring per the systolic flow), so the *RTL*
# expects neighbor pins to connect. If the abutment-ready tile is
# correct, the parent has zero inter-tile routing to do (signals connect
# by edge-touching metal) and zero PDN gen inside the array (followpins
# of one tile's last row line up with the next tile's first row).
#
# Failure modes this catches early (vs. waiting for the 32×32):
#   - Pin x/y mismatch between in/out of a pair → unconnected nets / LVS.
#   - Power rail misalignment → PSM-0069 floating macro power pins.
#   - Die-size off-by-grid → tiles don't tile cleanly.
#
# See tech/asap7/TILE_SPEC.md for the spec being validated.

export PLATFORM        = asap7
export DESIGN_NAME     = mac_array_small
export DESIGN_NICKNAME = mac_array_small_abut

export VERILOG_FILES = /work/build/sv2v/mac_array_small.v
export SDC_FILE      = /work/tech/asap7/orfs/mac_array_small_abut.sdc

# Die = 4×4 tile array (138.24 µm) + stdcell perimeter strip (~20 µm E+N)
# + 5 µm die margin. The stdcell strip is needed because MPL-0065 will
# fail if movable stdcells have nowhere to go — the array itself covers
# tiles only. CORE/DIE values land on-grid after ORFS's site/row snap
# (site = 0.054 µm H, row = 0.27 µm V):
#   - CORE min = (5.022, 5.13) — ORFS's snapped (5, 5) target.
#   - CORE max = (162.972, 163.08) — gives ~19.7 µm of stdcell space
#     between the array east/north edges (143.262, 143.37) and the
#     core boundary. On-grid (162.972/0.054 = 3018; 163.08/0.27 = 604).
#   - DIE: core + 5 µm margin.
export FLOORPLAN_DEF =
export DIE_AREA   = 0 0 250 250
export CORE_AREA  = 5.022 5.13 244.296 244.35

# Hardened-leaf macro: the abutment-ready mac_tmem_cell_tile (Phase A).
# The tile's DESIGN_NAME is mac_tmem_cell (same RTL), so the LEF/LIB/GDS
# below override the platform's stdcell mac_tmem_cell with the tile.
#
# IMPORTANT: use mac_tmem_cell_tile.lef (post-processed), NOT
# mac_tmem_cell.lef (raw write_abstract_lef output). The raw LEF marks
# OBS on M1..M6 across the full tile face, which blocks parent routing
# over the array. The post-processed version strips OBS down to {M3, M4}
# so the parent can use M1/M2/M5/M6 over the macro (M3/M4 still blocked
# where the actual internal routes and pins live).
ASAP7_RESULTS = /work/build/orfs/results/asap7
export ADDITIONAL_LEFS = $(ASAP7_RESULTS)/mac_tmem_cell_tile/base/mac_tmem_cell_tile.lef
export ADDITIONAL_LIBS = $(ASAP7_RESULTS)/mac_tmem_cell_tile/base/mac_tmem_cell_typ.lib
export ADDITIONAL_GDS  = $(ASAP7_RESULTS)/mac_tmem_cell_tile/base/6_final.gds

# Explicit 4×4 abutted placement (auto-gen below if it gets larger; 16
# entries fits inline). Tile size = 34.56 µm. Origin (lower-left) of
# tile (i, j) at (5 + j*34.56, 5 + i*34.56) — fills the core area.
export MACRO_PLACEMENT_TCL = /work/tech/asap7/orfs/mac_array_small_abut.macro_placement.tcl

# SYNTH_HIERARCHICAL keeps the 16 named instances visible for placement.
export SYNTH_HIERARCHICAL = 1

# Zero halo + zero channel: tiles must literally touch for abutment.
export MACRO_PLACE_HALO    = 0 0
export MACRO_PLACE_CHANNEL = 0 0

# Keep parent routing off the tile's interior layers — tiles use M1-M6,
# parent perimeter goes M6-M7.
export MAX_ROUTING_LAYER = M7

# Custom parent PDN — A1-shape (M6 stripes over the tiles + macro_grid
# M5-M6 connect rule) so parent power welds to each tile's M5 internal
# pins. Default would fail PDN-0232 (parent stripes don't overlap tile
# pin positions).
export PDN_TCL = /work/tech/asap7/orfs/mac_array_small_abut.pdn.tcl

export SKIP_LAST_GASP ?= 1
# Allow GRT congestion — Phase B is about validating abutment geometry,
# not optimal routing. Real fix is more routing space (perimeter wider)
# but for now let GRT keep going if it can't fully converge.
export GRT_ALLOW_CONGESTION = true
