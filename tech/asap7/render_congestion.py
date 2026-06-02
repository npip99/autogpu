#!/usr/bin/env python3
"""Render a GRT congestion-N.rpt as a 2D heatmap over the chip floorplan.

Useful for visually identifying where GRT is fighting overflow. Each
bbox in the report has (lo_x, lo_y) - (hi_x, hi_y) coordinates; this
script bins them into a 2D histogram and overlays on the macro
placement.

Usage:
    uv run tech/asap7/render_congestion.py \\
        --design compute_array_abut \\
        --report build/orfs/reports/asap7/compute_array_abut/base/congestion-5.rpt \\
        --out build/render/congestion_heatmap.png
"""
import argparse
import re
from pathlib import Path
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parents[2]

BBOX_RE = re.compile(
    r"bbox\s*=\s*\(([\d.]+),\s*([\d.]+)\)\s*-\s*\(([\d.]+),\s*([\d.]+)\)"
)


def parse_congestion(path):
    bboxes = []
    for line in path.read_text().splitlines():
        m = BBOX_RE.search(line)
        if m:
            x0, y0, x1, y1 = (float(g) for g in m.groups())
            bboxes.append(((x0 + x1) / 2, (y0 + y1) / 2))
    return bboxes


def parse_macro_placement(tcl_path):
    pat = re.compile(
        r"place_macro\s+-macro_name\s+\{([^}]+)\}\s+-location\s+\{([\d.]+)\s+([\d.]+)\}"
    )
    macros = []
    for line in tcl_path.read_text().splitlines():
        m = pat.search(line)
        if m:
            macros.append((m.group(1), float(m.group(2)), float(m.group(3))))
    return macros


def classify(name):
    if "u_cmd" in name:
        return "cmd_unit"
    if "u_a" in name and "skew" in name:
        return "skew_lane_a"
    if "u_b" in name and "skew" in name:
        return "skew_lane_b"
    return "mac"


SIZES = {"cmd_unit": (44.9, 44.9), "skew_lane_a": (30, 30), "skew_lane_b": (30, 30), "mac": (34.56, 34.56)}
COLORS = {"cmd_unit": "#d62728", "skew_lane_a": "#1f77b4", "skew_lane_b": "#2ca02c", "mac": "#ffe082"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--die", nargs=4, type=float, default=[0, 0, 1300, 1300])
    ap.add_argument("--bins", type=int, default=100)
    args = ap.parse_args()

    bboxes = parse_congestion(Path(args.report))
    print(f"parsed {len(bboxes)} congestion violations from {args.report}")
    if not bboxes:
        print("no violations to plot")
        return

    mp_path = REPO / f"tech/asap7/orfs/{args.design}.macro_placement.tcl"
    macros = parse_macro_placement(mp_path) if mp_path.exists() else []

    fig, ax = plt.subplots(figsize=(14, 14))

    # Heatmap (2D histogram of bbox centers)
    xs, ys = zip(*bboxes)
    h, xe, ye = np.histogram2d(xs, ys, bins=args.bins,
                                range=[[args.die[0], args.die[2]], [args.die[1], args.die[3]]])
    # Mask cells with no overflow so the background looks clean
    h_masked = np.ma.masked_where(h.T == 0, h.T)
    im = ax.imshow(h_masked, extent=[args.die[0], args.die[2], args.die[1], args.die[3]],
                   origin="lower", cmap="hot_r", alpha=0.7, aspect="equal", interpolation="nearest")
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="overflow violations / bin")

    # Macro outlines (lightly drawn, hide fill so heatmap shows through)
    for name, x, y in macros:
        cat = classify(name)
        w, h_sz = SIZES[cat]
        ax.add_patch(mpatches.Rectangle(
            (x, y), w, h_sz, fill=False,
            edgecolor=COLORS[cat], linewidth=0.3, alpha=0.4
        ))

    # Die outline
    ax.add_patch(mpatches.Rectangle((args.die[0], args.die[1]),
                                     args.die[2] - args.die[0], args.die[3] - args.die[1],
                                     fill=False, edgecolor="black", linewidth=2))

    handles = [
        mpatches.Patch(facecolor="white", edgecolor=COLORS[c], label=c)
        for c in ["cmd_unit", "skew_lane_a", "skew_lane_b", "mac"]
    ]
    ax.legend(handles=handles, loc="upper right", fontsize=9)

    ax.set_xlim(args.die[0] - 30, args.die[2] + 30)
    ax.set_ylim(args.die[1] - 30, args.die[3] + 30)
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")
    ax.set_title(f"{args.design} GRT congestion ({len(bboxes)} violations) — {Path(args.report).name}",
                 fontsize=12, weight="bold")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    print(f"wrote {out_path} ({out_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
