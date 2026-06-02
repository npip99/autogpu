#!/usr/bin/env python3
"""Render a planned ORFS floorplan from macro_placement.tcl + pins.tcl + die area.

Standalone matplotlib renderer — does NOT need OpenROAD, KLayout, or the build
to have completed. Reads:
  - macro_placement.tcl  (place_macro commands → macro coords)
  - pins.tcl             (set_io_pin_constraint → which signals go to which edge)
  - die area + LEF SIZE  (passed as args / read from LEFs)

Produces a labeled PNG showing macro outlines + IO pin regions, so you can
sanity-check the floorplan + pin placement BEFORE the build finishes.

Usage:
    uv run tech/asap7/render_planned_floorplan.py \\
        --design compute_array_abut \\
        --out build/render/compute_array_abut_planned.png
"""
import argparse
import re
from pathlib import Path
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[2]


def parse_macro_placement(tcl_path):
    """Parse `place_macro -macro_name {N} -location {x y} -orientation O`."""
    pat = re.compile(
        r"place_macro\s+-macro_name\s+\{([^}]+)\}\s+-location\s+\{([\d.]+)\s+([\d.]+)\}"
    )
    macros = []
    for line in tcl_path.read_text().splitlines():
        m = pat.search(line)
        if m:
            macros.append((m.group(1), float(m.group(2)), float(m.group(3))))
    return macros


def parse_pin_regions(tcl_path):
    """Parse `set_io_pin_constraint -region <edge>:* -pin_names {...}`.

    Returns dict of {edge: [pin_patterns]} where edge ∈ {left,right,top,bottom}.
    """
    text = tcl_path.read_text()
    # Match each set_io_pin_constraint block, capturing region edge + brace body.
    pat = re.compile(
        r"set_io_pin_constraint\s+-region\s+(\w+):\*\s+-pin_names\s+\{([^}]+)\}",
        re.DOTALL,
    )
    out = {}
    for m in pat.finditer(text):
        edge = m.group(1)
        pins = m.group(2).split()
        out[edge] = pins
    return out


def parse_lef_size(lef_path):
    """Return (w, h) µm of the first SIZE record in the LEF."""
    pat = re.compile(r"^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", re.MULTILINE)
    m = pat.search(lef_path.read_text())
    if not m:
        raise SystemExit(f"no SIZE in {lef_path}")
    return float(m.group(1)), float(m.group(2))


def classify(macro_name):
    """Map macro instance name → category for color/size lookup."""
    if "u_cmd" in macro_name:
        return "cmd_unit"
    if "u_a" in macro_name and "skew" in macro_name:
        return "skew_lane_a"
    if "u_b" in macro_name and "skew" in macro_name:
        return "skew_lane_b"
    return "mac_tmem_cell_tile"


COLORS = {
    "cmd_unit":            "#d62728",   # red
    "skew_lane_a":         "#1f77b4",   # blue
    "skew_lane_b":         "#2ca02c",   # green
    "mac_tmem_cell_tile":  "#ffbf00",   # gold
}

