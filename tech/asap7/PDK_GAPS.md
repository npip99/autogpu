# asap7 sign-off PDK gaps

What the asap7 ORFS install (the `openroad/orfs:latest` container's
`/OpenROAD-flow-scripts/flow/platforms/asap7/` tree) does NOT ship. Each
of these is a hard tape-out blocker. None can be fixed by tooling in this
repo — they are missing **PDK data** and the right fix is to commission
the data upstream, port to a real foundry PDK, or, where possible,
overlay public asap7-paper values for a "predictive sign-off" pass.

This file pairs with `DESIGN.md` ("Sign-off gaps" section). DESIGN.md
says *what the chip is missing*; this file says *why the PDK can't even
check*.

## Antenna sign-off — `tech/asap7/orfs/problems/A4_antenna.md`

### Evidence

```
$ grep -ci ANTENNA <asap7 tech LEF>
0
$ grep -ci ANTENNA <asap7 stdcell LEFs>
0
$ grep -ci antenna <asap7 klayout DRC deck (asap7.lydrc)>
0
```

The asap7 platform's own OpenLane config (`platforms/asap7/openlane/
config.tcl`) is explicit:

```tcl
# Asap7 has no antenna rules nor diode cells
set ::env(DIODE_INSERTION_STRATEGY) 0
```

### What's missing

| Data item                              | Why it's needed                                                  | Where it'd live           |
|----------------------------------------|------------------------------------------------------------------|---------------------------|
| `ANTENNAAREARATIO`, `ANTENNADIFFAREARATIO`, `ANTENNACUMDIFFAREARATIO`, `ANTENNADIFFSIDEAREARATIO`, `ANTENNACUMDIFFSIDEAREARATIO` on M1..M9 | OpenROAD's `check_antennas` cannot compute PAR/CAR without a per-layer limit | tech LEF, inside each `LAYER M*` block |
| `ANTENNAGATEAREA` on every input pin of every stdcell | Without it, no gate area to ratio against — `check_antennas` sees zero gates and reports "no violations" vacuously | stdcell LEF, inside each `PIN` block |
| `ANTENNADIFFAREA` on every input pin   | Defines diffusion-protected pins (no antenna protection) — without it, OpenROAD assumes every pin is unprotected | same |
| A diode/`ANTENNACELL` macro            | What `repair_antennas` inserts to fix unrepairable nets          | one of the stdcell LEFs   |

### Consequence

`./tech/asap7/orfs/antenna_check.sh <module>` invokes OpenROAD's
`check_antennas` against the post-route ODB. With **zero rules and zero
gate areas in the LEFs**, the check returns "0 violations" — but this
is a **vacuous pass**, not a sign-off. The script exits with code 4
("VACUOUS PASS") in this state so it cannot be mistaken for green.

ORFS's `repair_antennas` is already wired into the route step
(`scripts/global_route.tcl`, `scripts/detail_route.tcl` inside the docker
image, gated on `SKIP_ANTENNA_REPAIR*` env vars which we don't set —
defaults are 0). With zero rules it's a no-op: no violations are ever
found so no diodes/jumpers are ever inserted. The integration is
correct; the inputs are absent.

### Tape-out paths

In priority order:

1. **Port to a PDK that ships antenna data.** SkyWater sky130 has full
   antenna rules + a diode cell (`sky130_fd_sc_hd__diode_2`); IHP130
   ships analogous data. The `antenna_check.sh` script in this repo
   works against any ORFS platform — only the hard-coded `TECH_LEF`
   / `SC_LEF` paths change.
2. **(IMPLEMENTED, OPT-IN) Predictive overlay derived from the asap7
   paper (Clark et al., 2016).** Per-layer PAR/CAR from the paper's
   M1..M9 width / thickness / pitch numbers, plus a single conservative
   per-input-pin `ANTENNAGATEAREA`. Lives in
   `tech/asap7/orfs/asap7_antenna_overlay.lef` (the canonical
   human-readable source of the ratios) and
   `tech/asap7/orfs/scripts/inject_antenna_gate_area.py` (regenerates an
   offline-inspectable patched stdcell LEF at
   `build/asap7_stdcell_with_antenna.lef`). Opt in with
   `./tech/asap7/orfs/antenna_check.sh --with-overlay <module>` (or
   `ANTENNA_OVERLAY=1 ...`); without the flag the default vacuous-pass
   behavior is unchanged so users cannot mistake predictive for
   foundry sign-off. The overlay run prints a `(PREDICTIVE overlay
   — NOT foundry sign-off)` tag on its CLEAN/FAIL line and writes
   `ANTENNA_OVERLAY_NOTE` into the report. Implementation note:
   OpenROAD's `read_lef` silently drops antenna properties supplied
   via a second LEF that re-declares an already-declared layer, AND
   `read_db` restores the layer/master state from when the ODB was
   written — so `antenna_check.tcl` parses the overlay LEF in Tcl and
   attaches rules + gate areas to the in-memory tech / masters via
   the ODB API after `read_db`. The "patched stdcell LEF" is kept as
   an offline diff artifact only; it isn't loaded at check time.
3. **Synthesize a diode cell from existing stdcells.** A 1-input tied-
   to-gnd inverter with the input PIN tagged as the antenna cell would
   give `repair_antennas` something to insert. Requires LEF + LIB + GDS
   layout work. Out of scope for A4 — captured here for the future fix.
   The overlay path above gives `check_antennas` a real denominator
   but, because the route step's `repair_antennas` ran with zero rules
   loaded, the overlay run cannot itself trigger diode insertion. A
   future re-route-with-overlay pass would close that loop.

### What `antenna_check.sh` does ship

- Mechanical correctness: reads tech + stdcell + macro LEFs, the
  routed ODB, runs `check_antennas -verbose -report_file …`, writes
  `build/orfs/reports/asap7/<module>/antenna.log`.
- Honest exit code: distinguishes CLEAN (rules exist and check passes),
  FAIL (rules exist and violations remain), and VACUOUS PASS (no rules
  in the PDK — sign-off impossible).
- Hierarchy-aware: extracts `ADDITIONAL_LEFS` from the module's
  `*.config.mk` so leaf macros are loaded alongside the parent.
- Opt-in predictive overlay (`--with-overlay` / `ANTENNA_OVERLAY=1`):
  attaches the per-layer ratios from `asap7_antenna_overlay.lef` and a
  conservative `ANTENNAGATEAREA` on every input pin in-memory after
  `read_db`, then runs `check_antennas`. Output line is tagged
  `(PREDICTIVE overlay — NOT foundry sign-off)`. Default behavior is
  unchanged — must be explicitly requested.

When a real (foundry-verified) PDK gap closure ships, the overlay path
can be removed and the script will return meaningful CLEAN/FAIL results
from the platform LEFs alone with no further changes.
