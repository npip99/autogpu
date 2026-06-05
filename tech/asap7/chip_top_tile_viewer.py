"""Serve a chip_top Leaflet tile pyramid over HTTP.

The pyramid + viewer HTML are built by `render_chip_top_pyramid.py`; this
script is serve-only.

Usage:
    uv run --with pillow python3 tech/asap7/chip_top_tile_viewer.py serve [port]

Then open http://localhost:8000/ (or the chosen port). Pan with mouse drag,
zoom with scroll wheel.

The pillow dep is requested for parity with the builder script's invocation
pattern; serve mode itself only uses the stdlib.

Pyramid layout on disk (standard XYZ scheme, written by the builder):
    build/render/chip_top_tiles/{z}/{x}/{y}.png
    build/render/chip_top_tiles/index.html
"""

import http.server
import socketserver
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TILES_DIR = REPO / "build/render/chip_top_tiles"


def serve(port: int = 8000) -> None:
    if not TILES_DIR.exists():
        sys.exit(
            f"tiles not generated yet — build the pyramid first:\n"
            f"  uv run --with pillow python3 tech/asap7/render_chip_top_pyramid.py "
            f"--density 208 --workers 8\n"
            f"(see tech/asap7/CHIP_TOP_VIEWER.md)"
        )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(TILES_DIR), **kwargs)

        def log_message(self, fmt, *args):
            pass

    with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
        print(f"chip_top viewer: http://localhost:{port}/")
        print(f"  (or http://<this-machine>:{port}/ from elsewhere)")
        print(f"  Ctrl-C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "serve":
        sys.exit(f"usage: {sys.argv[0]} serve [port]")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    serve(port)


if __name__ == "__main__":
    main()
