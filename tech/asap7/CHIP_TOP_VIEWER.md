# chip_top tile viewer

Google-Maps-style web viewer for `chip_top.gds`. Renders the full die into a
tile pyramid with KLayout, then serves the pyramid via a tiny HTTP server with
a Leaflet front-end. Pan/zoom navigates the chip; zoom-in resolves wires and
(at high enough density) individual transistors.

## Files

| File | Purpose |
|---|---|
| `render_chip_top_pyramid.py` | Build the tile pyramid (KLayout-in-docker → PNG render-tiles → Leaflet tiles). |
| `chip_top_tile_viewer.py`    | Tiny HTTP server for the pyramid. |
| `render_corner.py`           | Per-render-tile KLayout script (already tracked). Sets vibrant per-layer colors, hides implant/well/fill noise. |

## Prerequisites

- Docker (the renderer pulls `ghcr.io/efabless/openlane2:2.3.10` for klayout)
- `chip_top` harden completed — the renderer reads
  `build/orfs/results/asap7/chip_top/base/6_final.gds`
- ASAP7 PDK at `~/.volare` (the docker mount expects it there)
- `uv` (used to install `pillow` inline)

## Build the pyramid

```bash
uv run --with pillow python3 tech/asap7/render_chip_top_pyramid.py \
    --density 52 \
    --workers 8
```

Flags:
- `--density <px/µm>` — pixels per µm at max zoom. Higher = finer detail, more
  render-tiles, longer render. See table below.
- `--workers <N>` — parallel klayout invocations (one docker container per worker).
- `--render-tile-px <N>` — pixels per side of each klayout render. Default 16384
  (production). Smaller is **not** faster overall — more render-tiles means more
  klayout startup overhead; only useful for testing the slicer.

The script is **resumable** — render-tiles that already exist on disk are skipped.
Kill + restart without losing work.

### Density / cost reference

| Density (px/µm) | nm/px | What's visible | Render-tiles | Render time (8 workers) | Pyramid size |
|---|---|---|---|---|---|
| 13  | 77  | macros, big buses          | 4    | ~1.5 min  | ~250 MB   |
| 27  | 37  | stdcells just resolvable   | 12   | ~5 min    | ~1.8 GB   |
| 52  | 19  | M2 wires readable          | 48   | ~6 min    | ~5.5 GB   |
| 208 | 4.8 | poly/M1, ~stdcell-internal | 713  | ~90 min   | ~65 GB    |
| 463 | 2.2 | individual transistors     | ~3500 | ~6 hr     | ~250 GB   |

Pick the lowest density that shows what you need. At 52 px/µm the chip is fully
useful for navigating macros + inter-macro buses.

## Serve the pyramid

```bash
# Recommended: run inside a named gnu screen session so it survives
# terminal disconnect.
screen -dmS chip-viewer bash -c \
  'uv run --with pillow python3 tech/asap7/chip_top_tile_viewer.py serve 8765 \
   2>&1 | tee /tmp/chip_viewer.log'
```

Then open `http://<host>:8765/` in a browser. The server has no auth — only
expose on a trusted network.

To stop: `screen -S chip-viewer -X quit`.

## Pyramid layout on disk

```
build/render/
├── chip_top_render_tiles/      # 16k×16k PNG per klayout invocation (intermediate)
│   ├── rt_000_000.png
│   └── ...
└── chip_top_tiles/             # Leaflet pyramid (what the server serves)
    ├── index.html              # auto-generated viewer page
    ├── 0/0/0.png               # z=0: whole chip in one 256×256 tile
    ├── 1/{x}/{y}.png           # z=1: 2×2 tiles
    └── {z}/{x}/{y}.png
```

Y is **flipped** relative to GDS — Leaflet's y-axis runs top-down while GDS y
goes up. The slicer inverts the row index so chip-bottom appears at the bottom
of the viewer.

## Viewer notes

The viewer uses `zoomOffset: 1` on the Leaflet tile layer. At viewport zoom V
it fetches `(V+1)/x/y.png` and displays the 256-px tile at 256 screen-px,
giving **2× oversampling** — content stays crisp at every zoom instead of
looking blurry at half-zoom. Side effect: max viewport zoom is `max_native - 1`,
with visual over-zoom (blurry upscale) available beyond that.

To re-tune the viewer without re-rendering, edit `make_viewer_html()` in
`render_chip_top_pyramid.py` and re-run the script — the render step is a
no-op on existing tiles.

## Common operations

```bash
# Quick: low-density preview (~1.5 min total)
uv run --with pillow python3 tech/asap7/render_chip_top_pyramid.py \
    --density 13 --workers 8

# Just re-slice (e.g. after editing viewer HTML or slicer logic) —
# render step skips all existing render-tiles.
uv run --with pillow python3 tech/asap7/render_chip_top_pyramid.py \
    --density 52 --workers 8

# Wipe everything and rebuild from scratch
rm -rf build/render/chip_top_tiles build/render/chip_top_render_tiles
```
