"""Render chip_top.gds to a Leaflet tile pyramid at transistor-level density.

Strategy:
  1. Divide the chip into "render-tiles" of 16k × 16k px each (at target
     density, e.g. 463 px/µm → 34.6 µm per render-tile).
  2. N parallel workers each call klayout-in-docker for their render-tiles.
  3. After all render-tiles are written, slice each into 64×64 = 4096
     Leaflet tiles (256 px each) at the max zoom level.
  4. Downsample to lower zoom levels via PIL.
  5. Write Leaflet viewer HTML.

Resumable: skips render-tiles whose output already exists. Kill + restart
without losing work.

Usage:
    uv run --with pillow python3 tech/asap7/render_chip_top_pyramid.py \
        --density 463 --workers 8

For lower-density preview (much faster):
    uv run --with pillow python3 tech/asap7/render_chip_top_pyramid.py \
        --density 27 --workers 4   # ~15 min, stdcell-visible
"""

import argparse
import math
import multiprocessing as mp
import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

# PIL refuses to open large images by default ("decompression bomb").
# Our render-tiles are intentionally large (16384²) — disable the limit.
Image.MAX_IMAGE_PIXELS = None

REPO = Path(__file__).resolve().parents[2]
GDS = REPO / "build/orfs/results/asap7/chip_top/base/6_final.gds"
TILES_DIR = REPO / "build/render/chip_top_tiles"
RENDER_TILES_DIR = REPO / "build/render/chip_top_render_tiles"
VIEWER_HTML = TILES_DIR / "index.html"

CHIP_W_UM = 2400.0
CHIP_H_UM = 1800.0

TILE_PX = 256             # Leaflet tile size
# RENDER_TILE_PX is the pixels per side of each klayout invocation. Larger =
# fewer docker spawns but slower per-tile. 16384 is the production setting;
# smaller values (e.g. 4096) are useful for fast iteration on the slicing
# pipeline since each render is ~16× faster. Overridden by --render-tile-px.
RENDER_TILE_PX = 16384


def render_one_tile(args):
    """Render one render-tile via klayout-in-docker. Returns (idx, path, took_sec)."""
    idx, x0, y0, x1, y1, out_path = args
    if out_path.exists():
        return (idx, out_path, 0.0)  # already done — resume
    rel_out = out_path.relative_to(REPO)
    rel_gds = GDS.relative_to(REPO)
    cmd = [
        "sg", "docker", "-c",
        f"docker run --rm --user {os.getuid()}:{os.getgid()} "
        f"-v {REPO}:/work "
        f"-v {Path.home()}/.volare:{Path.home()}/.volare "
        f"-e RES={RENDER_TILE_PX} "
        f"-e CROP_BOX={x0:.4f},{y0:.4f},{x1:.4f},{y1:.4f} "
        f"-e LAYOUT_PATH=/work/{rel_gds} "
        f"-e OUT_PNG=/work/{rel_out} "
        f"-e PDK_ROOT={Path.home()}/.volare "
        f"ghcr.io/efabless/openlane2:2.3.10 "
        f"klayout -b -r /work/tech/asap7/render_corner.py"
    ]
    t0 = time.time()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    took = time.time() - t0
    if res.returncode != 0 or not out_path.exists():
        return (idx, None, took, res.stderr[-500:])
    return (idx, out_path, took, None)


def make_render_tiles(density_px_per_um: float, workers: int) -> dict:
    """Generate the high-res render-tiles in parallel.

    Each render-tile = RENDER_TILE_PX px square, covering
    (RENDER_TILE_PX / density) µm square.
    Returns {(col, row): path}.
    """
    chunk_um = RENDER_TILE_PX / density_px_per_um
    n_cols = math.ceil(CHIP_W_UM / chunk_um)
    n_rows = math.ceil(CHIP_H_UM / chunk_um)
    n_tiles = n_cols * n_rows
    print(f"Render plan: {n_cols} × {n_rows} = {n_tiles} render-tiles "
          f"of {chunk_um:.2f}×{chunk_um:.2f} µm at {RENDER_TILE_PX}×{RENDER_TILE_PX} px")
    print(f"Density: {density_px_per_um:.1f} px/µm ({1000/density_px_per_um:.2f} nm/px)")
    print(f"Workers: {workers}")
    print(f"ETA: ~{n_tiles * 48 / workers / 60:.1f} min")
    print()
    RENDER_TILES_DIR.mkdir(parents=True, exist_ok=True)

    # IMPORTANT: do NOT clamp x1/y1 to the chip extent. KLayout renders a
    # non-square zoom_box into a square (RES×RES) PNG with aspect distortion,
    # which would misalign the boundary render-tiles. Always render the full
    # chunk_um × chunk_um square; the off-chip area renders as background
    # (black). This guarantees uniform px/µm across all render-tiles.
    jobs = []
    tile_paths = {}
    for col in range(n_cols):
        for row in range(n_rows):
            x0 = col * chunk_um
            y0 = row * chunk_um
            x1 = (col + 1) * chunk_um
            y1 = (row + 1) * chunk_um
            out_path = RENDER_TILES_DIR / f"rt_{col:03d}_{row:03d}.png"
            jobs.append((len(jobs), x0, y0, x1, y1, out_path))
            tile_paths[(col, row)] = out_path

    t_start = time.time()
    completed = 0
    with mp.Pool(workers) as pool:
        for result in pool.imap_unordered(render_one_tile, jobs):
            completed += 1
            idx = result[0]
            took = result[2]
            err = result[3] if len(result) > 3 else None
            status = "✓" if result[1] else "✗"
            elapsed = time.time() - t_start
            eta = (elapsed / completed) * (len(jobs) - completed) if completed else 0
            print(f"  [{completed}/{len(jobs)}] {status} idx={idx} took={took:.0f}s "
                  f"elapsed={elapsed/60:.1f}m eta={eta/60:.1f}m")
            if err:
                print(f"      ERR: {err}")
    return tile_paths


