# asap7 ORFS design constraints

What was learned hardening this repo on asap7 with OpenROAD-flow-scripts.
Read before designing a new module or changing the hierarchy.

> **Maintaining this doc.** Update DESIGN.md in the same PR whenever you:
> add or remove a script under `tech/asap7/`, change a workaround
> (e.g. swap `SKIP_CTS_REPAIR_TIMING` for `HOLD_SLACK_MARGIN`), add or
> retire an ORFS-knob override, ship a new sign-off tool, or add /
> resolve a `problems/` entry. The File map and "Known issues / TODO
> toward tape-out" sections rot fastest; check those first. A stale
> DESIGN.md sends future-you (and reviewers) chasing patterns that no
> longer exist.

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

Historic workarounds (now superseded by the A2 fix below — kept for
contributors hitting the same shape on a different design):

- `HOLD_SLACK_MARGIN = -200`: tells `repair_timing` to terminate when
  hold violations are within 200 ps of zero. Flow completes but ships
  ~200 ps of negative hold slack; chip would race on silicon. Was the
  interim mitigation on `compute_array_tiny_bcast0` until A2.
- `SKIP_CTS_REPAIR_TIMING = 1`: the heavier sledgehammer
  `compute_array_clk1000.config.mk` still uses for sweep experiments.
  Skips hold-fix entirely. The user has rejected this for tape-out
  work; prefer the structural fix below for any new design.

Alternatives that would fix this structurally on other designs (the A2
fix is the **RTL pipeline** option; the rest are listed for completeness):

- **RTL pipeline** between `cmd_unit` and `skew_lane` macros in
  `compute_array.sv`. Adds 1 cycle of issue latency; turns hold paths
  from macro-to-macro combinational into flop-to-flop with a full cycle
  of breathing room. **This is the A2 fix — see next subsection.**
- **Hierarchical CTS methodology** with leaf-level
  `set_clock_source_latency` matched across all leaves + top-level CTS
  compensation. ORFS doesn't automate this — typically a commercial-tool
  (Synopsys ICC2, Cadence Innovus) capability.
- **Flatten the hierarchy** — let ORFS do flat CTS over all 50K+ stdcells.
  Loses all the value of hierarchical hardening (long ABC, long route).
- **Re-harden leaves with output flops** to absorb the hold time, then
  let parent route their now-relaxed-timing outputs. Substantial RTL work
  on every leaf.

### Mitigation in production: `BCAST_PIPE=1` + 2500 ps clock

`compute_array.sv` accepts `BCAST_PIPE=N` (set via sv2v `-D BCAST_PIPE=N`).
With N>0, the parent inserts N D-FF stages on every cmd_unit → cells/
skew_lanes broadcast (push_*, drain_en/slot, scrub_en) AND N symmetric
stages on every cmd_unit → chip-external completion signal (mma_busy/done,
arrive_*, drain_busy/done/row_valid/row_idx/row_last). `rd_*_en/addr`
are intentionally NOT piped (SMEM is at the chip boundary). The symmetric
output pipe is what makes the cocotb cycle-by-cycle lockstep pass —
`pymodel/compute_array.py` takes `bcast_pipe=` and models the matching
shift registers.

`compute_array_tiny_bcast0.config.mk` now uses BCAST_PIPE=1 and the
SDC sits at 2500 ps (400 MHz, same as full `compute_array.sdc`). Post-
route timing: **0 hold violations, 0 setup violations, fmax 440 MHz**.
`HOLD_SLACK_MARGIN` is no longer needed — repair_timing converges to
zero cleanly. See `tech/asap7/problems/A2_hold_timing_rtl.md` for the
journey and `compute_array_tiny_slow*.config.mk` for the alternatives
that were tried (CTS clustering tweaks, reversed useful-skew SDC,
BCAST_PIPE=2 — none beat the simplest config).

