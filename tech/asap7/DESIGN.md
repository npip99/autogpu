# asap7 ORFS design constraints

What was learned hardening this repo on asap7 with OpenROAD-flow-scripts.
Read before designing a new module or changing the hierarchy.

## Toolchain

- **Flow**: ORFS (`openroad/orfs:latest` Docker image), invoked from
  `tech/asap7/orfs/run.sh`. OpenLane was tried first; it choked on asap7
  detailed routing (DRT-0085 access patterns) and KLayout streamout
  (empty `.map` file). ORFS handles asap7 natively.
- **PDK**: vendored at `~/.volare/asap7` (asap7sc7p5t_rvt 7.5-track,
  9-metal stack M1..M9).
- **GDS finishing**: KLayout, not Magic — the asap7 PDK in `orfs:latest`
  has no `.magicrc`, so Magic-based steps must be skipped.
- **RTL → Verilog**: shared with sky130 via `tech/sky130/Makefile`'s
  `sv2v` target, output to `build/sv2v/chip_top.v`.

## Hierarchy and layer discipline

The current asap7 setup is a 1-level hierarchy: 4 leaf macros
(`mac_tmem_cell`, `skew_lane_a`, `skew_lane_b`, `cmd_unit`) wrapped
inside `compute_array`. Layer-stack split:

- Leaves: M1..M5 internal routing + M5/M6 internal PDN stripes
- Parent (`compute_array`): M6/M7 PDN stripes, signal routing M2..M7

This **does not scale to arbitrary depths** — asap7 has 9 metals, so
the current stacking caps us at 3 levels (leaf → midlevel → chip_top).

### To unlock arbitrary depth: channel-only routing

Drop the layer-stack discipline; rule becomes "no level ever routes
over a macro at any layer." Every parent's PDN + signal routing happens
exclusively in the inter-macro channels. Then every level can reuse the
same layers. Sketch of what to change:

1. Every module's `*.pdn.tcl` defines stripes at the pitch+offset of
   ITS macro grid's channels (already true in `compute_array.pdn.tcl`).
2. LEFs go back to full bloated obstructions — no `strip_lef_obs_layers.py`
   post-step. Parent must respect the OBS as no-route zones.
3. Drop `MAX_ROUTING_LAYER` caps on per-module configs.

Not done because the current 1-level setup works; ship later when chip_top
needs depth.

## PDN

### Why our custom `compute_array.pdn.tcl` exists

ORFS's `BLOCKS_grid_strategy.tcl` (asap7 default for designs with macros)
lays M5/M6 stripes flat across the die and **clips every stripe against
every macro halo**. That's O(stripes × macros). On compute_array's 1089
macros + ~1000 stripes, it ran for >20 minutes and ate 4.5 GB before we
killed it.

Our PDN aligns its stripe pitch+offset to the inter-macro channel
centers (pitch = 45 µm matching mac grid, offset = channel center). pdngen
never sees a macro to clip against, so the slow path is gone. PDN
completes in seconds.

### Abstract LEF: bloat-then-strip dance

Conflicting requirements:

- **Parent's GRT pin-access analyzer** needs realistic obstructions on
  the leaf's internal routing layers (M1..M5). If layers look free in the
  LEF but are occupied internally, GRT picks bad access points and bombs
  with `[DRT-0073] No access point for ...`.
- **Parent's PDN** needs M6/M7 clear over the macro so power stripes can
  route through. Otherwise `[PDN-0006] VSS blocked by M6/M7 obstructions`.

`write_abstract_lef` only offers `-bloat_occupied_layers` (all-or-nothing,
gives both signal AND PDN obstructions) or no bloat (no obstructions at
all, signal pins become unfindable). Neither alone works.

Two-step in `run.sh`:
1. `write_abstract_lef -bloat_occupied_layers` — gets bloated OBS on
   every layer the leaf used (M1..M5 from signal routing, M6 from internal
   PDN stripes).
2. `scripts/strip_lef_obs_layers.py LEF M6 M7` — post-processes the LEF
   to delete the M6/M7 OBS blocks, leaving only M1..M5 obstructed.

## Cell exclusions

ASAP7 has known-bad cells for OpenROAD's pin-access analyzer. Excluded
in every leaf config:

- `*xp25*`, `*xp33*`, `*xp5*`, `*xp67*`, `*xp75*` — sub-1x drive
  variants with pin geometries DRT can't route.