def _slice_one_rt(args):
    """Worker: slice one render-tile across z_breakdown..max_zoom.

    Y axis: GDS y goes UP, Leaflet y goes DOWN. The render-tile PNG itself
    is correct (KLayout puts high-GDS-y at PNG-top), but the row stacking
    must be inverted: row=0 of the chip (bottom in GDS) must end up at the
    BOTTOM of the Leaflet map (largest abs_y), not the top.
    """
    col, row, rt_path_str, z_breakdown, max_zoom, n_rows = args
    rt_path = Path(rt_path_str)
    if not rt_path.exists():
        return 0
    img = Image.open(rt_path).convert("RGB")
    n_written = 0
    for z in range(z_breakdown, max_zoom + 1):
        shrink = max_zoom - z
        scaled_size = RENDER_TILE_PX // (2 ** shrink)
        rt_scaled = img if shrink == 0 else img.resize(
            (scaled_size, scaled_size), Image.LANCZOS)
        tps_per_rt = scaled_size // TILE_PX
        flipped_row = n_rows - 1 - row
        for tx in range(tps_per_rt):
            for ty in range(tps_per_rt):
                abs_x = col * tps_per_rt + tx
                abs_y = flipped_row * tps_per_rt + ty
                tile = rt_scaled.crop((tx * TILE_PX, ty * TILE_PX,
                                        (tx + 1) * TILE_PX,
                                        (ty + 1) * TILE_PX))
                out = TILES_DIR / str(z) / str(abs_x) / f"{abs_y}.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                # No optimize=True — too slow and marginal size benefit on
                # chip-render tiles (which are mostly uniform fill).
                tile.save(out, "PNG")
                n_written += 1
    img.close()
    return n_written


def slice_render_tiles_to_leaflet(tile_paths: dict, density_px_per_um: float,
                                    max_zoom: int, workers: int = 8) -> None:
    """Parallel per-rt slicing for high zoom levels, mosaic for low."""
    n_per_rt_side = RENDER_TILE_PX // TILE_PX  # 64 at our default
    z_breakdown = max_zoom - int(math.log2(n_per_rt_side))
    print(f"\nz_breakdown = {z_breakdown}: per-rt slicing for z>={z_breakdown}, "
          f"mosaic for z<{z_breakdown}")
    # Parallel pass 1: per-render-tile slicing for z=z_breakdown..max_zoom
    n_rows = max(r for _, r in tile_paths) + 1
    jobs = [(col, row, str(p), z_breakdown, max_zoom, n_rows)
            for (col, row), p in tile_paths.items() if p and p.exists()]
    print(f"  parallel-slicing {len(jobs)} render-tiles with {workers} workers...")
    t0 = time.time()
    total_tiles = 0
    with mp.Pool(workers) as pool:
        for i, n in enumerate(pool.imap_unordered(_slice_one_rt, jobs), 1):
            total_tiles += n
            elapsed = time.time() - t0
            print(f"    [{i}/{len(jobs)}] +{n} tiles  ({total_tiles} so far, "
                  f"{total_tiles/max(elapsed,0.1):.0f}/s)")

    # Pass 2: for z < z_breakdown, stitch a low-res mosaic from the
    # render-tiles (each shrunk to TILE_PX) then slice + downscale further.
    if z_breakdown > 0:
        # Mosaic side: 2^z_breakdown tiles per side (each TILE_PX), but
        # padded to square. n_per_rt_side = 64 normally.
        # At z=z_breakdown each rt becomes 1 lf tile.
        n_cols = max(c for c, _ in tile_paths) + 1
        n_rows = max(r for _, r in tile_paths) + 1
        side_tiles = 2 ** z_breakdown
        mosaic = Image.new("RGB", (side_tiles * TILE_PX, side_tiles * TILE_PX), (0, 0, 0))
        for (col, row), rt_path in tile_paths.items():
            if not rt_path or not rt_path.exists():
                continue
            img = Image.open(rt_path).convert("RGB")
            shrunk = img.resize((TILE_PX, TILE_PX), Image.LANCZOS)
            # Y flip: chip row=0 (bottom in GDS) → bottom of Leaflet map.
            flipped_row = n_rows - 1 - row
            mosaic.paste(shrunk, (col * TILE_PX, flipped_row * TILE_PX))
            img.close()
        for z in range(z_breakdown - 1, -1, -1):
            tps = 2 ** z
            scaled = mosaic.resize((tps * TILE_PX, tps * TILE_PX), Image.LANCZOS)
            for x in range(tps):
                (TILES_DIR / str(z) / str(x)).mkdir(parents=True, exist_ok=True)
                for y in range(tps):
                    tile = scaled.crop((x * TILE_PX, y * TILE_PX,
                                         (x + 1) * TILE_PX, (y + 1) * TILE_PX))
                    tile.save(TILES_DIR / str(z) / str(x) / f"{y}.png",
                              "PNG", optimize=True)
            print(f"  mosaic-derived z={z}: {tps}×{tps} tiles")

    VIEWER_HTML.write_text(make_viewer_html(max_zoom))
    print(f"\nWrote {VIEWER_HTML.relative_to(REPO)}")