The three alternative-fix options listed above (commercial CTS,
flatten hierarchy, re-harden leaves with output flops) remain on the
table for designs where 2500 ps isn't an acceptable clock target.

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
    ├── lvs.sh                      post-route cell-instance LVS (A3)
    ├── <module>.config.mk          one per module (mac_tmem_cell, compute_array,
    │                               cmdproc, smem, chip_top, …)
    ├── <module>.sdc                clock + IO constraints
    ├── compute_array.pdn.tcl       custom channel-aligned PDN
    ├── chip_top.pdn.tcl            simple grid + M1/M2 followpins (A6).
    │                               Inherits A1's -macro grid pattern.
    ├── <module>.macro_placement.tcl  place_macro lines (auto-gen; tiny + smem + chip_top variants)
    ├── <module>.floorplan_preview.png  matplotlib preview (auto-gen, same variants)
    ├── asap7_antenna_overlay.lef   predictive antenna rules (A4 overlay mode)
    ├── noop_tapcell.tcl            suppresses tap cells inside macro channels
    └── scripts/
        ├── gen_compute_array_floorplan.py  emit placement.tcl + preview.png
        ├── gen_smem_floorplan.py           same, for smem
        ├── gen_chip_top_floorplan.py       same, for chip_top (A6)
        ├── gen_lef_from_odb.tcl            re-emit LEF + LIB from an
        ├── gen_lef.sh                      existing 6_final.odb without
        │                                   re-running the full flow (A6).
        │                                   Used in A6 to harvest LEFs
        │                                   for cmdproc/load/barrier/
        │                                   reset_seq/compute_array_tiny.
        ├── rewrite_abstract_lef.tcl        bloated abstract LEF
        ├── strip_lef_obs_layers.py         post-strip M1/M2/M5/M6/M7 OBS
        ├── render_odb.sh                   any ODB → PNG via klayout
        ├── verify_macro_power.tcl          parent-PDN ↔ macro-pin connectivity check
        ├── antenna_check.tcl               OpenROAD-side antenna-check driver
        ├── inject_antenna_gate_area.py     LEF patcher used by antenna overlay mode
        ├── ir_drop.tcl                     OpenROAD-side IR-drop driver
        ├── _ir_drop_env.mk                 ORFS env probe (include-only make file)
        ├── ir_drop_postprocess.py          IR-drop CSV → sign-off report
        ├── lvs.py                          KLayout cell-instance LVS impl
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

- [x] **RESOLVED (A1): PSM-0069 floating macro power pins.** Fixed by
      adding a `define_pdn_grid -macro` + `add_pdn_connect -grid
      macro_grid -layers {M5 M6}` and `{M6 M7}` to
      `compute_array.pdn.tcl`. Post-fix: `verify_macro_power.tcl`
      reports `ok=264 fail=0` on `compute_array_tiny_bcast0` and
      `ok=12752 fail=0` on the full 32×32 `compute_array` post-PDN ODB.
      See `tech/asap7/problems/A1_pdn_macro_grid.md`.

- [x] **RESOLVED (A2, 2026-05-25): ~200 ps negative hold slack.**
      `compute_array_tiny_bcast0.config.mk` no longer sets
      `HOLD_SLACK_MARGIN`. Fix taken: option (a) above — `compute_array.sv`
      now has a `BCAST_PIPE=1` parent-level pipeline stage on every
      cmd_unit → skew_lane / mac_tmem_cell broadcast (with a matching
      output pipe on the chip-external completion signals so cocotb
      cycle-by-cycle lockstep still passes; pymodel models the same
      latency). Combined with relaxing the SDC from 1 GHz → 400 MHz,
      post-route timing is 0 hold violations, 0 setup violations, fmax
      440 MHz. See `tech/asap7/problems/A2_hold_timing_rtl.md`.

### Integration gaps (chip is not fully assembled)

- [x] **chip_top integrated as first-pass (A6).** Lands
      `chip_top.config.mk` + `.sdc` + `.pdn.tcl` +
      `scripts/gen_chip_top_floorplan.py` + macro_placement.tcl. Uses
      `compute_array_tiny_bcast0` (4×4 mac grid) as the compute_array
      black-box, with chip_top synthesized at MMA_M=MMA_N=MMA_K=4.
      cmdproc, load, barrier, reset_seq, and the compute_array variant
      are hardened LEFs; smem is inlined → 32 fakeram7_256x32 banks;
      store is inlined → flat FF logic. Die: ~750 × 800 µm. Still open:
      full-size (MMA_M=32) chip_top — with A1 + A2 closed in master,
      the full 32×32 `compute_array` can now harden; bumping `MMA_DIM=32`
      and swapping the compute_array LEF reference is the only delta.
      Currently stuck in detail route iterations on smem's bank_rdata
      mux congestion (see caveats below). See `tech/asap7/problems/A6_chip_top.md`.

