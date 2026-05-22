# Render scripts — lessons learned

This directory has Python scripts that visualize the sky130 / OpenLane flow:
pin diagrams, routing congestion heatmaps, clock trees, block diagrams, DRC
overlays, etc. Many failure modes during this project came from renders that
**looked plausible but didn't reflect the chip** — guessed coordinates,
stale data, wrong pin order, wrong layer interpretation. This file
catalogues every quirk we hit so the next render isn't built on the same
landmines.

The rule, learned the hard way: **EVERY POSITION MUST BE DERIVED FROM AN
AUTHORITATIVE SOURCE** (LEF / DEF / pin_order.cfg / gen_config.py / SV).
Never hand-code constants that the tool decides. Never guess offsets.

---

## 1. Coordinate systems and macro placement

### 1.1 The 50 µm PDN grid is law

`mac_tmem_cell`, `skew_lane_a/b`, `cmd_unit` were all hardened with
`FP_PDN_VPITCH = FP_PDN_HPITCH = 50` and `FP_PDN_VOFFSET = FP_PDN_HOFFSET = 0`.
This forces every macro origin in compute_array to sit on a 50 µm grid so
that parent PDN straps align with macro-internal PDN straps.

**Consequences:**

- The minimum macro pitch in the parent design is `ceil(macro_dim / 50) * 50`.
  For mac_tmem_cell (426.55 wide) the minimum pitch is **450 µm**, not 427.
  The leftover 23.45 µm gap is the routing channel.
- `gen_config.py` uses `snap(v) = ceil(v/50)*50` for every coordinate. If a
  rendered position doesn't snap to 50, it's wrong.
- Misaligning by even a few µm causes `PSM-0069` (PDN connectivity failed).

### 1.2 Macro pin coordinates are LOCAL to the macro

LEFs declare pins as `RECT x1 y1 x2 y2` in the macro's local frame. To get
parent coordinates:

```
parent_x = macro_origin_x + local_x
parent_y = macro_origin_y + local_y
```

**only when orientation is `N`**. For rotated macros, pin coordinates
transform. **We deliberately avoid all rotation in compute_array** —
skew_lane_a vs skew_lane_b are separate hardenings precisely so we can
place both at orientation N.

Rotating a macro (e.g. R270) swaps the macro-internal met4/met5 axes onto
the same layer as the parent's straps — PDN's via-based connect path can't
reach across, and you get PSM-0069 every time. *Trust this; we burned hours
on it.*

### 1.3 Macro instance names in DEF have backslash escapes

After yosys flattens generate-blocks, instance names like
`gen_row[0].gen_col[0].u_cell` appear in the DEF as:

```
gen_row\[0\].gen_col\[0\].u_cell
```

Regex must match `\\\[(\d+)\\\]` not `\[(\d+)\]`. Same for `gen_a_skew\[7\].u_a`
etc. Skipping this gives "0 cells found" while the file has 1024.

---

## 2. Pin order, pin face placement, and `$N` virtual pins

### 2.1 How OpenLane assigns external pin positions

OpenLane's pin placer (`ppl` in OpenROAD) parses `pin_order.cfg` per
section (`#N`, `#E`, `#S`, `#W`). Pins are then placed along that face in
listed order, evenly spaced from one corner to the other.

**Convention (verified empirically by reading DEFs):**

- `#N`: west → east as listed
- `#S`: west → east as listed
- `#W`: south → north as listed
- `#E`: south → north as listed

(Reversed by writing `#NN`, `#WW`, etc. We have never used reverse.)

### 2.2 `$N` virtual pins shift everything

`$10` consumes 10 pin-position slots without emitting an actual pin.
Subsequent real pins land 10 slots further along the face. Use this to
deliberately push real pins to a specific region of the face.

We use this in `compute_array.pin_order.cfg` to keep chip-IO pins out of
the WEST half of the N edge (which would route across the push_a fan to
a-skew at x=500).

### 2.3 Pin face placement matters geometrically, NOT just for ordering

The biggest pin-related disaster was assuming pin face only affected
diagrams. It affects routing. Example from this project:

