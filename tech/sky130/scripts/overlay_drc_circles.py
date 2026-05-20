#!/usr/bin/env python3
"""
Overlay markers on each m4.7 violation directly on the 8192×8192 store
GDS render — preserve native resolution by drawing with PIL.

Outputs:
    build/render/store_gds_annotated.png       8192×8192 full chip
    build/render/store_gds_annotated_strip.png cropped to top 50 µm,
                                               same native px/µm
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parents[3]
GDS_PNG = REPO / "build/render/store_gds_tight.png"
DRC_XML = REPO / "tech/sky130/submodules/store/runs/RUN_2026-05-19_17-16-15/59-klayout-drc/reports/drc_violations.klayout.xml"
OUT_FULL = REPO / "build/render/store_gds_annotated.png"
OUT_STRIP = REPO / "build/render/store_gds_annotated_strip.png"

STORE_W, STORE_H = 2400, 2400


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


def draw_markers(img, viols, radius_px, color, outline, line_w):
    """Draw markers in image-pixel space. Returns new image."""
    W, H = img.size
    annotated = img.copy().convert("RGBA")
    overlay = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for (cx_um, cy_um) in viols:
        px = cx_um * W / STORE_W
        py = H - cy_um * H / STORE_H
        draw.ellipse((px - radius_px, py - radius_px,
                      px + radius_px, py + radius_px),
                     outline=outline, width=line_w, fill=color)
    annotated = Image.alpha_composite(annotated, overlay)
    return annotated


def main():
    img = Image.open(GDS_PNG)
    W, H = img.size
    viols = load_violations()
    print(f"image {W}x{H} (tight zoom); {len(viols)} violations")

    # ----- Full chip @ 8192×8192 -----
    # Marker radius 1.75 µm so it sits fully inside the chip (violations
    # are at y=2398, only 2 µm from top edge).
    radius_um = 1.5
    r_px = int(radius_um * W / STORE_W)
    full = draw_markers(img, viols, r_px,
                        color=(255, 255, 0, 220),
                        outline=(0, 0, 0, 255), line_w=2)
    full.save(OUT_FULL, optimize=False)
    print(f"wrote {OUT_FULL}")

    # ----- Zoomed strip: top 50 µm, full width, native px/µm preserved -----
    strip_um_h = 50  # µm tall
    strip_h_px = int(strip_um_h * H / STORE_H)  # image rows 0..strip_h_px
    img_strip = img.crop((0, 0, W, strip_h_px))
    # Marker radius bumped to 0.5 µm wide for the zoom (still visible at
    # native resolution; circles ~3 µm).
    r_px_strip = int(1.0 * W / STORE_W)
    strip = draw_markers(img_strip, viols, r_px_strip,
                         color=(255, 255, 0, 200),
                         outline=(255, 0, 0, 255), line_w=3)
    strip.save(OUT_STRIP, optimize=False)
    print(f"wrote {OUT_STRIP}  ({W}×{strip_h_px}, top {strip_um_h} µm)")


if __name__ == "__main__":
    main()