- [ ] **No IO pads / pad ring.** The ORFS asap7 platform doesn't ship pad
      cells. Tape-out needs pads (or a wafer-level format without them).

### Sign-off gaps (chip might not be verifiable as correct)

- [~] **LVS — cell-instance level shipped, transistor-level blocked.**
      `tech/asap7/orfs/lvs.sh <module>` runs a KLayout-based equivalence
      check on the post-route GDS vs. `6_final.v` for any hardened
      block, treating standard cells and hardened macros as black-box
      subcircuits. See "LVS infrastructure" below for what it catches
      (and what the asap7 PDK gap — no transistor-level rule deck —
      prevents).
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
- [~] **IR-drop sign-off — tooling shipped, parent-level re-run pending.**
      `tech/asap7/orfs/ir_drop.sh <module>` runs psm (analyze_power_grid)
      post-route with a documented activity factor (default 0.10) and
      reports worst-case Vdrop vs 10% of VDD. Exit 0=PASS, 1=FAIL
      (Vdrop > budget), 2=BLOCKED (PSM-0069), 3=tool/env failure. Leaf
      `mac_tmem_cell` passes (2.3 mV / 70 mV budget). The previous
      A1-PDN-bug BLOCKED status on `compute_array_tiny_bcast0` is
      resolved (see Hard blockers above); re-run after a fresh
      compute_array route to capture the real Vdrop number. chip_top
      doesn't yet exist (A6).

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
- [x] `scripts/gen_lef_from_odb.tcl` + `gen_lef.sh` — re-emit a leaf's
      LEF + timing LIB from an existing `6_final.odb` without re-running
      the whole flow. Used in A6 to harvest LEFs for `cmdproc`, `load`,
      `barrier`, `reset_seq`, and `compute_array_tiny_bcast0` whose
      original runs reached layout but skipped or failed
      `generate_abstract`. Wraps `write_abstract_lef -bloat_occupied_layers`
      and `write_timing_model` with the asap7 standard cell + RVT_TT
      liberty files preloaded; then runs `strip_lef_obs_layers.py` to
      strip M1/M2/M5/M6/M7 OBS so parent stripes can pass over.

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
- [x] `orfs/lvs.sh` — cell-instance LVS. See "LVS infrastructure" below.

### Sign-off tool exit-code conventions

Each sign-off tool defines its own exit-code contract. The codes overlap
numerically but encode different distinctions per tool — a future
aggregator that runs several tools needs a per-tool mapping table to
arrive at a single tape-out verdict.

| exit | `verify_macro_power.tcl` | `antenna_check.sh`        | `ir_drop.sh`           | `lvs.sh`               |
|------|--------------------------|---------------------------|------------------------|------------------------|
| 0    | CLEAN                    | CLEAN                     | PASS                   | CLEAN                  |
| 1    | (real PDN bug)           | usage / artifact missing  | FAIL (Vdrop > budget)  | FAIL (mismatch)        |
| 2    | —                        | VIOLATIONS                | BLOCKED (PSM-0069)     | config / artifact error|
| 3    | —                        | openroad invocation failed| tool / env failure     | —                      |
| 4    | —                        | VACUOUS PASS (no PDK rules)| —                     | —                      |

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
- `lvs.sh` uses exit 2 for "config / artifact error" (missing GDS or
  Verilog input), not for a tape-out-relevant pass/fail state. Its
  pass/fail is binary at the cell-instance level: clean (0) or mismatch
  (1). The PDN power-pin check is folded into the mismatch verdict.

Treat **exit 0 = green** as the only cross-tool invariant. Anything
non-zero needs per-tool interpretation. When a tape-out aggregator
script exists, the mapping above is its source of truth.

## chip_top integration (A6)

First-pass `chip_top.config.mk` hardens a 4×4 (MMA_M=MMA_N=MMA_K=4)
chip_top against the `compute_array_tiny_bcast0` LEF. Once A1+A2 close
compute_array at 32×32 (now possible since both are merged on master),
bumping `MMA_DIM=32` and swapping the `compute_array_tiny_bcast0` →
`compute_array` LEF reference in chip_top.config.mk is the only delta.

### Why MMA=4 first-pass