- `cmd_unit` originally had chip-IO pins on its N face.
- `compute_array`'s external N edge had chip-IO pins spread x=69 to 16730.
- Some chip-IO pins (e.g. `mma_issue` at x=69) sit WEST of `a-skew` at
  x=500, which means parent wires fly UP-LEFT from cmd_unit's east-half
  N face to land at x<500. Those wires cross the push_a fan (cmd_unit
  west-half N face → a-skew column).
- Fix was a combination of:
  1. `$N` virtual offset in compute_array.pin_order to push chip-IO east of x=500
  2. Swap cmd_unit N face order so chip-IO sits west, push_a east
  3. Bit-reverse push_a_bytes-to-a-skew mapping in compute_array.sv so
     within-byte-group wires diverge instead of crossing

### 2.4 Tcl `# comment` mid-line is NOT a comment

```tcl
read_lef $::env(SKEW_B_LEF)  # opaque version    ← BROKEN
```

Tcl treats `#` as comment only at the START of a command. Mid-command,
`# opaque version` is interpreted as extra arguments. This caused
`STA-0565: read_lef requires one positional argument` for ~30 min of
debugging. Either put the comment on its own line or remove it.

---

## 3. LEF interpretation — OBS rects mislead by count

The LEF `OBS` section declares routing obstructions. Beware:

| Layer | OBS rect count | Blocked area % |
|---|---|---|
| met2 | 121 | **96%** of macro |
| met3 | 53 | 93% |
| met4 | **26** | **74%** |
| met5 | 2 | 13% |

26 rects sounds "sparse" but covers 74% of the macro area. Always compute
actual area (`sum((x2-x1)*(y2-y1))`), not count.

**Implication:** macros DO block lower layers heavily (effectively a
black-box on met1-met3). Upper layers (met4, met5) leave significant
fly-over space (74% / 13% blocked respectively). Parent GR uses that
remaining capacity. This is why "routing inside macros" shows on the
congestion heatmap — it's the parent's fly-over routes on whatever upper
layers the macro left open.

---

## 4. OpenROAD / OpenLane GR quirks

### 4.1 `GRT_ADJUSTMENT` derates capacity

Default is `0.3` in sky130A. Means GR pretends only 70 % of physical
tracks are usable, leaving 30 % safety margin for DR. Hence "100 %
congestion" in the heatmap = 70 % physical use.

```
Capacity_GR = Capacity_physical * (1 - GRT_ADJUSTMENT)
```

### 4.2 `GRT_LAYER_ADJUSTMENTS` overrides per layer

sky130A PDK default: `[0.99, 0, 0, 0, 0, 0]` → [li1, met1, met2, met3, met4, met5].

- li1: **99 % derated** — effectively banned from GR (used only for via stacks at sinks)
- met1-met5: use the global `GRT_ADJUSTMENT`

### 4.3 `set_routing_layers -signal met1-met5` EXCLUDES li1 entirely

This was the actual root cause of the compute_array GR failure. The TCL
sequence is (sourced by `openroad/common/grt.tcl`):

```tcl
set_routing_layers -signal met1-met5 -clock met1-met5   ← bans li1 ENTIRELY (0% available)
set_global_routing_layer_adjustment * $GRT_ADJUSTMENT
set_global_routing_layer_adjustment li1 0.99            ← redundant since li1 already excluded
```

With li1 excluded by `set_routing_layers`, parent wires that previously
escaped pin tiles via tiny li1 hops can't. Demand cascades to met1/met2 → overflow.

**Standalone GR test that didn't use `set_routing_layers`** routed cleanly
(li1 at 30 % derate gave 70 % of its tracks for short escapes). That's
why my standalone test passed where OpenLane's flow failed — different
layer constraints. **Always replicate OpenLane's exact TCL preamble when
trying to reproduce its GR.**

### 4.4 `-allow_congestion` doesn't run more iterations

It just tells GR "don't error out at the end with GRT-0118 if overflow
remains." It does NOT continue iterating. If 200 iterations weren't
enough, `-allow_congestion` returns a guide with residual overflow that
DR has to fix.

### 4.5 GR guide format

