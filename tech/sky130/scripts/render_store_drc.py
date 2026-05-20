#!/usr/bin/env python3
"""
Plot store's 32 m4.7 DRC violations on the store macro outline so you
can see where the gaps live.

Output: build/render/store_drc_m4_7.png
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt


REPO = Path(__file__).resolve().parents[3]
DRC_XML = REPO / "tech/sky130/submodules/store/runs/RUN_2026-05-19_17-16-15/59-klayout-drc/reports/drc_violations.klayout.xml"
STORE_W, STORE_H = 2400, 2400  # from config.yaml
OUT = REPO / "build/render/store_drc_m4_7.png"


def load_violations():
    t = ET.parse(DRC_XML)
    rects = []
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
        rects.append((x0, y0, x1, y1))
    return rects


def main():
    rects = load_violations()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # Two views: macro overview + zoomed strip at y=2398.
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))

    # Overview.
    ax1.add_patch(mpatches.Rectangle((0, 0), STORE_W, STORE_H,
                                     fill=False, edgecolor="black", linewidth=2))
    for x0, y0, x1, y1 in rects:
        # Inflate for visibility at this scale.
        ax1.add_patch(mpatches.Rectangle((x0 - 5, y0 - 5),
                                         (x1 - x0) + 10, (y1 - y0) + 10,
                                         facecolor="red", edgecolor="darkred", linewidth=0.5))
    ax1.set_xlim(-100, STORE_W + 100)
    ax1.set_ylim(-100, STORE_H + 100)
    ax1.set_aspect("equal")
    ax1.set_title(f"store macro 2400x2400 µm — {len(rects)} m4.7 violations\n(red dots inflated 10x for visibility)")
    ax1.set_xlabel("x (µm)")
    ax1.set_ylabel("y (µm)")
    ax1.grid(True, linestyle=":", alpha=0.4)
    ax1.axhspan(2398, 2400, alpha=0.1, color="orange")
    ax1.text(STORE_W/2, 2399, "violation strip", ha="center", va="center", fontsize=8, color="darkorange")

    # Strip view: zoom in horizontally, stretched vertically so rectangles
    # are visible. (Aspect not preserved — these are 0.47 × 0.38 µm and
    # the strip is 1658 µm wide, so equal aspect makes them invisible.)
    x_min = min(r[0] for r in rects) - 20
    x_max = max(r[2] for r in rects) + 20
    y_min = min(r[1] for r in rects) - 2
    y_max = max(r[3] for r in rects) + 2
    for x0, y0, x1, y1 in rects:
        # Draw as a wider marker (10 µm wide, full strip tall) so visible.
        ax2.add_patch(mpatches.Rectangle((x0 - 5, y0), 10, y1 - y0,
                                         facecolor="red", edgecolor="darkred", linewidth=0.3))
    ax2.set_xlim(x_min, x_max)
    ax2.set_ylim(y_min, y_max)
    ax2.set_title(f"strip view: 32 violation x-positions across y={y_min+2:.1f}..{y_max-2:.1f}\n"
                  f"(markers inflated to 10 µm wide; real width 0.47 µm)")
    ax2.set_xlabel("x (µm)")
    ax2.set_ylabel("y (µm)")
    ax2.grid(True, linestyle=":", alpha=0.4)

    fig.tight_layout()
    fig.savefig(OUT, dpi=300)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