When this PR was authored, `compute_array_tiny_bcast0` was the only
compute_array variant whose routing completed. The full 32×32
(`compute_array`) stopped at the CTS hold-repair runaway (RSZ-0060);
all `compute_array_bcastN` and `compute_array_clkN` variants stalled in
the same place. The tiny LEF has MMA_M=4 port widths (rd_a_data[31:0]
vs full's [255:0]), so chip_top must match. `chip_top.sv` got
`\`ifndef MMA_M / \`define MMA_M 32 / \`endif` guards (mirroring the
`compute_array.sv` pattern) plus explicit parameter passing on every
submodule instantiation, so `sv2v -D MMA_M=4` is sufficient to
specialize the whole netlist.

A1 (PDN macro_grid) + A2 (BCAST_PIPE=1 + 2500ps SDC) have since merged.
The full 32×32 compute_array should now harden cleanly. Once it does,
chip_top can run at full MMA=32.

### What chip_top consumes

| Submodule    | At chip_top                    | Source                                |
|--------------|--------------------------------|---------------------------------------|
| compute_array| hardened LEF (tiny)            | `compute_array_tiny_bcast0/base/`     |
| cmdproc      | hardened LEF                   | `cmdproc/base/` (via `gen_lef.sh`)    |
| load         | hardened LEF                   | `load/base/` (via `gen_lef.sh`)       |
| barrier      | hardened LEF                   | `barrier/base/` (via `gen_lef.sh`)    |
| reset_seq    | hardened LEF                   | `reset_seq/base/` (via `gen_lef.sh`)  |
| smem         | inlined → 32 fakeram macros    | `fakeram7_256x32` from asap7 platform |
| store        | inlined (FF logic only)        | RTL                                   |
| tile_buf_8row| inlined inside store           | RTL (LEF ROW_W mismatch at MMA=4)     |

### Known caveats at chip_top first-pass

1. **PSM-0069 fix applied** via A1's pattern: `chip_top.pdn.tcl` now
   ships a `define_pdn_grid -macro -name {macro_grid}` with
   `add_pdn_connect -layers {M5 M6}` and `{M6 M7}` so the parent
   stripes weld to every hardened-leaf VDD/VSS pin (compute_array +
   the 4 sub-block macros + 32 fakeram banks). Same mechanical fix as
   `compute_array.pdn.tcl`. Verify with
   `scripts/verify_macro_power.tcl` after a 6_final route.
2. **compute_array GDS exists post-A1.** `ADDITIONAL_GDS` now includes
   `compute_array_tiny_bcast0/base/6_final.gds`; `GDS_ALLOW_EMPTY` is
   tightened to just `fakeram.*` (those macros are LEF-only from the
   asap7 platform).
3. **HOLD_SLACK_MARGIN intentionally NOT set.** A2's structural fix
   (BCAST_PIPE=1 + 2500 ps SDC at compute_array) is baked into the
   compute_array LEF chip_top consumes, so chip_top doesn't inherit
   that hold-buffer runaway. If chip_top RE-introduces the same shape
   on its own broadcast nets (cmdproc → engines), pipeline at the RTL
   level — don't reach for `HOLD_SLACK_MARGIN`.
4. **No IO pads.** Top-level ports are die-edge pins. Pad ring is a
   separate follow-up.
5. **smem standalone is broken (GRT-0116 congestion).** chip_top inlines
   smem to dodge the broken LEF. Real fix: per-block smem follow-up to
   either retune its floorplan or fix the bank_rdata mux RTL.

### How to re-run

```
# After any compute_array (or other submodule) re-harden:
uv run python tech/asap7/orfs/scripts/gen_chip_top_floorplan.py
make -C tech/sky130 build/sv2v/chip_top_asap7_tiny.v   # or MMA_DIM=32
./tech/asap7/orfs/run.sh chip_top
```

## LVS infrastructure

`tech/asap7/orfs/lvs.sh <module>` runs a Layout-vs-Schematic check on a
hardened block. Outputs exit code 0 on clean, nonzero on mismatch, and
writes a report to `build/orfs/reports/asap7/<module>/base/lvs.log`.

### What it does

For a given module (e.g., `mac_tmem_cell`, `compute_array_tiny_bcast0`):

1. Reads `build/orfs/results/asap7/<module>/base/6_final.gds`.
2. Extracts a gate-level netlist from the GDS via KLayout's
   `LayoutToNetlist`. Standard cells (`*_ASAP7_75t_*`) and hardened
   macros are treated as black-box subcircuits — only the metal-stack
   routing M1..M9 + via stack V1..V8 is traced, no transistor-level
   extraction.
3. Reads `6_final.v` and parses it into a `pya.Netlist` via a custom
   structural-Verilog parser.
4. Reconciles known asymmetries (physical-only cells in GDS but not
   netlist, dangling CTS-load outputs, etc).
5. Compares with `pya.NetlistComparer`.

### What it catches

Two independent checks must both pass to report LVS clean:

1. **Structural cell-instance compare** (KLayout NetlistComparer)
    - Misplaced or missing cells (GDS cell count ≠ Verilog instance count).
    - Routing shorts and opens (two cell pins on the same M1 net where
      the netlist says they should be separate, or vice versa).
    - Pin swaps (a cell's `A` and `B` inputs wired the wrong way).
    - Mis-routed buses (one bit of a bus connected to the wrong
      destination).

2. **Macro power-pin connectivity check**
    - **Floating macro power pins** — the PSM-0069 failure mode. For
      each subcircuit instance, the script verifies its VDD/VSS pins
      land on a multi-fanout (global) net at the parent level. Any pin
      on a 1-fanout dangling net flags a PDN bug. (This check has to
      run *before* the structural simplify, which would otherwise purge
      the dangling nets and mask the issue.)

This sort of cleanly separates "did you wire the signal nets right?"
(structural) from "did the PDN actually deliver power to every macro?"
(PDN check).

### What it does NOT catch (asap7 PDK gap — explicit limitation)

- **Standard-cell-internal transistor-level bugs.** asap7 ships no
  production-grade LVS rule deck:
    - The volare-packaged asap7 has empty placeholder files at
      `~/.volare/asap7/libs.tech/{magic,netgen}/*`
    - `openroad/orfs:latest` does not have `magic` or `netgen` installed
    - The ORFS asap7 platform dir has only `drc/` (DRC rules from
      laurentc2), no `lvs/`
    - KLayout LVS is installed (we use its `LayoutToNetlist`) but no
      transistor-extraction rule deck exists for asap7 either
  Writing one from scratch for production tape-out would mean porting
  the asap7 LEF/GDS cell geometries into KLayout LVS DSL — a sizable
  task and ultimately moot, since asap7 is itself an academic predictive
  PDK with no fab path (see "Fundamental constraint" below).

- **Stdcell substitution attacks.** If a stdcell GDS were swapped with
  a malformed variant (same cell name, different transistor topology),
  cell-instance LVS wouldn't notice. Out of scope; trust the PDK.

### Hierarchy mode

Cell-instance hierarchical: standard cells are black-box leaves; hardened
macros (`mac_tmem_cell`, `skew_lane_a/b`, `cmd_unit` at compute_array
level) are also black-box. At the leaf level, the lvs.sh on each
hardened macro verifies that macro's own routing. At the parent level,
those macros become black boxes so we only check the parent's routing.
This is the same hierarchy mode used by commercial LVS flows for
top-down tape-out sign-off — each level checks only what's new at that
level.

### Reproduction

```bash
# After the synthesis flow has produced 6_final.gds / .v:
./tech/asap7/orfs/lvs.sh mac_tmem_cell                # PASS
./tech/asap7/orfs/lvs.sh skew_lane_a                  # PASS
./tech/asap7/orfs/lvs.sh skew_lane_b                  # PASS
./tech/asap7/orfs/lvs.sh cmd_unit                     # PASS
./tech/asap7/orfs/lvs.sh compute_array_tiny_bcast0    # PASS (post-A1)
```

Uses the same `openroad/orfs:latest` Docker image as the synthesis
flow; no additional tools required.

Note: pre-A1, the compute_array case failed with 31 macro VDD/VSS
power pins landing on dangling single-fanout nets — the LVS PDN check
independently triangulated PSM-0069 before A1 closed it. After the A1
`-macro` grid lands the LVS PDN check should report green and the
overall compute_array LVS should pass.

### Files

```
tech/asap7/orfs/
├── lvs.sh                          driver (this is what you invoke)
├── scripts/
│   └── lvs.py                      KLayout Python implementation
└── ...
build/orfs/reports/asap7/<module>/base/
├── lvs.log                         full LVS report
└── layout_netlist.cir              SPICE dump of the extracted netlist
                                    (handy for grep / manual inspection)
```
