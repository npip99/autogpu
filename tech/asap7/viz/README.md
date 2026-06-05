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
| `def_wires_3d.py`   | builds the to-scale `wires.glb` (wires + vias + real enclosures + RECT + **logic-cell gate boxes** + macros). `VIZ_CELLS=0` to omit gates. |
| `coverage_audit.py` | **exhaustive** char-level coverage: proves every non-whitespace char of NETS+SPECIALNETS routing is recognized |
| `verify_wires.py`   | the formal verification (13 checks); calls the coverage audit |
| `odb_dump.py`       | dumps routing tallies from OpenROAD's own database (run inside the ORFS image) |
| `cross_check_odb.py`| compares the extractor against `odb_dump.py`'s output — the authoritative independent witness |
| `klayout_render.py` | headless KLayout top-down render (run inside the ORFS image) |
| `asap7/*.json`      | per-layer z-heights, colors |
| `cell_sizes.json`   | std-cell master → (w,h) from the ASAP7 LEF (212 types) — drives the gate boxes |
| `build_macros.py`   | regenerates **full-res** macro routing meshes from each macro's own DEF (no decimation) → `out/macros/<type>.glb` (gitignored) |
| `feol.py`           | renders **logic-cell FEOL** (real device layers — well/fin/active/gate/LIG/LISD/V0 — of logic cells, fill excluded, macros pruned, below M1) from a design's GDS. `VIZ_DEF=<m.def> VIZ_GDS=<m.gds> VIZ_WINDOW=… python feol.py out/x.glb`. Heavy; fill's device "sea" (~800M faces) is intentionally skipped. |
| `klayout_wires.py`  | extract metal + via cuts from the **GDS** (full hierarchy, no DEF) over a window — runs inside the ORFS image. `MINLONG=0` keeps all geometry (incl. via pads); see header for the docker call. |
| `extrude_wires.py`  | extrude `klayout_wires.py` JSON → glb (metal boxes + via pillars on the real z-stack) |
| `build_gds_instances.py` | hierarchical GDS → instanced scene: top-cell metal (`parent.glb`) + one master mesh per cell type + `instances.json` (transforms). Macros reuse `out/macros/<type>.glb`. → `out/gds_inst/` |
| `render_wires_flat.py` | flat bright top-down PNG of extracted GDS wires (matplotlib, painter's order by layer) |
| `klayout_extract.py` | GDS polygon inspector — per-layer shape sizes in a window (debugging) |
| `viewers/`          | three.js fly-through pages (see **Web viewers** below): `macros_instanced.html` (working), `gds_instanced.html` / `gds_metal.html` (WIP) |
| `macros/`           | committed macro **metadata**: `placements.json` (positions) + `footprints.json` (box fallback). The large meshes are *not* committed — they're regenerated. |

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
python build_macros.py cmd_unit   # regen full-res macro routing from its DEF -> out/macros/cmd_unit.glb
python def_wires_3d.py            # -> $VIZ_OUT/wires.glb  (to scale, real widths)
# def_wires_3d args: <z-exaggeration> <out.glb> <macros 0|1> <pdn 0|1>
```
A macro with a regenerated mesh in `out/macros/` renders as its real full-res routing; types without
one render as a footprint box. (`cmd_unit` done; `skew_lane_a/b`, `mac_tmem_cell` are the same command.)
Full-res meshes are large (cmd ≈ 92 MB) and gitignored — never decimated, never committed.

## Web viewers (3D fly-through)

The `viewers/` pages are three.js fly-throughs served as static files. Serve the `viz/`
directory, then open a viewer in a browser:

```bash
cd tech/asap7/viz
python3 -m http.server 8017 --bind 0.0.0.0      # serves viz/ at http://<host>:8017/
```

| viewer | open | shows |
|--------|------|-------|
| `viewers/macros_instanced.html` | `/viewers/macros_instanced.html?win=1` | the working view: recursive macro instancer (chip-top → compute_array → macros, GPU-instanced). `?win=1` = bottom-left corner only; **drop the flag for the full chip**. Needs `out/base_routing.glb` + `out/parent_feol_logic.glb` + `out/macros/*.glb`. |
| `viewers/gds_instanced.html` | `/viewers/gds_instanced.html` | **WIP** — whole compute-array corner instanced straight from the single `6_final.gds`: top-cell metal + 43 instanced cell masters. Needs `out/gds_inst/`. |
| `viewers/gds_metal.html` | `/viewers/gds_metal.html` | **WIP** — one window of *all* GDS metal + via pillars, flattened. Needs `out/gds_metal.glb`. |

Controls: click to fly · mouse=look · WASD · Space/Shift=up/down · Ctrl=sprint · scroll=speed ·
`P`=copy pose · `H`=hide macros (where present).

### Regenerate the meshes the viewers load (all gitignored)

```bash
# macros_instanced.html
VIZ_CELLS=0 python def_wires_3d.py 1.0 out/base_routing.glb 0 1   # parent routing only (macros=0)
python feol.py out/parent_feol_logic.glb                         # parent-channel FEOL
python build_macros.py cmd_unit skew_lane_a skew_lane_b mac_tmem_cell

# gds viewers — straight from the parent GDS, no DEF. klayout_wires.py runs inside the ORFS
# image (see its header for the docker invocation); the others are plain python.
python build_gds_instances.py        # -> out/gds_inst/  (parent.glb + per-cell masters + instances.json)
python extrude_wires.py out/gds_metal.json out/gds_metal.glb     # after a klayout_wires.py extract
```

For the **full-die 2-D** Google-Maps-style viewer (the whole `chip_top`, tile pyramid + Leaflet),
see [`../CHIP_TOP_VIEWER.md`](../CHIP_TOP_VIEWER.md).

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
