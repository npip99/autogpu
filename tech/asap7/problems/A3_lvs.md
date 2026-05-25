# A3 — LVS (Layout-vs-Schematic) infrastructure

## Problem

No LVS has ever been run on any hardened block in this project. LVS is
the ground-truth check that the post-route GDS matches the synthesized
netlist — it traces every transistor and net through the layout and
compares against the schematic.

For tape-out, LVS clean is a hard requirement at every hierarchy level
(leaves, compute_array, chip_top). Without it:
- Routing bugs (shorted nets, missing connections) ship to silicon
- pdngen via misses (the failure mode A1 is fixing) wouldn't be
  detectable by sign-off alone
- Hand-edits or scripted bypasses to DEF/GDS are unverifiable

The asap7 PDK does not ship LVS rule decks in the standard ORFS install
(checked `/OpenROAD-flow-scripts/flow/platforms/asap7/` — no `lvs/`
directory; only `drc/`, `KLayout/`, `lef/`, `gds/`, `lib/`). The
`~/.volare/asap7/libs.tech/` may or may not have them — verify.

Possible tools:
- **magic + netgen** — open-source LVS pair; widely used with SkyWater
  PDK. asap7 support is unclear.
- **KLayout LVS** — KLayout has a native LVS engine; rule files would
  need to be authored or ported from foundry sources.
- **OpenROAD `verilog_to_def` / built-in equivalence checks** — these
  exist but are weaker than full transistor-level LVS.

## Acceptance criteria

1. A one-command invocation `./tech/asap7/orfs/lvs.sh <module>` returns
   exit 0 on LVS clean, nonzero with a useful diagnostic on mismatch.
2. LVS clean demonstrated on at least one leaf (recommend
   `mac_tmem_cell` — small, well-understood) AND on the post-A1-fix
   `compute_array_tiny_bcast0`.
3. The check is reproducible from build artifacts only — no manual
   intervention, no GUI required.
4. The check works hierarchically: at compute_array level, leaves may
   be black-box; at chip_top level, compute_array/smem/etc. may be
   black-box. Document the hierarchy mode used.
5. Output: a `reports/asap7/<module>/lvs.log` and a single-line summary
   on stdout.

## Constraints

- **Don't modify the hardening flow.** LVS is a post-hardening
  sign-off. It reads `6_final.gds` + `6_final.v` (or `6_final.spef`)
  and reports.
- **Don't rely on commercial tools.** Solution must be reproducible
  using open-source tooling available in the current docker image or
  installable via `apt`/`pip`/`brew`/source build. If you need a tool
  that isn't installed, add it to a Dockerfile that extends
  `openroad/orfs:latest`.
- **PDK gap is real.** asap7 may genuinely lack production-grade LVS
  rules. If so, document the gap clearly (this becomes a tape-out
  blocker that no amount of tooling can paper over) and pick the most
  credible available option (e.g., KLayout LVS with hand-rolled rules
  from the asap7 LEF/GDS for the cells we actually use).
- **Don't change leaf RTL or hardening artifacts.** LVS may catch real
  bugs; if it does, file them as separate issues. Your job is the
  tooling, not the fixes.
- **Hierarchical LVS preferred** to flat — compute_array fully flat
  with all 1089 macros expanded is huge. Document whatever mode you use
  and why.

## Inputs / references

- PDK install host-side: `~/.volare/asap7/libs.tech/`,
  `~/.volare/asap7/libs.ref/`
- PDK install docker-side:
  `/OpenROAD-flow-scripts/flow/platforms/asap7/`
- Per-module artifacts:
  `build/orfs/results/asap7/<module>/base/6_final.{gds,v,def,spef}`
- ORFS source: `/OpenROAD-flow-scripts/flow/` — search for any
  LVS-related Makefile targets or tcl scripts
- `tech/asap7/orfs/run.sh` — template for docker-based invocations
- `tech/asap7/render_layout.py` — KLayout invocation template (we
  already use KLayout for rendering, so KLayout LVS may be a natural
  extension)

## Out of scope

- Fixing any LVS errors that come up (file as separate issues per
  block — these become Pool B per-macro tasks)
- PDN, hold timing, antenna, IR, chip_top (other A and B tasks)