- `AO*`, `OA*` — combo AND-OR/OR-AND cells; same issue at x1/x2
  drives, yosys decomposes to NAND/NOR fallback.

If a new module synthesizes badly, check `EXTRA_EXCLUDED_CELLS`
in its `config.mk`.

## SRAM modules: blocked

Three modules use `sram_1rw`: `smem`, `store`, `tile_buf_8row`. The
shared sv2v output is built with `-D USE_SKY130_MACRO` (selects the
sky130 SRAM macro). There's no asap7 SRAM macro — these modules can't be
hardened until either:

1. We add an asap7-specific sv2v target that drops `USE_SKY130_MACRO`
   (gives a behavioral FF fallback — large but synthesizable), or
2. We wrap an external SRAM compiler's asap7 output.

## Clock period vs RC budget

asap7 transistors are 7nm, but **wire RC doesn't shrink proportionally**.
On long broadcasts, wire delay dominates the clock period budget. Rules
of thumb on asap7 M5/M6 from this work:

- ~500-800 ps per mm of long-wire delay
- Each buffer stage in a clock or signal tree: ~50-100 ps
- Each cell delay: ~20-50 ps

`compute_array` at 1 GHz (1 ns period) had a -1.22 ns violation on
`u_cmd/rd_b_data[12]` — a global broadcast from `cmd_unit` in the SW
corner to the 1500 µm-distant east mac column. ORFS's resizer cannot
fix wire delay; it can only add buffers (which add more delay).

Fixes when this happens:

1. **Relax `clk_period` in the module's SDC.** Quickest. The physical
   layout is identical; only timing analysis cares. Currently
   `compute_array.sdc` is at 2500 ps (400 MHz) to give comfortable slack.
2. **Center the broadcaster.** Move `cmd_unit` from a corner to the
   array center — halves max wire distance.
3. **Pipeline the broadcast bus.** RTL change, adds 1 cycle latency.
4. **Predict it before synth.** Bus to N receivers across L µm of die
   has min period ≈ wire_delay(L) + ceil(log2(N)) * buffer_delay.

## ORFS knobs we override

Per-module `config.mk` overrides, with rationale (see Layer discipline +
PDN sections above):

- `EXTRA_EXCLUDED_CELLS` — drop xp* and AO/OA variants
- `GPL_CELL_PADDING`, `DPL_CELL_PADDING` — looser than asap7 defaults
- `KLAYOUT_DEF_LAYER_MAP` — *only* needed if using OpenLane; ORFS uses
  klayout's tech-bound map
- `MACRO_PLACEMENT_TCL` — explicit `place_macro` lines (auto-gen by
  `scripts/gen_compute_array_floorplan.py`)
- `PDN_TCL` — our custom channel-aligned PDN
- `LEC_CHECK=0` — kepler-formal LEC is exponential on this size, skipped
- `SKIP_LAST_GASP` — saves ~10 min, doesn't affect final GDS

## File map

```
tech/asap7/
├── DESIGN.md                       (you are here)
├── render_layout.py                klayout PNG renderer (DEF or GDS)
└── orfs/
    ├── run.sh                      driver: docker → openroad/orfs
    ├── <module>.config.mk          one per module (mac_tmem_cell, compute_array, ...)
    ├── <module>.sdc                clock + IO constraints
    ├── compute_array.pdn.tcl       custom channel-aligned PDN
    ├── compute_array.macro_placement.tcl   1089 place_macro lines (auto-gen)
    ├── compute_array.floorplan_preview.png matplotlib preview (auto-gen)
    └── scripts/
        ├── gen_compute_array_floorplan.py  emit placement.tcl + preview.png
        ├── rewrite_abstract_lef.tcl        bloated abstract LEF
        ├── strip_lef_obs_layers.py         post-strip M6/M7 OBS
        └── render_odb.sh                   any ODB → PNG via klayout
```

Build artifacts (all gitignored, all under `build/`):
- `build/sv2v/<module>.v` — sv2v output (shared with sky130)
- `build/orfs/results/asap7/<module>/base/6_final.gds` — final layout
- `build/orfs/results/asap7/<module>/base/<module>.lef` — for hierarchy
- `build/orfs/results/asap7/<module>/base/<module>_typ.lib` — for hierarchy
- `build/render/<module>_asap7.png` — visual preview
