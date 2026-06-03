# compute_array routing visualization + verification

Tools to extract the routed metal of `compute_array_abut` from the ORFS `6_final.def`,
build a to-scale 3D model (`wires.glb`), and **formally verify** that the model contains
every piece of routing geometry the DEF has — nothing dropped, nothing fabricated.

This exists because hand-rolled DEF parsing repeatedly missed constructs (wire-end
coordinate *extensions*, RECT fill patches, PDN via enclosures). The verification is built
to catch that entire class of bug.

## Layout

| file | purpose |
|------|---------|
| `config.py`         | paths/window, all overridable by env vars |
| `def_wires.py`      | DEF routing extractor: `extract` (wires), `extract_via_placements`/`extract_vias`, `extract_rects`, `extract_via_geom` (VIAS-section enclosures) |
| `def_wires_3d.py`   | builds the to-scale `wires.glb` (wires + vias + real enclosures + RECT patches + macro blocks) |
| `coverage_audit.py` | **exhaustive** char-level coverage: proves every non-whitespace char of NETS+SPECIALNETS routing is recognized |
| `verify_wires.py`   | the formal verification (13 checks); calls the coverage audit |
| `odb_dump.py`       | dumps routing tallies from OpenROAD's own database (run inside the ORFS image) |
| `cross_check_odb.py`| compares the extractor against `odb_dump.py`'s output — the authoritative independent witness |
| `klayout_render.py` | headless KLayout top-down render (run inside the ORFS image) |
| `asap7/*.json`      | per-layer z-heights, colors |
| `macros/`           | committed macro assets: detailed tile meshes (`<type>.glb`), `placements.json`, `footprints.json`. A type with a `.glb` renders as its real geometry; others render as a footprint box. `cmd_unit` is in (full detail); `skew_lane_a/b`, `mac_tmem_cell` to follow. |

## Run the verification

```bash
# deps: trimesh shapely scipy numpy gdstk  (see requirements.txt)
cd tech/asap7/viz
python verify_wires.py          # 13 checks; exit 0 = all pass
python coverage_audit.py        # the exhaustive coverage audit on its own
```

Override inputs via env (defaults in `config.py`):
`ORFS_RESULTS`, `VIZ_DESIGN`, `VIZ_DEF`, `VIZ_ODB`, `VIZ_GDS`, `VIZ_OUT`, `VIZ_LOD`, `VIZ_WINDOW`.

## Build the model

```bash
python def_wires_3d.py            # -> $VIZ_OUT/wires.glb  (to scale, real widths)
# args: <z-exaggeration> <out.glb> <macros 0|1> <pdn 0|1>
```
Macro bodies come from pre-rendered LOD tile meshes in `$VIZ_LOD` (built separately by gds2stl).

## What the verification proves

- **EXHAUSTIVE char coverage** — every non-whitespace character of the routing is consumed by a
  recognized pattern. Validated *sensitive*: dropping `( x y ext )` support flags exactly the
  40,463 extension coords it should. This is the guard the earlier checks lacked.
- **Conservation (Δ0)** — per-layer wire length, per-pair via counts, and RECT counts from the
  extractor exactly equal an independent re-derivation from the DEF.
- **Coordinate arity** — only `(x y)`, `(x y ext)`, `(x y a b)` forms exist and all are handled.
- **GLB tie-out** — the built mesh's face count equals `12 ×` the predicted box count.
- **Connectivity** — every via reaches routing metal or a component pin (the only un-rendered
  destinations are standard-cell pins, by design).

## Independent cross-check (optional, authoritative)

`odb_dump.py` loads OpenROAD's own `6_final.odb` and reports per-layer routing length / via /
rect tallies — a completely separate parser (the tool that *wrote* the routing). Run in the
pinned ORFS image:

```bash
. ../orfs/orfs_image.sh
BASE=$ORFS_RESULTS/$VIZ_DESIGN/base
docker run --rm -v "$BASE":/data:ro -v "$PWD":/viz:ro --entrypoint bash "$ORFS_IMAGE" \
  -c "/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -python /viz/odb_dump.py" \
  | sed -n '/ODB_JSON_BEGIN/,/ODB_JSON_END/p' | sed '1d;$d' > out/odb.json
python cross_check_odb.py        # asserts exact agreement, exit 0 = pass
```

Result: per-layer wire length, via counts, and RECT counts all match OpenROAD's database
**exactly** (length to 0.01 µm). Combined with the char-coverage audit (nothing unrecognized),
the routing extraction is verified complete and correct from two independent directions.
