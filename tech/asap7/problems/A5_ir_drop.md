# A5 — IR-drop / power-integrity sign-off

## Problem

Need static IR-drop analysis: for the routed PDN, given an estimate of
per-instance switching power, what is the worst-case voltage drop from
the chip's power pads through the grid to each instance? Tape-out
budget is typically ≤ 10 % of VDD (so for asap7 at VDD ≈ 0.7 V, max
drop ≈ 70 mV).

Current state:
- `psm` (OpenROAD's static IR / connectivity checker) runs as part of
  ORFS's `6_report` step but has been emitting PSM-0069 errors that we
  treated as tool artifacts.
- After A1 lands and the PDN is fixed, `psm` should at least report
  meaningful numbers, but we've never inspected its IR-drop output.
- No activity-factor model has been built. `report_power` can produce
  per-instance switching power from default activity factors; refining
  those needs RTL toggle activity which we don't have a flow for.
- For chip-level: we have no power-pad placement (pad ring doesn't
  exist; see chip_top.md, A6), so chip-level IR can't be done
  realistically yet.

## Acceptance criteria

1. A one-command `./tech/asap7/orfs/ir_drop.sh <module>` returns a
   worst-case Vdrop value + pass/fail vs. a configurable budget (default
   10 % of VDD).
2. Per-instance IR-drop report exists for at least one leaf
   (`mac_tmem_cell`) AND for `compute_array_tiny_bcast0` post-A1-fix.
3. If any instance exceeds the budget, output identifies it by name
   and location so the PDN can be reinforced (becomes a feedback loop
   into A1).
4. Activity-factor assumption documented in the report (e.g., "default
   ORFS activity = 0.2", or whatever number is used).
5. Output: `reports/asap7/<module>/ir_drop.log` + summary line.

## Constraints

- **Don't fix PDN here.** If IR-drop shows failures, file as PDN issue
  (extend A1 or as a follow-up). This task is sign-off, not repair.
- **Don't claim chip-level IR sign-off without real pad placement.**
  At the leaf and compute_array level, edge-supply assumption is
  acceptable (rings supply power uniformly). At chip_top level, real
  pad positions matter; if they don't exist yet (A6 may not provide
  them), document IR-drop at chip_top as "blocked on pad ring".
- **Activity factor must be reproducible.** No hand-tweaked numbers
  hidden in a tcl. Either use ORFS defaults (and say so) or feed in a
  documented switching-activity file (`.saif`) — but we don't currently
  generate `.saif`, so default is fine.
- **psm IS the in-tree IR tool.** Don't try to integrate commercial
  PrimePower / RedHawk. Use what's available.

## Inputs / references

- OpenROAD commands: `analyze_power_grid`, `report_power`,
  `set_pdnsim_net_voltage`, `check_power_grid` — these collectively
  drive psm's IR analysis. Find the right invocation.
- Per-module artifacts:
  `build/orfs/results/asap7/<module>/base/6_final.odb` (has full PDN)
- ORFS `6_report.tcl` (inside docker at
  `/OpenROAD-flow-scripts/flow/scripts/final_report.tcl` or similar) —
  see how psm is invoked there
- `tech/asap7/orfs/run.sh` — driver template

## Out of scope

- Fixing PDN issues that IR-drop surfaces (loops back to A1)
- Dynamic IR-drop (requires waveform analysis; out of scope for OSS
  flow)
- Pad-ring placement (A6's responsibility, and likely needs follow-up)
- Antenna (A4), LVS (A3), PDN connectivity (A1), hold (A2), chip_top
  integration (A6)
