"""Build a Leaflet tile pyramid + HTTP server for the chip_top GDS render.

Reads `build/render/chip_top_asap7.png` (the 8192×8192 KLayout dump produced
at the end of the chip_top harden), slices it into a Google-Maps-style
tile pyramid, and serves it via a tiny HTTP server with a Leaflet-based
viewer.

Usage:
    # Generate tiles (one-time, ~1 min)
    uv run --with pillow python3 tech/asap7/chip_top_tile_viewer.py tiles

    # Serve (any time after)
    uv run --with pillow python3 tech/asap7/chip_top_tile_viewer.py serve [port]

Then open http://localhost:8000 (or the chosen port). Pan with mouse drag,
zoom with scroll wheel. Higher zoom = more detail.

Tile pyramid layout (standard XYZ scheme):
    build/render/chip_top_tiles/{z}/{x}/{y}.png

Where z=0 fits the whole chip in one 256×256 tile, and each higher z
doubles the tile grid (4x the pixels).
"""

import http.server
import socketserver
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
SOURCE_PNG = REPO / "build/render/chip_top_asap7.png"
TILES_DIR = REPO / "build/render/chip_top_tiles"
VIEWER_HTML = TILES_DIR / "index.html"

TILE_SIZE = 256  # standard Leaflet tile size

# 8192 px = 2^13. With 256 px tiles, max zoom is 2^(13-8) = 2^5 = 32 tiles.
# So we generate z=0..5 (6 zoom levels) for an 8192-px base.
MAX_ZOOM_FROM_8K = 5  # 2^5 = 32 tiles per side at max zoom


def make_tiles(source_png: Path, out_dir: Path) -> None:
    """Slice the source PNG into a tile pyramid."""
    out_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(source_png).convert("RGB")
    src_w, src_h = img.size
    print(f"source: {src_w}×{src_h} from {source_png.relative_to(REPO)}")

    # Pad to a square so tile math is clean (chip is square-ish anyway).
    side = max(src_w, src_h)
    if (src_w, src_h) != (side, side):
        canvas = Image.new("RGB", (side, side), (0, 0, 0))
        canvas.paste(img, ((side - src_w) // 2, (side - src_h) // 2))
        img = canvas
        print(f"  padded to {side}×{side}")

    max_zoom = MAX_ZOOM_FROM_8K
    for z in range(max_zoom + 1):
        tiles_per_side = 2 ** z
        scaled_side = tiles_per_side * TILE_SIZE
        # Downscale the source to this zoom level's total pixel size
        zoom_img = img.resize((scaled_side, scaled_side), Image.LANCZOS)
        n_tiles = 0
        for x in range(tiles_per_side):
            (out_dir / str(z) / str(x)).mkdir(parents=True, exist_ok=True)
            for y in range(tiles_per_side):
                # Leaflet's y-axis runs top-down. PIL's crop is also top-down.
                left, top = x * TILE_SIZE, y * TILE_SIZE
                tile = zoom_img.crop((left, top, left + TILE_SIZE, top + TILE_SIZE))
                tile.save(out_dir / str(z) / str(x) / f"{y}.png", "PNG", optimize=True)
                n_tiles += 1
        print(f"  z={z}: {tiles_per_side}×{tiles_per_side} = {n_tiles} tiles ({scaled_side}×{scaled_side} px total)")

    # Write the Leaflet HTML viewer
    VIEWER_HTML.write_text(make_viewer_html(max_zoom))
    print(f"\nWrote {VIEWER_HTML.relative_to(REPO)}")
    print(f"Serve: uv run --with pillow python3 {Path(__file__).relative_to(REPO)} serve")


def make_viewer_html(max_zoom: int) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>chip_top tile viewer</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        html, body, #map {{ height: 100%; margin: 0; padding: 0; }}
        #map {{ background: #111; }}
        .info {{
            position: absolute; top: 8px; right: 8px;
            background: rgba(0,0,0,0.7); color: #fff;
            padding: 8px 12px; font-family: monospace; font-size: 11px;
            border-radius: 4px; z-index: 1000;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="info">
        chip_top.gds — pan to navigate, scroll to zoom<br>
        z=0 whole die · z={max_zoom} max detail (~9.4 µm/px → ~0.3 µm/px)
    </div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        // Use Leaflet's L.CRS.Simple for an XY grid (not geographic).
        var map = L.map('map', {{
            crs: L.CRS.Simple,
            minZoom: 0,
            maxZoom: {max_zoom},
            zoomControl: true,
            attributionControl: false,
        }});

        // Tile layer pulls /{{z}}/{{x}}/{{y}}.png from this server.
        L.tileLayer('./{{z}}/{{x}}/{{y}}.png', {{
            tileSize: {TILE_SIZE},
            noWrap: true,
            minZoom: 0,
            maxZoom: {max_zoom},
            bounds: [[-{TILE_SIZE}, 0], [0, {TILE_SIZE}]],
        }}).addTo(map);

        // Fit view to the whole tile at z=0.
        var bounds = [[-{TILE_SIZE}, 0], [0, {TILE_SIZE}]];
        map.fitBounds(bounds);
        map.setView([-{TILE_SIZE // 2}, {TILE_SIZE // 2}], 1);
    </script>
</body>
</html>
"""


def serve(port: int = 8000) -> None:
    """Serve the tiles directory over HTTP."""
    if not TILES_DIR.exists():
        sys.exit(f"tiles not generated yet — run: {sys.argv[0]} tiles")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(TILES_DIR), **kwargs)

        def log_message(self, fmt, *args):
            pass  # quiet

    with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
        print(f"chip_top viewer: http://localhost:{port}/")
        print(f"  (or http://<this-machine>:{port}/ from elsewhere)")
        print(f"  Ctrl-C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("tiles", "serve"):
        sys.exit(f"usage:\n  {sys.argv[0]} tiles       # generate tile pyramid\n"
                 f"  {sys.argv[0]} serve [port]  # serve at http://localhost:port/")
    if sys.argv[1] == "tiles":
        if not SOURCE_PNG.exists():
            sys.exit(f"missing source: {SOURCE_PNG}\n"
                     f"  Re-run the chip_top harden to regenerate, OR adjust SOURCE_PNG path.")
        make_tiles(SOURCE_PNG, TILES_DIR)
    else:
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        serve(port)


if __name__ == "__main__":
    main()
