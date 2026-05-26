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

## Hold-timing limitation of hierarchical hardening

When a parent design (compute_array) instantiates hardened-leaf macros
(mac_tmem_cell, skew_lane, cmd_unit), hold violations on macro-to-macro
paths cannot be repaired by ORFS's resizer. The disease:

- Top-level CTS only routes clock to each macro's CLK pin
- Each macro has its OWN internal clock tree from leaf hardening
- Internal clock latency varies between leaves (depends on their CTS
  cluster diameter, buffer chain depth)
- A macro-to-macro signal path's hold timing depends on the difference
  in *internal* clock arrival, which the parent flow doesn't control

Symptom: `RSZ-0060 Max buffer count reached` during CTS repair_timing.
The resizer inserts thousands of hold buffers on short macro-to-macro
wires but the worst-path WNS doesn't move — buffers added to non-worst
paths only. We reproduced this on a 4×4 compute_array (`compute_array_tiny`)
where total wire length is <500 µm, confirming it isn't wire delay.

Current workaround: `HOLD_SLACK_MARGIN = -200` in
`compute_array_tiny_bcast0.config.mk`. Tells `repair_timing` to terminate
when hold violations are within 200 ps of zero instead of fighting to
close them. The flow completes; the GDS is physically valid but ships
~200 ps of negative hold slack — broadcast paths from `cmd_unit` to
`skew_lane` would race ahead of the receiving clock edge on real
silicon. Acceptable for renderable-GDS work in this repo; **not** for
tape-out.

An older variant (`compute_array_clk1000.config.mk`) still uses the
heavier `SKIP_CTS_REPAIR_TIMING=1` sledgehammer; the user has rejected
that approach for tape-out work, so prefer `HOLD_SLACK_MARGIN` for any
new variant.

What would actually fix this (see `tech/asap7/problems/A2_hold_timing_rtl.md`
for the active problem spec):

- **RTL pipeline** between `cmd_unit` and the `skew_lane` macros in
  `compute_array.sv`. Adds 1 cycle of issue latency; turns hold paths
  from macro-to-macro combinational into flop-to-flop with a full cycle
  of breathing room. Cleanest in-repo path.
- **Hierarchical CTS methodology** with leaf-level
  `set_clock_source_latency` matched across all leaves + top-level CTS
  compensation. ORFS doesn't automate this — typically a commercial-tool
  (Synopsys ICC2, Cadence Innovus) capability.
- **Flatten the hierarchy** — let ORFS do flat CTS over all 50K+ stdcells.
  Loses all the value of hierarchical hardening (long ABC, long route).
- **Re-harden leaves with output flops** to absorb the hold time, then
  let parent route their now-relaxed-timing outputs. Substantial RTL work
  on every leaf.

For now, the workaround stays in place; the active fix is tracked in A2.

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
├── PDK_GAPS.md                     what asap7 ships without (antenna, LVS, diode, ...)
├── render_layout.py                klayout PNG renderer (DEF or GDS)
├── problems/                       problem specs for outstanding work (A1..A6)
│   ├── A1_pdn_macro_grid.md
│   ├── A2_hold_timing_rtl.md
│   ├── A3_lvs.md
│   ├── A4_antenna.md
│   ├── A5_ir_drop.md
│   └── A6_chip_top.md
└── orfs/
    ├── run.sh                      driver: docker → openroad/orfs (build flow)
    ├── antenna_check.sh            post-route antenna sign-off (A4)
    ├── ir_drop.sh                  post-route IR-drop sign-off (A5)
    ├── <module>.config.mk          one per module (mac_tmem_cell, compute_array, ...)
    ├── <module>.sdc                clock + IO constraints
    ├── compute_array.pdn.tcl       custom channel-aligned PDN
    ├── <module>.macro_placement.tcl  place_macro lines (auto-gen; also _tiny + smem variants)
    ├── <module>.floorplan_preview.png  matplotlib preview (auto-gen, same variants)
    ├── asap7_antenna_overlay.lef   predictive antenna rules (A4 overlay mode)
    ├── noop_tapcell.tcl            suppresses tap cells inside macro channels
    └── scripts/
        ├── gen_compute_array_floorplan.py  emit placement.tcl + preview.png
        ├── gen_smem_floorplan.py           same, for smem
        ├── rewrite_abstract_lef.tcl        bloated abstract LEF
        ├── strip_lef_obs_layers.py         post-strip M1/M2/M5/M6/M7 OBS
        ├── render_odb.sh                   any ODB → PNG via klayout
        ├── verify_macro_power.tcl          parent-PDN ↔ macro-pin connectivity check
        ├── antenna_check.tcl               OpenROAD-side antenna-check driver
        ├── inject_antenna_gate_area.py     LEF patcher used by antenna overlay mode
        ├── ir_drop.tcl                     OpenROAD-side IR-drop driver
        ├── _ir_drop_env.mk                 ORFS env probe (include-only make file)
        ├── ir_drop_postprocess.py          IR-drop CSV → sign-off report
        └── tests/
            ├── test_inject_antenna_gate_area.py
            └── test_ir_drop_postprocess.py