EDGE_COLORS = {
    "left":    "#9467bd",   # purple
    "right":   "#8c564b",   # brown
    "top":     "#7f7f7f",   # grey
    "bottom":  "#e377c2",   # pink
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--design", required=True, help="e.g. compute_array_abut")
    ap.add_argument(
        "--macro-placement",
        help="path to macro_placement.tcl (default: tech/asap7/orfs/<design>.macro_placement.tcl)",
    )
    ap.add_argument(
        "--pins",
        help="path to pins.tcl (default: tech/asap7/orfs/scripts/<design>.pins.tcl)",
    )
    ap.add_argument(
        "--die",
        nargs=4,
        type=float,
        default=None,
        metavar=("X0", "Y0", "X1", "Y1"),
        help="die area in µm (default: read from <design>.config.mk DIE_AREA)",
    )
    ap.add_argument(
        "--lef-root",
        default=str(REPO / "build/orfs/results/asap7"),
        help="root containing <macro>/base/<macro>.lef",
    )
    ap.add_argument("--out", required=True, help="output PNG path")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()

    mp_path = Path(
        args.macro_placement
        or REPO / f"tech/asap7/orfs/{args.design}.macro_placement.tcl"
    )
    pins_path = Path(
        args.pins or REPO / f"tech/asap7/orfs/scripts/{args.design}.pins.tcl"
    )

    if args.die is None:
        config = (REPO / f"tech/asap7/orfs/{args.design}.config.mk").read_text()
        m = re.search(r"DIE_AREA\s*=\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", config)
        if not m:
            raise SystemExit("DIE_AREA not found in config; pass --die explicitly")
        die = tuple(float(x) for x in m.groups())
    else:
        die = tuple(args.die)

    sizes = {}
    for cat in COLORS:
        sizes[cat] = parse_lef_size(Path(args.lef_root) / cat / "base" / f"{cat}.lef")

    macros = parse_macro_placement(mp_path)
    pin_regions = parse_pin_regions(pins_path) if pins_path.exists() else {}

    fig, ax = plt.subplots(figsize=(14, 14))

    # Die outline
    ax.add_patch(mpatches.Rectangle(
        (die[0], die[1]), die[2] - die[0], die[3] - die[1],
        fill=False, edgecolor="black", linewidth=2,
    ))

    # IO pin regions — colored strip just inside the die edge
    strip_w = (die[2] - die[0]) * 0.012
    pin_strips = {
        "left":   (die[0], die[1], strip_w, die[3] - die[1]),
        "right":  (die[2] - strip_w, die[1], strip_w, die[3] - die[1]),
        "bottom": (die[0], die[1], die[2] - die[0], strip_w),
        "top":    (die[0], die[3] - strip_w, die[2] - die[0], strip_w),
    }
    for edge, pins in pin_regions.items():
        x, y, w, h = pin_strips[edge]
        ax.add_patch(mpatches.Rectangle(
            (x, y), w, h,
            facecolor=EDGE_COLORS[edge], alpha=0.6,
            edgecolor=EDGE_COLORS[edge], linewidth=0,
        ))

    # Macros
    counts = {c: 0 for c in COLORS}
    for name, x, y in macros:
        cat = classify(name)
        w, h = sizes[cat]
        ax.add_patch(mpatches.Rectangle(
            (x, y), w, h,
            facecolor=COLORS[cat], edgecolor="black",
            linewidth=0.15 if cat == "mac_tmem_cell_tile" else 0.6,
            alpha=0.85,
        ))
        counts[cat] += 1

    # Legend — macros + pin regions
    handles = [
        mpatches.Patch(
            facecolor=c, edgecolor="black",
            label=f"{cat} ({counts[cat]}) — {sizes[cat][0]:.1f}×{sizes[cat][1]:.1f} µm",
        )
        for cat, c in COLORS.items() if counts[cat] > 0
    ]
    for edge, pins in pin_regions.items():
        n = sum(1 for p in pins if not p.startswith("#"))
        handles.append(mpatches.Patch(
            facecolor=EDGE_COLORS[edge], alpha=0.6,
            label=f"{edge} pins: {n} patterns",
        ))
    ax.legend(handles=handles, loc="upper right", fontsize=9, framealpha=0.95)

    ax.set_xlim(die[0] - 30, die[2] + 30)
    ax.set_ylim(die[1] - 30, die[3] + 30)
    ax.set_aspect("equal")
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")
    ax.set_title(
        f"{args.design} — planned floorplan ({die[2] - die[0]:.0f}×{die[3] - die[1]:.0f} µm die, "
        f"{len(macros)} macros)"
    )
    ax.grid(True, linestyle=":", alpha=0.3)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=args.dpi, bbox_inches="tight")
    print(f"wrote {out_path} ({out_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