```
<net_name>
(
<x1> <y1> <x2> <y2> <layer>
<x1> <y1> <x2> <y2> <layer>
...
)
<next_net_name>
(
...
)
```

- Coordinates in **nm** (divide by 1000 for µm)
- Net names match Verilog hierarchy after yosys flattening. Escapes: `\[`, `\]`.
- yosys-internal nets: `_00000_`, `net12345`
- CTS-added buffer tree edges: `clknet_<level>_<branch>_<idx>_clk`
- Original chip-level clk: literally `clk`

### 4.6 GR guide has implicit gaps near macros

The original `clk` net in the guide only has 6 segments — all clustered
right at the chip-IO pin (x=19444-19500). The first CTS buffer is at
x=18000. The 1500 µm wire connecting them is not explicitly in the guide.
Either it's stored under the chip-IO pin's port representation (which
isn't `LAYER ... RECT` form) or it's an OpenROAD quirk where short
near-pin segments are subsumed into the pin's port shape.

**For visualization: always draw an explicit "implied connection" between
the chip-IO pin and the first observed segment of a net, with a different
color/style to mark it as inferred.**

### 4.7 GRT-0118 = overflow remaining after max iterations

Default `GRT_OVERFLOW_ITERS=50`. We raised to 200; still failed because
the algorithm hits `GRT-0230` ("congestion iterations cannot increase
overflow") which means "I'm stuck and giving up early." Increasing
iterations beyond that doesn't help — overflow is a *capacity* problem,
not an *effort* problem.

---

## 5. Heatmap dump quirks

### 5.1 `gui::dump_heatmap "Routing" <csv>` format

Each row: `x0,y0,x1,y1,value`. Bounds in µm. Value is congestion %.

- Tile size: typically **7×7 µm** in sky130A
- Total tiles for a 19500×19900 die: ~7.9M
- Value is **capped at 100.0** in the dump (no >100 even if true demand exceeds)

The 100-cap is misleading: a tile at exactly 100 might have *barely* fit or
might have had 30 % overflow that GR couldn't reduce. To find true edge
overflow you have to read the GR log's "Final congestion report" table
which has `Max H / Max V / Total Overflow` columns. Those report
edge-overflow counts, not tile-aggregate.

### 5.2 To get a heatmap you must use the `-gui` openroad mode

`gui::dump_heatmap` is a GUI tcl command. Run openroad with `-gui` and
`QT_QPA_PLATFORM=offscreen` for headless dump. Without `-gui`, the
command is undefined.

### 5.3 Heatmap reflects whatever GR settings you used

If you run a standalone GR for the heatmap dump but with different
`set_routing_layers` / `GRT_ADJUSTMENT` / `set_global_routing_layer_adjustment`
than the OpenLane flow used, you'll get a DIFFERENT heatmap than what the
flow saw. **Always replicate the OpenLane preamble exactly** (see 4.3).

---

## 6. OpenLane state, ODB, and resuming runs

### 6.1 ODB binary has macros baked in

`read_db <path>.odb` loads the OpenROAD database with all macro instances
referencing the master definitions present **at the time the ODB was
written**. Loading a modified LEF *after* `read_db` does NOT update
existing instances.

To inject changes (e.g., additional routing obstructions over a macro
footprint) **after** loading the ODB, use the odb API directly:

```tcl
set block [ord::get_db_block]
set tech [ord::get_db_tech]
foreach inst [$block getInsts] {
    set master [$inst getMaster]
    if {[$master getName] == "skew_lane_b"} {
        set bbox [$inst getBBox]
        foreach lname {met1 met2 met3 met4 met5} {
            set lyr [$tech findLayer $lname]
            odb::dbObstruction_create $block $lyr \
                [$bbox xMin] [$bbox yMin] [$bbox xMax] [$bbox yMax]
        }
    }
}
```

### 6.2 Some OpenLane steps don't write a new DEF

Specifically `STAMidPNR` (steps 31 and 34) and the various `*-checker-*`
steps are read-only — they don't write `compute_array.def`. The state
passed forward is the same ODB the prior step wrote.

`state_in.json` in a step's dir tells you exactly which DEF/ODB/SDC the
step consumed. Read it to find the actual input state.

### 6.3 Resuming with `--from <step_id> --run-tag <RUN_*>`

OpenLane2 supports resuming a failed flow:

```bash
python -m openlane --pdk sky130A \
  --run-tag RUN_2026-05-20_16-01-10 \
  --from OpenROAD.ResizerTimingPostCTS \
  config.yaml
```

Gotchas:

- `--run-tag` must EXACTLY match the existing run dir name including the
  `RUN_` prefix (`RUN_2026-05-20_16-01-10`, not `2026-05-20_16-01-10`).
  Failed first attempts of `--from` may create an empty stub dir with no
  prefix — delete it before retrying.
- `--last-run` picks the latest by mtime; may pick the empty stub. Use
  explicit `--run-tag` for safety.
- A new step dir is appended each time you re-run from the same point
  (`32-openroad-resizertimingpostcts`, `33-openroad-resizertimingpostcts`,
  `34-...`, etc). The latest one is the live one.

### 6.4 Default filler/tap insertion dominates GP runtime

OpenLane's `OpenROAD.TapEndCapInsertion` step runs early (step 14). For
compute_array, this inserts 2.35M tap cells + 369k decap fillers (over
the 190 M µm² of empty die area). These cells are then visible to
`GlobalPlacement` (step 19, 23).

GP's Nesterov solver computes density on every cell every iteration:

- ~28M density updates per iteration × ~1000 iterations × ~1 ns each = ~30 sec
- *Actual measurement:* GP-skip-IO took **1h 52 min**, full GP another 2h.

This is the dominant cost. To accelerate iteration cycles:

- Smaller die area = fewer empty space = fewer fillers = faster GP.
- Or set `RUN_FILLER_INSERTION: false` and add filler at the very end (we
  did *not* do this; preserved signoff defaults).

Our PoC `dense_grid` (32×32 mac_tmem_cell at 450 µm pitch, 90 % macro
density) demonstrated this: GP-skip-IO took **12 min** vs compute_array's
112 min. **9.3× speedup just from less empty area.**

---

## 7. Rendering performance

### 7.1 `matplotlib.pyplot.plot()` is SLOW per segment

For ~700k segments (a full compute_array GR guide), individual `plot()`
calls take ~20 minutes total. Each call creates a `Line2D` object.

Use `LineCollection` for bulk:

```python
from matplotlib.collections import LineCollection
segs = [((x1,y1),(x2,y2)), ...]
ax.add_collection(LineCollection(segs, colors="green", linewidths=0.5, alpha=0.9))
```

100× faster. Same visual result.

### 7.2 DPI / image size tradeoffs

- 8000×8000 image rendered at 200 DPI: ~3-5 min, file size 4-10 MB
- 2000×2000 image rendered at 120 DPI: ~30 sec, file size 0.5-2 MB
- Display tools may refuse >2000×2000 — always also write a thumbnail
  resized to 1800-1900 max dim for inline viewing

### 7.3 Inline images via Read tool need to be reasonably sized

If Read returns "media removed — rejected by API" the image is too large.
Resize with PIL `thumbnail((1800, 1800))` and save as a separate
`*_small.png` before reading.

---

## 8. Macro-specific layout knowledge for compute_array

### 8.1 Macro dimensions (verified from LEFs)

| Macro | W | H | Notes |
|---|---|---|---|
| mac_tmem_cell | 426.55 | 437.27 | 147 pins. PDN met4/met5 on 50µm grid. |
| skew_lane_a | 250 | 300 | 33 pins. clk on **W face met3**. Designed to be placed at W column. |
| skew_lane_b | 250 | 300 | 33 pins. clk on **S face met2**. Designed to be placed at S row. |
| cmd_unit | 1200 | 1600 | 1316 pins. Re-hardened from 600×800 to give more pin pitch. |

### 8.2 Pin face mapping (which pins on which face) — derived from LEF positions

**mac_tmem_cell:**
- S face (y≈2): `b_in[0..7]`, `drain_in[0..31]`, `compute_in`, `slot_in[0..1]`, `accum_in`, `scrub_en`, `clk`
- N face (y near top): `drain_out[0..31]`, `b_out[0..7]`
- W face (x near 0): `a_in[0..7]`
- E face (x near width): `a_out[0..7]`, `compute_out`, `slot_out`, `accum_out`

**skew_lane_b:**
- S face: `clk`, `reset`, `push_byte[0..7]`, `push_now`, `push_slot[0..1]`, `push_accum`, `tap_index[*]`
- N face: `edge_byte[0..7]`, `edge_valid`, `edge_slot[0..1]`, `edge_accum` (last 4 UNUSED in compute_array.sv)

### 8.3 Connection topology (from compute_array.sv) — bit-reverse caveat

After `compute_array.sv` was modified for routing-friendly bit ordering:

```sv
skew_lane_a u_a (.push_byte (push_a_bytes[(MMA_M-1-gi_a)*8 +: 8]), ...);
skew_lane_b u_b (.push_byte (push_b_bytes[(MMA_N-1-gj_b)*8 +: 8]), ...);
```

so `push_a_bytes[0..7]` actually feeds `a-skew[31]`, not `a-skew[0]`.
**Any renderer that draws "byte 0 → lane 0" is WRONG**. The
`render_compute_array_wires.py` script detects this via:

```python
rev_a = "(MMA_M-1-gi_a)*8" in sv_text or "(MMA_M - 1 - gi_a) * 8" in sv_text
```

and inverts the mapping. Other renderers must do the same or they show
crossings that aren't there.

### 8.4 b-skew unused outputs (visible but dangling pins)

In `compute_array.sv` lines 156-159:

```sv
.edge_valid (ev_unused),
.edge_slot  (es_unused),
.edge_accum (ea_unused),
```

These 4 pins on every b-skew's N face have NO connection at parent
level. Renderers will see them in the LEF and may draw them as
"dangling." That's correct — they really are unconnected.

### 8.5 Where common signals actually route

| Signal | Source | Sinks | Face used | Notes |
|---|---|---|---|---|
| `clk` (chip-level) | E die edge near y=4978 | 1089 sinks via CTS tree | enters E edge | First buffer at x≈18000, then full chip fanout |
| `reset` | E die edge near y=14925 | similar broadcast | enters E edge | Second-listed pin on `#E` |
| `rd_a/b_*` | W die edge | cmd_unit W face | enters W edge | 582 pins total |
| `mma_*`, `issue_*`, etc. | N die edge | cmd_unit N face | enters N edge | 200 chip-IO pins |
| `drain_row_data[0..1023]` | S die edge | cells[0][c].drain_out | exits S edge | 1024 pins, the matmul result |

### 8.6 The b-skew row is the routing bottleneck of compute_array

Confirmed by GR overflow analysis: 781 / 781 overflow tiles (100 %) sit
inside the b-skew row macros at y≈2598. Why:

1. b-skew internal layout uses **96 % of its met2 area** for pin escape
   (large OBS area despite few rect count).
2. Parent CTS has to route its E-W trunk somewhere near y=2500-2700 to
   reach all 32 b-skew clk pins (on b-skew's S face at y=2400) plus
   feed cells row 0 (clk pin at y=2900).
3. The natural trunk position is **inside b-skew's footprint** because
   the 200 µm channel between b-skew and cell row 0 isn't wide enough
   for a CTS spine plus its branches.

This is structural, not config — it requires either re-hardening b-skew
larger, widening the channel, or routing the clock with explicit
non-default placement. *We have NOT fixed this in the project as of
this writing.*

---

## 9. The renderers themselves

### 9.1 Source-of-truth hierarchy

```
gen_config.py constants
  ↓
config.yaml (auto-generated)
  ↓
pin_order.cfg (committed)
  ↓
OpenLane / OpenROAD runs
  ↓
DEF + ODB + LEF in runs/RUN_*/final/
  ↓
GR guide file (from explicit dump or step-35)
  ↓
heatmap CSV (from gui::dump_heatmap)
```

Renderers should read from the LOWEST authoritative source available for
the data they need:

- For macro dimensions: LEF first, fallback to config.yaml DIE_AREA
- For macro placement: DEF COMPONENTS section
- For pin positions: DEF PINS section (post-IO-placement) OR derived
  from LEF + pin_order.cfg + face math (if no DEF yet)
- For wire routes: GR guide
- For congestion: heatmap CSV from `dump_heatmap`
- For instance topology: SV netlist (regex for systolic patterns)

Never hardcode positions in the renderer — always derive.

### 9.2 The config-driven renderer (`render_compute_array_wires.py`)

This is the most reliable renderer. It:

1. Reads gen_config.py constants (`CMD_OFFSET`, `CMD_HALO`, etc.)
2. Reads latest LEFs in `runs/RUN_*/final/lef/` (incl. cmd_unit which
   may have been re-hardened) and prefers config.yaml DIE_AREA
3. Parses pin_order.cfg per side, expands regex / virtual pins
4. Detects bit-reverse in compute_array.sv
5. Resolves each cmd_unit pin to its destination based on signal name
   patterns + topology rules

Edit any input file, re-run, get a correct diagram. Treat this as the
reference pattern for any new renderer.

### 9.3 Required quality bars for new renderers

Before committing a new render script:

- [ ] Every coordinate sourced from a file (not hardcoded). Add a
      one-line comment naming the source file for each constant.
- [ ] Re-render produces output consistent with the latest config; no
      stale paths to old `RUN_*` directories.
- [ ] If using OpenROAD-derived data (DEF/ODB/heatmap), the renderer
      replicates the SAME `set_routing_layers` / `GRT_ADJUSTMENT` /
      `set_global_routing_layer_adjustment` calls as
      `openroad/common/grt.tcl` — otherwise the data won't match the
      flow's GR output.
- [ ] If the renderer assumes a particular pin face, it validates by
      checking actual pin coordinates from LEF (e.g., "skew_lane_b
      should have clk on S face — assert it's actually at y<20").
- [ ] Macro instance regex includes the backslash-escaped form
      (`gen_row\\\[(\d+)\\\]`).
- [ ] Bit-reverse mapping detected from SV (not hardcoded).
- [ ] Uses `LineCollection` for ≥1000 segments; falls back to per-segment
      `plot()` only for ≤100 highlighted nets.
- [ ] Writes both a full-resolution PNG and a `_small.png` for inline view.
- [ ] Title or legend states the data source: `RUN_<timestamp>`, GR step
      number, config constants snapshot. Future-you needs to know what
      version of the design this image represents.

### 9.4 Per-step rendering — what we'd want next

We don't have these yet but should:

| OpenLane step | What to render | Source data |
|---|---|---|
| 09 Floorplan | macro outlines + die outline | DEF DIEAREA + COMPONENTS |
| 14 Tap insertion | filler/tap density per region | DEF COMPONENTS (count tap_* cells per band) |
| 16 PDN gen | met4/met5 PDN straps | DEF SPECIALNETS |
| 19 GP skip-IO | std cell density heatmap | DEF COMPONENTS (placement coords) |
| 21 IO placement | external pin positions per side | DEF PINS |
| 23 Full GP | std cell density (post-IO) | DEF |
| 30 CTS | clock tree topology | DEF NETS (clk-related) |
| 35 Global routing | congestion heatmap + offender nets | guide + heatmap CSV |
| 50 DR | actual wires per layer | DEF NETS (post-DR has ROUTED segments) |
| 56 XOR | DRC violation locations | XOR DEF |
| Final | layout overview | run.sh's klayout-rendered PNG |

A per-step renderer that follows the same config-driven pattern as
`render_compute_array_wires.py` could be auto-invoked at each step's
completion via OpenLane's hooks. **This is the next concrete TODO.**

---

## 9.5. Stale-coordinate landmines — the recurring renderer bug

This is a SEPARATE section because it keeps happening and is the #1 cause of
"the render doesn't match the chip."

### 9.5.1 The bug pattern

I made this mistake three times during compute_array bring-up. Each time:

1. I hand-coded a coordinate constant from a previous design state, e.g.
   ```python
   BSKEW_Y = 2400        # ← FROZEN to what origin_y - sbh was at the time
   ASKEW_X = 2250        # same kind of staleness
   ```
2. Later I changed `gen_config.py` (CMD_OFFSET, CMD_HALO, NORTH_MARGIN, etc.)
3. The macro placement coordinates ALL SHIFTED. The render scripts still used
   the old constants. Everything in the diagram drifted out of position.

**Real example from this project:**

| Time | CMD_OFFSET | CMD_HALO | computed origin_y | computed BSKEW_Y |
|---|---|---|---|---|
| early | 100 | 50 | 950 | 650 |
| mid | 300 | 500 | 2400 | 2100 |
| later | 800 | 500 | **2900** | **2600** |

But my analysis scripts had `BSKEW_Y = 2400` hardcoded from an earlier moment.
That literal `2400` doesn't correspond to *any* actual gen_config state — it
was a hand-tuned constant that someone (me) misremembered or got from a
mental snapshot of an earlier render.

**Effect:** my "classify overflow tile by zone" function reported "all 781
overflow tiles are inside b-skew." Actually most of them are in the std-cell
strip just south of b-skew. The geometric story I told was wrong by 200 µm
in the y direction, which is a *small fraction of die height* but *the entire
height of the std-cell strip* I should have been talking about.

### 9.5.2 The fix

**RULE: never hardcode a macro coordinate. Always read from the DEF or
derive from gen_config.py.**

A reusable helper in any analysis script:

```python
import re
def parse_def_macros(def_path, master_names):
    """Return {instance_name: (master, x, y, orient)} from DEF COMPONENTS."""
    text = open(def_path).read()
    out = {}
    comp_re = re.compile(
        r"^    - (\S+)\s+(\S+)\s+\+\s+(?:SOURCE\s+\S+\s+\+\s+)?(?:FIXED|PLACED)"
        r"\s+\(\s+(\S+)\s+(\S+)\s+\)\s+(\S+)\s*;",
        re.MULTILINE,
    )
    for m in comp_re.finditer(text):
        inst, master = m.group(1), m.group(2)
        if master in master_names:
            out[inst] = (master, int(m.group(3))/1000, int(m.group(4))/1000, m.group(5))
    return out

# In every analysis or render script:
macros = parse_def_macros(DEF_PATH, {"skew_lane_a","skew_lane_b","cmd_unit","mac_tmem_cell"})
askew_x = min(x for (m,x,y,o) in macros.values() if m == "skew_lane_a")
bskew_y = min(y for (m,x,y,o) in macros.values() if m == "skew_lane_b")
```

Or read `gen_config.py` constants and run the placement formula yourself
(matches `gen_config.py` exactly):

```python
import re
gc = (SUB / "compute_array/gen_config.py").read_text()
def get(n, d): return float(re.search(rf"^\s*{n}\s*=\s*(\d+(?:\.\d+)?)", gc, re.MULTILINE).group(1))
CMD_OFFSET = get("CMD_OFFSET", 100); CMD_HALO = get("CMD_HALO", 50)
def snap(v): return math.ceil(v/50)*50
origin_y = snap(max(CMD_OFFSET + cmd_h + CMD_HALO, sbh))
bskew_y = origin_y - snap(sbh)   # 2900 - 300 = 2600
```

**Either approach yields the live value.** Never type the number directly.

### 9.5.3 Sanity check to add to every render script

After loading positions, print them. Compare against the actual DEF:

```python
print(f"derived: askew_x={askew_x}, bskew_y={bskew_y}, origin_xy=({ox},{oy})")
print(f"DEF says: a-skew[0]@{macros['gen_a_skew\\[0\\].u_a'][1:3]}, "
      f"b-skew[0]@{macros['gen_b_skew\\[0\\].u_b'][1:3]}, "
      f"cell[0][0]@{macros['gen_row\\[0\\].gen_col\\[0\\].u_cell'][1:3]}")
```

If the two don't match — STOP. Don't render.

### 9.5.4 The render-output sanity check

Every render that places macros should:
1. Read at least one macro instance position from the DEF
2. Assert that the rendered macro outline matches the DEF position to 1 µm
3. Print the assertion result before drawing

Without this check, the renderer can produce a picture that "looks plausible"
but is silently wrong by hundreds of µm.

### 9.5.5 Why this matters more than it seems

The b-skew misplacement was 200 µm in y. The DIE is 19900 µm tall. So the
visual shift on a chip-scale render is 1 %. **Visually undetectable**, but
it changes the answer to "is the overflow inside or outside the b-skew
macro?" from "outside" to "inside." That's a totally different diagnosis
and leads to totally different fix proposals.

The lesson: 1 % geometric drift is enough to invert the conclusion. **Renders
must be coordinate-perfect or they actively mislead.**

---

## 10. Common mistakes (don't repeat these)

1. **Hard-coding macro origins** instead of deriving from `gen_config.py`.
   The origin shifts when CMD_OFFSET changes.

2. **Drawing point-to-point straight lines** when the actual route is L-shaped
   Manhattan. Use the GR guide if you need real wire paths.

3. **Forgetting bit-reverse** so byte 0 lines connect to lane 0 (wrong)
   instead of lane 31.

4. **Reading old LEFs** (e.g., cmd_unit's 600×800 version) when the macro
   was re-hardened to 1200×1600. Always glob `runs/RUN_*/final/lef/*.lef`
   and pick `ls -t | head -1`.

5. **Plotting OBS rect counts instead of areas.** "26 rects" misleads;
   "74 % blocked area" is the actual truth.

6. **Assuming met5 is fully blocked over macros.** It's 13 % blocked
   inside mac_tmem_cell; parent CAN fly over.

7. **Confusing tile-aggregate congestion with edge-overflow.** Tile %=100
   in heatmap doesn't mean no overflow; edge overflow lives in the GR
   log table.

8. **Running standalone GR with different settings than OpenLane** and
   then being surprised the results differ. Always replicate the
   `openroad/common/grt.tcl` preamble.

9. **`# inline comment` in Tcl scripts.** Use new-line comments only.

10. **Killing the wrong openroad PID.** OpenLane spawns multiple openroad
    subprocesses with different PIDs across steps. `pkill openroad` will
    kill the live one. The user explicitly directed: **never kill long
    runs without confirmation.**

11. **Reading from an empty stub run dir** that a failed `--from` attempt
    left behind. Always sanity-check that the dir has `01-yosys-jsonheader/`
    and a recent `state_in.json`.

12. **Drawing `# opaque version` style mid-line Tcl comments** as noted
    in 2.4. Bears repeating because it'll happen again.

13. **Hand-coding a `BSKEW_Y = 2400` (or similar) constant** that's frozen
    to an earlier gen_config state, then changing gen_config and forgetting
    to update. See section 9.5. Constants that PAST-you computed from
    config become stale silently when FUTURE-you changes config. Always
    re-derive from `gen_config.py` (or the DEF) at render time. Treat any
    hardcoded macro x/y as a bug.

---

## Appendix: useful file locations

```
gen_config.py:             tech/sky130/submodules/<macro>/gen_config.py
config.yaml:               tech/sky130/submodules/<macro>/config.yaml
pin_order.cfg:             tech/sky130/submodules/<macro>/<macro>.pin_order.cfg
Run dirs:                  tech/sky130/submodules/<macro>/runs/RUN_<timestamp>/
Final LEF/GDS/DEF:         <run>/final/
Step DEFs:                 <run>/<NN>-<step-name>/<macro>.def
Step ODB:                  <run>/<NN>-<step-name>/<macro>.odb
GR log:                    <run>/<NN>-openroad-globalrouting/openroad-globalrouting.log
State pointer:             <run>/<NN>-*/state_in.json
PDK LEFs:                  ~/.volare/volare/sky130/versions/<sha>/sky130A/libs.ref/
PDK lib (timing):          ~/.volare/.../libs.ref/sky130_fd_sc_hd/lib/
Existing renderers:        tech/sky130/scripts/
Render outputs:            build/render/*.png and *.svg
```
