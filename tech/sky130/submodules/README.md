# Per-submodule sky130 synthesis runs

Each directory under here synthesizes ONE module of chip_top in isolation —
producing its own GDS, area report, and timing report. Useful for:

- Bringing up the flow on a small module before debugging chip_top issues
- Per-module area / timing numbers (real, not synth-cell-count estimates)
- "Hardening" each module as a vendor macro for hierarchical synthesis
- Isolating which module (if any) is the routing bottleneck

## How it works

All submodule configs reuse the **same** sv2v-converted Verilog file at
`build/sv2v/chip_top.v`. OpenLane picks the chosen `DESIGN_NAME` from
within that file as the top module for that run.

Same speed flags as the chip_top config: `AREA 3` ABC strategy, no flatten,
no SHARE, no timing-driven placement. Macros and parameters are tuned per
module where needed (e.g., `smem` needs the SRAM macro; others don't).

## Run

From this directory:

```bash
./run.sh fp32_fma         # ~5-10 min, one of the simplest modules
./run.sh store            # similar
./run.sh mac_tmem_cell    # tiny leaf: 1 FMA + per-cell TMEM micro-storage
./run.sh compute_array    # the big one: 1024 mac_tmem_cell leaves + K-loop + drain
./run.sh load             # ~15-30 min (large FIFO arrays)
./run.sh cmdproc          # ~15-30 min (large imem)
./run.sh smem             # needs SRAM macro; ~10-20 min
```

Phase 7h replaced the old `mma` + `tmem` pair with `mac_tmem_cell` (the
single-cell leaf) wrapped 1024× by `compute_array`. The old monolithic
modules were dropped because their wide-port TMEM↔MMA interface
(32k-bit RMW) did not synthesize; the new architecture pushes
storage and the FMA into a per-(i, j) cell so each leaf is small and
the wide interface dissolves into local nets.

Or via Make:

```bash
make fp32_fma        # alias for ./run.sh fp32_fma
make all             # run every submodule in sequence
```

## Layout

```
submodules/
├── README.md       <- you are here
├── Makefile        <- per-module + all targets
├── run.sh          <- shared runner
└── <module>/
    ├── config.yaml <- OpenLane config; differs only on DESIGN_NAME + macros
    └── runs/       <- per-attempt outputs (gitignored)
```

## Sizing & config tips

Lessons learned hardening modules. Apply when writing or tuning a new
`config.yaml`:

### Pin layers — keep met5 PDN-only
Default `FP_IO_HLAYER: met3 met5` puts IO pins on met5, but chip-level PDN
also uses met5 for power straps. The two clash and produce hundreds of
*Metal5 spacing* DRC errors at the die edge. Set:
```yaml
FP_IO_HLAYER: met3
FP_IO_VLAYER: met2 met4
RT_MAX_LAYER: met4
```
This is true for every submodule we've hardened (barrier, load, store,
mac_array_small all needed this).

### Die size — proportional to actual cells
A massively oversized die makes the resizer insert huge buffer trees to
drive nets across long wires, which makes synth slow, blows up the GDS
size, and makes magic-writelef take hours. Target 30–50% utilization:
```
core_area ≈ instance_area / target_util
die_side  ≈ sqrt(core_area) + 100   # +50 µm border on each side
```
Get `instance_area` from any stage's `or_metrics_out.json` after global
placement (`design__instance__area`). At too-low util the resizer
inserts ~10× more buffers than necessary; at too-high util DR wedges on
routing congestion.

### Routing congestion on perimeter-heavy modules
Modules with hundreds of debug/observability pins (like `barrier` with
its bars_pending/expected/tx_pending output buses) need lower util so
pins have room to fan out. `FP_CORE_UTIL: 15` worked for barrier where
`30` failed in global routing.

### Hardened-macro children — PDN alignment
If your module instantiates hardened macros (like `compute_array`
contains 1024 `mac_tmem_cell` macros), the chip-level PDN strap pitch
+ offset must match the macro's internal pin pitch + offset, or you
get unconnected VPWR/VGND nodes (PSM-0069). For sky130 default PDN
(`FP_PDN_VPITCH=153.6`, `FP_PDN_VOFFSET=16.32`), place macro origins
at `(−4.72 + k·153.6, −10.08 + k·153.18)` and use a pitch that is a
multiple of 153.6 / 153.18.

### Standalone parameter overrides
If the module's SV default parameters don't match what `chip_top.sv`
passes (e.g. `INSTR_FIFO_DEPTH=8` for load vs 256 default), override
in the submodule config:
```yaml
SYNTH_PARAMETERS:
- "INSTR_FIFO_DEPTH=8"
```
Otherwise standalone synth uses the SV default — which can be wildly
different from the chip-top instantiation.

### When the IR-drop check blocks you
For dev runs where PDN connectivity is imperfect (e.g. macro-pin/strap
alignment hasn't been tuned yet), skip the check to get a viewable GDS:
```bash
EXTRA_OPENLANE_ARGS="--skip OpenROAD.IRDropReport" ./run.sh <module>
```
NOT safe for tape-out — only for getting through the flow to see the
GDS for inspection.
