# A4 — Antenna rule sign-off

## Problem

Antenna effects are a real silicon failure mode: during plasma etch of
metal layers, long unterminated metal traces accumulate charge that can
discharge through gate oxides connected to those traces, blowing the
gate dielectric and producing parametric or hard failures. Foundries
define **antenna rules** specifying the maximum ratio of (metal area
collecting charge) : (gate area at the receiver) before a diode/jumper
is required.

Current state:
- ORFS has built-in antenna-fix steps (run during/after routing) but
  it's unclear whether they're enabled for our asap7 flow.
- No per-block antenna ARC (antenna rule check) report exists or has
  been reviewed.
- `tech/asap7/orfs/run.sh` runs with `RUN_KLAYOUT_DRC=1` but not
  necessarily KLayout antenna check.
- asap7's `/OpenROAD-flow-scripts/flow/platforms/asap7/drc/` may or
  may not contain antenna rules — verify.

For tape-out: zero unwaived antenna violations at every hierarchy
level.

## Acceptance criteria

1. A one-command invocation `./tech/asap7/orfs/antenna_check.sh
   <module>` returns exit 0 on clean, nonzero with a per-violation
   diagnostic on fail.
2. Antenna check runs on at least one leaf (recommend
   `mac_tmem_cell`) AND on `compute_array_tiny_bcast0` post-A1-fix and
   confirms clean (or produces a documented list of violations to fix).
3. Any antenna-repair pass needed (insertion of diodes / jumpers)
   integrated into the ORFS flow itself, not as a post-pass — so the
   final GDS already has fixes applied. Document whether OpenROAD's
   `repair_antennas` is being invoked and is sufficient.
4. Output: `reports/asap7/<module>/antenna.log` + single-line summary
   on stdout.

## Constraints

- **Don't regress timing or routing.** Antenna diodes consume area and
  add capacitance. If `repair_antennas` is added, verify hold/setup WNS
  still meets requirements.
- **Use the asap7 PDK's antenna rules** if they exist. Don't author
  new rules from foundry datasheets — the predictive PDK should ship
  its own, and they're the right ones for this flow.
- **Don't touch leaf RTL or hardening artifacts.** Same logic as A3 —
  antenna fixes are a routing-pass concern, not RTL.
- **Don't bypass with `-no_antenna_check` or similar escape hatches.**

## Inputs / references

- asap7 antenna rules to find:
  `/OpenROAD-flow-scripts/flow/platforms/asap7/drc/` (inside docker),
  also check `~/.volare/asap7/libs.tech/`
- OpenROAD commands: `check_antennas` (the report) and
  `repair_antennas` (the fix-up)
- Per-module routed output: `build/orfs/results/asap7/<module>/base/
  6_final.{gds,def}`
- ORFS Makefile target for antenna: search
  `/OpenROAD-flow-scripts/flow/Makefile` for `antenna`
- `tech/asap7/orfs/run.sh` — driver template

## Out of scope

- DRC clean (already handled by `RUN_KLAYOUT_DRC=1` in `run.sh`)
- LVS (A3), IR (A5), PDN (A1), hold (A2), chip_top (A6)