```

Build artifacts (all gitignored, all under `build/`):
- `build/sv2v/<module>.v` — sv2v output (shared with sky130)
- `build/orfs/results/asap7/<module>/base/6_final.gds` — final layout
- `build/orfs/results/asap7/<module>/base/<module>.lef` — for hierarchy
- `build/orfs/results/asap7/<module>/base/<module>_typ.lib` — for hierarchy
- `build/render/<module>_asap7.png` — visual preview

## Known issues / TODO toward tape-out

State as of 2026-05-25. Ordered by severity. Items marked **BLOCKER** would
ship broken silicon if left unaddressed.

### Hard blockers (silicon would not function)

- [ ] **BLOCKER: PSM-0069 floating macro power pins.** `pdngen` in
      `compute_array.pdn.tcl` has only a `top` grid + parent-stripe-to-stripe
      connect rules. It does *not* have a `-macro` grid telling pdngen to
      weld parent stripes to leaf-macro VDD/VSS pins. Result on
      `compute_array_tiny_bcast0`: 264 macro power pins, **0** with any
      overlapping parent power-net shape (verified by
      `scripts/verify_macro_power.tcl`). Silicon would have ~264 floating
      VDD/VSS instances per tiny → brown-out. **Fix:** add
      `define_pdn_grid -macro` + `add_pdn_connect -grid macro_grid -layers
      {M5 M6}` and `{M6 M7}` to `compute_array.pdn.tcl`. Stripe pitch may
      need adjusting so vias actually land on the 5.4 µm-pitch leaf pin
      rows. Re-run tiny, confirm `verify_macro_power.tcl` exits 0.

- [ ] **BLOCKER: ~200 ps negative hold slack accepted as workaround.**
      `compute_array_tiny_bcast0.config.mk` sets `HOLD_SLACK_MARGIN=-200`,
      which tells `repair_timing` to terminate hold-fix when violations are
      within 200 ps (instead of fixing them). The violations remain in the
      design. On real silicon, broadcast paths from `cmd_unit` to
      `skew_lane` macros would race ahead of receiving clock edges and
      capture wrong values. **Fix candidates:** (a) insert an RTL pipeline
      flop in `compute_array.sv` between `cmd_unit` and the `skew_lane`
      macros (cleanest; +1 cycle latency); (b) re-harden leaves with
      matched `set_clock_latency` so internal CTS arrival aligns; (c)
      commercial CTS (CCOpt / ICC2). See "Hold-timing limitation" section
      above for the analysis.

### Integration gaps (chip is not fully assembled)

- [ ] **chip_top not yet integrated.** No `chip_top.config.mk`, no SDC, no
      floorplan, no IO ring. The full system (compute_array + smem +
      tile_buf + cmdproc + load + store + barrier + reset_seq) has only
      been built as separate hardened blocks. PSM-0069 will recur at
      chip_top once it's added — same macro-grid fix applies.

- [ ] **No IO pads / pad ring.** The ORFS asap7 platform doesn't ship pad
      cells. Tape-out needs pads (or a wafer-level format without them).

### Sign-off gaps (chip might not be verifiable as correct)

- [ ] **No LVS.** Magic + netgen with asap7 is not set up. Currently no
      schematic-vs-layout equivalence at any level.
- [~] **Antenna sign-off — tooling shipped, PDK gap blocks.**
      `tech/asap7/orfs/antenna_check.sh <module>` invokes OpenROAD's
      `check_antennas` against the routed ODB and writes a per-module
      report. ORFS's `repair_antennas` is already integrated into
      `global_route.tcl` and `detail_route.tcl` (defaults
      `SKIP_ANTENNA_REPAIR*=0`), so any fix-up has already happened by
      the time the check runs. **However**, the asap7 platform LEF has
      *zero* antenna properties (no `ANTENNAGATEAREA` on stdcell pins,
      no `ANTENNAAREARATIO` on M1..M9), so the check has nothing to
      evaluate. `antenna_check.sh` distinguishes "clean" from "vacuous
      pass" via exit code 4. See `tech/asap7/PDK_GAPS.md` for the data
      that'd need to be added.
- [~] **IR-drop sign-off — tooling shipped, blocked on PDN.**
      `tech/asap7/orfs/ir_drop.sh <module>` runs psm (analyze_power_grid)
      post-route with a documented activity factor (default 0.10) and
      reports worst-case Vdrop vs 10% of VDD. Exit 0=PASS, 1=FAIL
      (Vdrop > budget), 2=BLOCKED (PSM-0069), 3=tool/env failure. Leaf
      `mac_tmem_cell` passes (2.3 mV / 70 mV budget).
      `compute_array_tiny_bcast0` is BLOCKED on the A1 PDN bug; chip_top
      doesn't yet exist (A6). Unblocks once PSM-0069 is fixed.

### Fundamental constraint (outside this repo's reach)

- **asap7 is not a real foundry PDK.** It is an academic predictive PDK
  from ASU; there is no fab process behind it and no GDSII-to-silicon
  path. Anything built here is *representative* of 7 nm geometry/timing
  but cannot be physically taped out. For literal tape-out, switch to a
  real PDK (under NDA: TSMC N7/N6, GF12LP+; or open: SkyWater 130 — but
  not 7 nm in that case). ORFS support for real foundry PDKs is limited.

### Verification tooling that exists

- [x] `scripts/verify_macro_power.tcl` — walks post-route ODB, flags any
      macro VDD/VSS pin with no overlapping parent power-net shape. Use
      after every parent-level run: `openroad -exit -db <path>/6_final.odb
      scripts/verify_macro_power.tcl`. Exit 0 = clean, exit 1 = real PDN
      bug. Caught PSM-0069 as a real bug rather than tool artifact.
- [x] `antenna_check.sh` — one-command antenna sign-off for an
      ORFS-routed module. Reads tech + stdcell + macro LEFs and the
      post-route ODB, runs `check_antennas`, writes
      `build/orfs/reports/asap7/<module>/base/antenna.log`. Exit 0 / 2 / 4
      mean clean / violations / vacuous (no PDK rules). See
      `tech/asap7/PDK_GAPS.md`.
- [x] `orfs/ir_drop.sh <module> [--budget F] [--activity A]` — static
      IR-drop sign-off via psm. Loads `6_final.odb` + `.spef`, runs
      `report_power` with `set_power_activity -global -activity 0.10`
      (default), then `analyze_power_grid -voltage_file -error_file` for
      both nets at the asap7 typical corner (VDD=0.70 V). Outputs
      `build/orfs/reports/asap7/<module>/base/{ir_drop.log,VDD_voltage.csv,VSS_voltage.csv,VDD_error.rpt,VSS_error.rpt}`.
      The `.log` ends with a one-line `SUMMARY:` for grepping. Failing
      instances (above budget) are listed by name + (x,y) + layer.
      Activity factor and supply voltage are documented in every report.

### Sign-off tool exit-code conventions

Each sign-off tool defines its own exit-code contract. The codes overlap
numerically but encode different distinctions per tool — a future
aggregator that runs several tools needs a per-tool mapping table to
arrive at a single tape-out verdict.

| exit | `verify_macro_power.tcl` | `antenna_check.sh`        | `ir_drop.sh`           |
|------|--------------------------|---------------------------|------------------------|
| 0    | CLEAN                    | CLEAN                     | PASS                   |
| 1    | (real PDN bug)           | usage / artifact missing  | FAIL (Vdrop > budget)  |
| 2    | —                        | VIOLATIONS                | BLOCKED (PSM-0069)     |
| 3    | —                        | openroad invocation failed| tool / env failure     |
| 4    | —                        | VACUOUS PASS (no PDK rules)| —                     |

Why they differ (not a bug, but worth knowing):

- `antenna_check.sh` has the extra **VACUOUS PASS** state because the
  asap7 platform LEF ships with zero antenna properties; "0 violations"
  is meaningless without rules and must be distinguished from a real
  clean. There is no FAIL/BLOCKED distinction because the check itself
  cannot tell those apart — any non-zero violation count is FAIL.
- `ir_drop.sh` distinguishes **FAIL** (Vdrop computed and over budget,
  fix the PDN density) from **BLOCKED** (psm couldn't compute Vdrop
  because the grid is disconnected via PSM-0069, fix the PDN
  *connectivity* — see A1). The remediation paths are different so the
  exit codes have to be.

Treat **exit 0 = green** as the only cross-tool invariant. Anything
non-zero needs per-tool interpretation. When a tape-out aggregator
script exists, the mapping above is its source of truth.