def make_viewer_html(max_zoom: int) -> str:
    # 2x oversampling for crispness: zoomOffset=1 makes Leaflet fetch one
    # zoom level deeper than the natural viewport zoom. Each 256px source
    # tile renders into 256 screen-px containing 2x the source detail.
    # Result: text/wires never look blurry at any zoom (until you exceed
    # the deepest real tiles, then visual over-zoom kicks in).
    zoom_offset = 1
    native_max_view = max_zoom - zoom_offset   # deepest viewport zoom backed by real tiles
    visual_max = native_max_view + 6           # +6 = 64x extra magnification (blurry)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>chip_top tile viewer</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>html,body,#map{{height:100%;margin:0;padding:0}}#map{{background:#111}}
.info{{position:absolute;top:8px;right:8px;background:rgba(0,0,0,0.7);color:#fff;
padding:8px 12px;font-family:monospace;font-size:11px;border-radius:4px;z-index:1000}}</style>
</head><body><div id="map"></div>
<div class="info">chip_top.gds — pan/scroll to navigate<br>
  2x oversampled (zoomOffset={zoom_offset}); native viewport max z={native_max_view};
  visual zoom up to z={visual_max} (upscaled)</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
var map = L.map('map',{{crs:L.CRS.Simple,minZoom:0,maxZoom:{visual_max},attributionControl:false}});
L.tileLayer('./{{z}}/{{x}}/{{y}}.png',{{
  tileSize:{TILE_PX}, noWrap:true,
  minZoom:0, maxZoom:{visual_max},
  // zoomOffset=1: at viewport zoom V we fetch z=V+1 tiles, displayed at
  // native 256 px. Effectively 2x oversampling — always crisp.
  zoomOffset:{zoom_offset},
  maxNativeZoom:{native_max_view}
}}).addTo(map);
map.setView([-{TILE_PX // 2},{TILE_PX // 2}],1);
</script></body></html>
"""


def main():
    global RENDER_TILE_PX
    p = argparse.ArgumentParser()
    p.add_argument("--density", type=float, default=463.0,
                    help="px/µm at max zoom (default 463 = ~2 nm/px, transistor level)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--render-tile-px", type=int, default=RENDER_TILE_PX,
                    help="pixels per side of each klayout render-tile "
                         f"(default {RENDER_TILE_PX}). Smaller = faster iteration "
                         "for testing the slicing pipeline.")
    args = p.parse_args()
    RENDER_TILE_PX = args.render_tile_px

    if not GDS.exists():
        sys.exit(f"missing {GDS}")

    # max_zoom = ceil(log2(chip_dim_px / TILE_PX))
    chip_dim_px = max(CHIP_W_UM, CHIP_H_UM) * args.density
    max_zoom = math.ceil(math.log2(chip_dim_px / TILE_PX))
    print(f"=== chip_top pyramid render ===")
    print(f"max_zoom: {max_zoom} ({2**max_zoom * TILE_PX} px per side at top zoom)")
    print()

    t0 = time.time()
    tile_paths = make_render_tiles(args.density, args.workers)
    print(f"\nAll render-tiles done in {(time.time() - t0)/60:.1f} min")

    slice_render_tiles_to_leaflet(tile_paths, args.density, max_zoom, workers=args.workers)
    print(f"\nDone. Serve with:\n  uv run --with pillow python3 tech/asap7/chip_top_tile_viewer.py serve 8765")


if __name__ == "__main__":
    main()
