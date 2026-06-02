# OpenLane / OpenROAD failures we've hit on this project

Quick lookup for the failure codes we keep seeing, with the actual root
causes we found and the fixes we applied. When a failure recurs, find the
matching entry rather than re-debugging from scratch.

**For a NEW failure not listed here, read `RCA_DISCIPLINE.md` FIRST.**
That file describes the never-guess diagnosis process — capture symptom,
perf-sample the dominant function, gdb-identify the specific stuck
object, form ONE testable hypothesis, design an experiment, apply
fix-vs-workaround explicitly. Every claim in a diagnosis must cite the
evidence on the same line.

For render/analysis-script bugs (hardcoded coords, matplotlib slowness,
heatmap CSV caveats) see `scripts/README.md` section 10.

---

## Routing

### GRT-0118 — global routing overflow not resolved

> error: `[ERROR GRT-0118] Routing congestion too high.`

Reached after `GRT_OVERFLOW_ITERS` iterations without driving the
overflow tile count to zero.

- **Actual root cause on this project (2026-05-22):** the resizer
  (`OpenROAD.ResizerTimingPostCTS`) inserts **thousands of hold-fix
  buffers** that the placer crams into the thin std-cell band against
  the receiving macro's pin face.

  For compute_array specifically:
  ```
  [RSZ-0046] Found 3988 endpoints with hold violations.
  [RSZ-0032] Inserted 10117 hold buffers.
  ```
  Cell census after resizer:
  | Cell                 | Count | Role                          |
  |---                   |---    |---                            |
  | `dlygate4sd3_1`      | 5,768 | **pure delay cells (hold fix)** |
  | `buf_12`             | 3,899 | signal buffer chains          |
  | `clkbuf_16`          | 3,592 | clock-tree + repeaters        |
  | `clkbuf_4/8/2`       | 5,112 | clock buffers                 |
  | `buf_4/6/8`          | 2,032 | signal buffers                |
  | `inv_12, clkinv_*`   | 1,054 | inverters                     |
  | **buffer-like total**| ~21,500 | (vs only 96 conb_1 tie cells) |

  All 781 overflow tiles confined to y=2594–2622 (28 µm strip just
  below/inside b-skew's south pin face). Top-30 offending nets are auto-
  generated names like `net2953` — each one is a single dlygate/buf in
  a delay chain whose load is a b-skew input (e.g. `.push_now(net2953)`
  on three b-skews). The strip below b-skew becomes a "hold-buffer
  graveyard": ~5,000+ cells × 2 met1 pin shapes each → tracks consumed →
  GR can't escape the strip.

  **Why hold violations are so many (3,988):** worst-hold clock skew is
  −0.966 ns; at the FF corner (fast, cold, high-V) every cmd_unit →
  b-skew combinational path that completes in <1 ns trips a hold check.
  The resizer's only lever is to insert delay cells on the data path.

- **The earlier hypothesis** (96 tie cells + push_now buffers as the
  main offenders) was correct in *kind* but wrong in *magnitude*. Tie
  cells are <1% of the cell pile. The dominant offender is the hold-fix
  buffer chain population.

- **Diagnostic checklist:**
  1. Dump the heatmap: `openroad -gui` then `gui::dump_heatmap "Routing"
     /path/to.csv`. Find rows where value > 100.
  2. Parse the GR guide; cross-reference offending tiles with the net
     names that pass through them. (`scripts/analyze_overflow.py`)
  3. Grep the resizer log for `RSZ-0032` (hold buffers inserted) and
     `RSZ-0046` (endpoints with hold violations). If these numbers are
     in the thousands, GRT-0118 is downstream of hold-fix pressure.
  4. Look up dominant net names in `compute_array.pnl.v` (post-CTS, not
     post-synth `.nl.v`). The `.pnl.v` has the resizer-inserted
     `wireNNNN` instances and their delay-cell types.
- **Fixes that actually attack the root cause:**
  - **Reduce the hold-violation count.** Pipeline cmd_unit → b-skew
    paths (add a register stage at cmd_unit outputs) so they're not
    combinational at all → hold check disappears for that boundary.
  - **Tighten the CTS skew bound.** Worst-hold skew −0.966 ns is huge.
    Smaller `CTS_TARGET_SKEW` (e.g. 100 ps) means fewer paths trip hold
    at FF corner → fewer delay cells needed.
  - **Re-harden b-skew taller.** A taller macro = pin face spread over
    more y = more vertical room for hold-fix cells.
- **Fixes that just push the problem around:**
  - Drop unneeded tie cells (saves 96 of ~21,500 cells in the strip —
    marginal but easy)
  - Widen the std-cell band (`CMD_HALO` ↑): placer still hugs the new
    pin face, just at a different y — see `cmd_halo` discussion in
    chat history.
  - `GRT_ALLOW_CONGESTION: true` — defers the problem to DR; works only
    if residual count is small (<~50 tiles).
- **Doesn't help:**
  - `GRT_OVERFLOW_ITERS` past ~200 (see GRT-0230)
  - Adding routing obstructions over the macro footprint (made things
    worse — parent had even less fly-over room)
  - Routing on more layers (RT_MAX_LAYER=met5 already used)

### GRT-0230 — congestion iterations cannot increase overflow

> warning: `[WARNING GRT-0230] Congestion iterations cannot increase
> overflow. Stop the iterations.`

GR detects that further passes are making things worse, not better. Means
overflow is bounded below by routing demand — capacity issue, not effort
issue. Raise iteration count won't help. Treat it as a definite signal
to change geometry/tie-cells/density, not as transient noise.

### GRT-0116 — global routing finished with congestion

> error: `[ERROR GRT-0116] Global routing finished with congestion.
> Check the congestion regions in the DRC Viewer.`

Two distinct flavors — diagnose by reading the `[INFO GRT-0096] Final
congestion report` table:

1. **High overall layer usage (>50%) on one or two layers** — true
   demand-vs-capacity shortage. Genuine routability problem; fixes are
   the GRT-0118 menu (broaden core, reduce buffer pressure, etc.).

2. **Low total usage (<10%) but high `Max H` / `Max V` per gcell** —
   localized hotspots. Some gcells want N tracks but have 0. Almost
   always means **a routing layer is unavailable in a region** because a
   macro's LEF marks it as `OBS`. The parent router squeezes wires
   through what little space remains around the OBS edges.

   Hit on issue #32 Phase B: tile LEF declared OBS on M1..M6 over the
   full 34.56 µm² tile face. Five parent broadcasts (`drain_en`,
   `drain_slot[0..1]`, `scrub_en`, `reset`) had to route from a die-edge
   input port to all 16 abutted tile pins — but with `MAX_ROUTING_LAYER
   = M7` (vertical), there was NO horizontal layer available over the
   array. Router jammed them into 0-track M2/M3 gcells at tile edges →
   45,973 total overflow at 5% usage.

   **Fix:** don't route over abutted macros. Convert parent broadcasts
   to W↔E abutment-feedthrough pin pairs (`*_w` input on W edge, `*_e`
   output on E edge, internal `assign *_e = *_w`). Parent drives only
   the westernmost column; abutment propagates the signal east via
   pin-to-pin metal touching. See `tech/asap7/problems/C1_abutment_layer_planning.md`.

### DRT-0199 — boundary shorts at abutted-macro seams

> log: `[INFO DRT-0199]   Number of violations = N` (does not converge)

Symptom: routing report shows tiny M4 (or other pin-layer) shorts at
exact tile-to-tile boundary x or y coordinates, one short per pin pair
the parent has to wire across. DRT iterates 60+ times trying to fix
them; doesn't converge.

Root cause: `write_abstract_lef -bloat_occupied_layers` marks every
routing layer the macro used as a single rectangle covering the full
macro face. If the **pin layer itself** is in that OBS, DRT can't land
pin-access vias next to the pin without colliding with the OBS — so it
draws tiny wrong-way jogs at the boundary that DRC flags as shorts
between the macro's pin metal and the parent's connection wire.

**Fix:** in the LEF post-process, strip the pin layer (and any other
sparse-internal-usage layer) from the OBS list. For asap7 abutment-ready
tiles with W/E M4 pins, strip M4 in addition to the usual M1/M2/M5/M6/M7.
Keeping only M3 in OBS still prevents broad parent over-tile routing
but lets DRT freely place pin-access vias.

Diagnostic shortcut: **DRT iteration speed is a correctness signal.**
A converging design clears in 4–6 iterations (~2 min for a 4×4 abutted
harness). A non-converging design wastes 30+ min churning through max
iterations. If iter 0 starts >1000 violations OR iter 3 still shows
>100, abort early and re-diagnose — don't wait for it to finish.

### DRT-0xxxx — detailed routing antenna / spacing

We haven't hit this yet on compute_array since we keep failing at GR.
It's the natural next failure to expect once GR clears. Common fixes:
diode insertion, layer-shifting, careful pin layer choice on macros.

---

## Placement

### DPL-0036 — detailed placement failed

> error: detailed placement: cells could not be legalized

- **Root cause we hit:** too many cells (filler + std cells + tap) for
  the available core area after macros are placed. On `dense_grid` PoC
  with 32×32 macros at 450 µm pitch, leftover std cell area was
  insufficient.
- **Fixes:**
  - Reduce density (`PL_TARGET_DENSITY_PCT` lower)
  - Add `MARGIN` to give std cells more space
  - In emergencies for PoC work: `--skip Checker.PowerGridViolations`
    and `--skip OpenROAD.DetailedPlacement` to push past for debugging
    (NOT for signoff)

### Global Placement timing out

Not a "failure" but blocks iteration: GP at 90+% density on compute_array
takes 2+ hours. Its Nesterov solver is O(cells × iterations); fillers
inserted at step 14 dominate (~370k fillers for compute_array's empty
die area).

- **Workarounds:**
  - Smaller die area (less empty space = fewer fillers)
  - Densely-packed macro arrays (e.g. dense_grid PoC was 9× faster than
    compute_array thanks to less empty area)
  - Skip filler insertion if iterating fast (`RUN_FILLER_INSERTION: false`)

---

## PDN / Power

### PSM-0069 — PDN connectivity failed

> error: `[ERROR PSM-0069] Unconnected vias or instances.`

The PDN extractor can't find a path from every cell/macro to chip-level
power rails.

- **Recurring root causes on this project:**
  1. **Macro placed at orientation other than N.** Rotation flips the
     macro-internal PG strap layers onto the same layer as the parent's
     straps → via-based connect path can't reach across. **Fix:** keep
     every macro at orientation N. We re-hardened `skew_lane` twice with
     different pin orders (skew_lane_a vs skew_lane_b) so both
     instances place at N without rotation.
  2. **Macro origin not on 50 µm grid.** Parent PDN straps at 50 µm
     pitch don't align. **Fix:** `gen_config.py` snaps every origin to
     `ceil(v/50)*50`.
  3. **VPITCH/HPITCH mismatch** between parent (`FP_PDN_VPITCH`) and
     macro internal PDN pitch. **Fix:** parent PDN matches the cell's
     50 µm pitch so chip rails overlay macro PG rails directly.

### PSM-IR voltage drop

Not yet hit. Will become relevant for full chip_top integration.

---

## Synthesis / Netlist

### yosys missing module

We hit this when SV refers to `skew_lane` but only `skew_lane_a/b` were
hardened. Fix: stub generation in `tech/sky130/scripts/gen_stub_*.py`
emits an empty matching-interface module so yosys is happy.

### Bit-reverse routing crossings

Not a tool error but causes physical-routing crossings: if SV maps
`push_a_bytes[k*8 +: 8]` to `a-skew[k]`, the wires from cmd_unit (east
half of N face) to a-skew[k] (west column) cross each other because
cmd_unit's high-index pins are on the east while a-skew's low-index
sinks are on the south. Fix: reverse the mapping with
`push_a_bytes[(MMA_M-1-k)*8 +: 8]` so within-byte-group wires diverge
rather than crossing.

---

## OpenLane framework

### Failed `--from` resume leaves empty stub dir

When you re-run with `--from <step>` and `--last-run`, a brand-new dir
may get created with a malformed name (no `RUN_` prefix). On the next
`--last-run`, OpenLane picks that empty stub instead of the real run.

- **Symptom:** "step <N> not found" or "state_in.json missing"
- **Fix:** check `runs/` for empty/stub dirs and remove them. Always
  pass `--run-tag RUN_<timestamp>` explicitly, not `--last-run`.

### `read_db` loads macros baked in at write time

OpenLane2's `read_db` of an `.odb` snapshot brings in macro instances
**with their definitions captured at ODB-write time**. Re-running with a
modified macro LEF after `read_db` doesn't update existing instances.

- **Symptom:** "I changed the LEF but the obs/pin positions didn't
  update"
- **Fix:** to inject routing obstructions or other modifications after
  `read_db`, use the odb API directly:
  ```tcl
  set block [ord::get_db_block]
  set tech [ord::get_db_tech]
  foreach inst [$block getInsts] {
      if {[[$inst getMaster] getName] == "skew_lane_b"} {
          set bbox [$inst getBBox]
          foreach lname {met1 met2 met3 met4 met5} {
              set lyr [$tech findLayer $lname]
              odb::dbObstruction_create $block $lyr \
                  [$bbox xMin] [$bbox yMin] [$bbox xMax] [$bbox yMax]
          }
      }
  }
  ```

### Steps that don't write a new DEF

`STAMidPNR` (steps 31, 34) and `*-checker-*` steps are read-only. They
forward the same DEF/ODB the prior step wrote. If you're checking the
state at one of these steps, read the upstream step's DEF.

- **Diagnostic:** every step has a `state_in.json` listing the exact
  DEF/ODB/SDC it consumed.

---

## Tcl / shell traps

### STA-0565 — `read_lef requires one positional argument`

Caused by a `# comment` mid-Tcl-line:

```tcl
read_lef $::env(SKEW_B_LEF)  # opaque version   ← BROKEN
```

Tcl only treats `#` as comment at the START of a command. Mid-command,
`#` and following words become extra positional arguments.

- **Fix:** put comments on their own line, or remove inline.

### `set_routing_layers -signal met1-met5` excludes li1 entirely

Sets li1 capacity to **0**, not "use the global derate." This overrides
`set_global_routing_layer_adjustment li1 0.99`. Standalone GR tests
that omit `set_routing_layers` give different results than the
OpenLane flow.

- **Diagnostic if standalone passes but flow fails:** check whether
  you replicated the full `openroad/common/grt.tcl` preamble
  (`set_routing_layers` + `set_global_routing_layer_adjustment`).

### `-allow_congestion` doesn't run more iterations

It only suppresses GRT-0118 errors at the end. Residual overflow stays
in the design and DR has to clean up. Use this knowingly — don't expect
extra effort from GR.

### Docker `-e VAR` (bare) requires pass-through

Inside `sg docker -c '... -e MY_VAR'`, bare `-e MY_VAR` requires
`MY_VAR` exported AND visible inside the `sg` shell.

- **Safer:** `-e MY_VAR=$MY_VAR` with explicit value substitution.

---

## Process / human

### Killed a long-running PID by mistake

`pkill openroad` kills the active step's openroad. On a 6-hour run this
hurts a lot.

- **Recovery:** `--from <step>` + `--run-tag RUN_<timestamp>` resumes
  from the last completed step. The lost step has to be re-run; earlier
  steps' artifacts (DEF, ODB, SDC) are preserved.
- **Prevention:** check which PID is the live one (`ps -ef | grep
  openroad`); only kill specific PIDs, never `pkill -f openroad` while
  any synthesis is in flight.

### Stale `gen_config.py` constants in analysis scripts

Hand-coded `CMD_HALO`, `CMD_OFFSET`, `BSKEW_Y`, etc. in render or
analysis scripts drift out of sync with `gen_config.py` after every
change. Outputs misclassify regions.

- **Fix:** every render/analysis script reads `gen_config.py` (regex
  match the constants) or parses the DEF for actual placements. Never
  hand-code a coordinate. Render scripts should assert against DEF
  positions before drawing — see `scripts/README.md` section 9.5.

### SDC constraint masks a real timing path

> A `set_false_path` (or `set_multicycle_path`, `set_clock_groups`,
> etc.) tells STA to *ignore* a path. Used wrongly, it hides a real
> bug — the build closes with margin to spare even though the
> underlying physical path doesn't actually meet timing at scale.

Caught on issue #32 PR #36 review. The fix RTL added a combinational
W→E broadcast feedthrough chain (`assign *_e = *_w`) across each row
of the mac tile array. Four signals went through it: `reset`,
`drain_slot`, `scrub_en`, and `drain_en`. The first three are
quasi-static (held many cycles by their producers) so `set_false_path`
on them is correct. **`drain_en` is not** — it's a single-cycle
snapshot pulse from `cmd_unit.sv`'s `D_IDLE → D_PULSE` transition that
every cell must capture on the SAME clock edge to load
`storage[drain_slot]` simultaneously. False-pathing it would let STA
*and the optimizer* both ignore the ~1100 µm M4 ripple across a
32-wide chain; at M=N=4 the chain closes anyway (the harness can't
catch the bug); at M=N=32 cells in far columns would miss the pulse
and `drain_row_data` would come out column-misaligned.

How this class of bug evades the defenses we have:

1. **cocotb/Verilator sim does not model wire delay.** RTL is
   correct; the bug is purely in the SDC. Sim has nothing to detect.
2. **Small builds close regardless of the constraint** — the masked
   path is short enough at 4×4 to meet timing whether STA times it or
   ignores it. The harness reports "clean."
3. **No gate-level SDF simulation in this repo.** That would be the
   only automated check that catches a constraint-vs-reality mismatch.
4. **STA reports look identical** with or without the wrong constraint
   in a small build, so a reviewer skimming the WNS/TNS numbers can't
   see anything wrong.

**Detection rule (the rule reviewers must apply manually):**

> Any `set_false_path` on a downstream signal requires you to look at
> the *producer FSM* of that signal and classify it:
>
> - **Quasi-static** (producer holds it stable across many cycles
>   before consumers sample): `set_false_path` is correct.
> - **Single-cycle pulse with multiple consumers that must capture
>   the same edge**: NEVER `set_false_path`. Let STA time it, see if
>   it closes at scale, and treat any failure as real architectural
>   information.
> - **Multi-cycle phase** (producer holds for N cycles, consumer
>   samples once per phase): consider `set_multicycle_path -setup N`
>   instead of `set_false_path`.

Every existing `set_false_path` in this repo (10 total — 3 quasi-static
broadcasts on this PR + 4 block-level IO false-paths in
`compute_array.sdc` deferring to chip_top closure via #28) was audited
against this rule and found correct.

**Add to PR-review checklist:** any commit that touches `*.sdc` must
include either (a) classification of every new constraint against this
rule or (b) an explicit comment in the SDC explaining why STA can
safely ignore the path. See `tech/asap7/TILE_SPEC.md` §
"Broadcast feedthrough timing contract" for the canonical worked
example.

### Quality-gate skips (SKIP_LAST_GASP, GRT_ALLOW_CONGESTION, etc.)

Same class of risk: an env knob that *tells the tool to give up on a
quality requirement* lets a build report "clean" while leaving real
violations behind.

| Knob | What it skips | What can leak through |
|---|---|---|
| `SKIP_LAST_GASP=1` | OpenROAD's post-route timing repair pass | residual hold violations (CTS adds buffers; main repair fixes most; last_gasp catches the rest). Bad if you call the build "tape-out clean." |
| `GRT_ALLOW_CONGESTION` | (not a real env var; was tried and failed) | n/a |
| relaxed `clk_period` | shifts the target frequency lower | real high-frequency closure problems |

**Rule:** every skip needs a `# WHY:` comment in the config and a
periodic re-validation that the skipped pass would have reported clean
anyway. Don't ship a tape-out with a quality knob disabled.

---

## Frequency on this project

| Failure | Hits |
|---|---|
| GRT-0118 (overflow)                        | 6 |
| PSM-0069 (PDN)                             | 4 |
| Hardcoded-coord render bug                 | 3 |
| GRT-0116 (broadcast over abutted macro)    | 3 |
| DRT-0199 (M4 boundary shorts on abutment)  | 3 |
| `--from` resume confusion                  | 2 |
| Tcl inline comment                         | 1 |
| Killed openroad PID by mistake             | 1 |
| DPL-0036 (PoC only)                        | 1 |
| yosys missing module                       | 1 |
| `read_db` modified LEF didn't apply        | 1 |
| Wrong `set_false_path` masking real path   | 1 |

GRT-0118 dominates because compute_array's b-skew row was the routing
bottleneck and we kept iterating on it. GRT-0116 + DRT-0199 are the
abutment-tile pair we hit on issue #32 Phase B (3 iterations each before
we landed the right fix in `tech/asap7/problems/C1_abutment_layer_planning.md`).
