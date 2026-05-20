#!/usr/bin/env python3
"""
Overlay m4.7 violation markers on the top-strip render of store
(GDS bbox 0..2400 × 2350..2400 → 8192×8192 image, stretched).

Output: build/render/store_top_strip_annotated.png
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw


REPO = Path(__file__).resolve().parents[3]
STRIP_PNG = REPO / "build/render/store_top_strip.png"
DRC_XML = REPO / "tech/sky130/submodules/store/runs/RUN_2026-05-19_17-16-15/59-klayout-drc/reports/drc_violations.klayout.xml"
OUT = REPO / "build/render/store_top_strip_annotated.png"

# strip bbox in µm
BBOX_X0, BBOX_Y0, BBOX_X1, BBOX_Y1 = 0, 2250, 2400, 2400


def load_violations():
    t = ET.parse(DRC_XML)
    out = []
    for item in t.getroot().iter("item"):
        v = item.find("values/value")
        if v is None or not v.text:
            continue
        pts = re.findall(r"([\d.]+),([\d.]+)", v.text)
        if not pts:
            continue
        x0 = min(float(p[0]) for p in pts)
        x1 = max(float(p[0]) for p in pts)
        y0 = min(float(p[1]) for p in pts)
        y1 = max(float(p[1]) for p in pts)
        out.append(((x0 + x1) / 2, (y0 + y1) / 2))
    return out


def main():
    img = Image.open(STRIP_PNG).convert("RGBA")
    W, H = img.size
    viols = load_violations()

    bbox_w = BBOX_X1 - BBOX_X0
    bbox_h = BBOX_Y1 - BBOX_Y0
    px_per_um_x = W / bbox_w
    px_per_um_y = H / bbox_h
    print(f"strip {W}×{H} px, bbox {bbox_w}×{bbox_h} µm "
          f"→ {px_per_um_x:.2f} px/µm horiz, {px_per_um_y:.2f} px/µm vert")

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for cx_um, cy_um in viols:
        x_um = cx_um - BBOX_X0
        y_um = cy_um - BBOX_Y0
        # In image: y increases downward, but our µm y increases upward in chip.
        px = x_um * px_per_um_x
        py = H - y_um * px_per_um_y
        # Marker: 1.5 µm radius (aspect now matches, so a circle in µm
        # appears as a circle in pixels). Big enough to see; small enough
        # not to swamp the pin pads (which are 0.6 µm wide).
        rx = 1.5 * px_per_um_x
        ry = 1.5 * px_per_um_y
        draw.ellipse((px - rx, py - ry, px + rx, py + ry),
                     outline=(255, 255, 0, 255), width=4,
                     fill=(255, 255, 0, 0))

    out_img = Image.alpha_composite(img, overlay)
    out_img.save(OUT, optimize=False)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
