#!/usr/bin/env python3
"""
Draw every guide rectangle in the routed design, colored by metal layer.

Usage:
    render_wires_all.py <after_grt.guide> [out.png]
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import yaml


REPO = Path(__file__).resolve().parents[3]

LAYER_COLORS = {
    "li1":  "#888888",
    "met1": "#1f77b4",
    "met2": "#ff7f0e",
    "met3": "#2ca02c",
    "met4": "#d62728",
    "met5": "#9467bd",
}


def parse_guide(path):
    """Return {layer: [(xl, yl, xh, yh), ...]} across all nets."""
    out = defaultdict(list)
    in_net = False
    for raw in open(path):
        line = raw.strip()
        if not line:
            continue
        if line == "(":
            in_net = True
            continue
        if line == ")":
            in_net = False
            continue
        if not in_net:
            continue
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            xl, yl, xh, yh = (int(p) for p in parts[:4])
        except ValueError:
            continue
        out[parts[4]].append((xl, yl, xh, yh))
    return out


def load_floorplan():
    fp = yaml.safe_load((REPO / "tech/sky130/chip_top_floorplan.yaml").read_text())
    die_w, die_h = fp["chip"]["die"]
    modules = []
    for mod, spec in fp["modules"].items():
        x, y = spec["location"]
        cfg = yaml.safe_load((REPO / f"tech/sky130/submodules/{mod}/config.yaml").read_text())
        die = cfg.get("DIE_AREA")
        w, h = (die[2] - die[0], die[3] - die[1]) if die else (0, 0)
        if not w:
            lef_stub = REPO / f"build/sv2v/lef-stub/{mod}.lef"
            if lef_stub.exists():
                for line in lef_stub.read_text().splitlines():
                    m = re.match(r"\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", line)
                    if m:
                        w, h = float(m.group(1)), float(m.group(2))
                        break
        modules.append((mod, x, y, w, h))
    return die_w, die_h, modules


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    guide_path = Path(argv[1])
    out_path = Path(argv[2]) if len(argv) > 2 else REPO / "build/render/wires_all.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("parsing guide…")
    layer_segs = parse_guide(guide_path)
    total = sum(len(v) for v in layer_segs.values())
    print(f"{total} segments across {len(layer_segs)} layers")
    for layer in sorted(layer_segs):
        print(f"  {layer}: {len(layer_segs[layer])} segments")

    die_w_um, die_h_um, modules = load_floorplan()

    fig, ax = plt.subplots(figsize=(20, 20))
    ax.set_xlim(-500, die_w_um + 500)
    ax.set_ylim(-500, die_h_um + 500)
    ax.set_aspect("equal")
    ax.add_patch(mpatches.Rectangle((0, 0), die_w_um, die_h_um,
                                    fill=False, edgecolor="black", linewidth=2))

    # Module rectangles + labels (under the wires).
    for mod, x, y, w, h in modules:
        ax.add_patch(mpatches.Rectangle((x, y), w, h,
                                        facecolor="#eeeeee",
                                        edgecolor="black", linewidth=1, alpha=0.4))
        ax.text(x + w/2, y + h/2, mod,
                ha="center", va="center", fontsize=14, weight="bold",
                color="black", alpha=0.6, zorder=10)

    # Draw layer-by-layer, low layers first.
    for layer in ["li1", "met1", "met2", "met3", "met4", "met5"]:
        if layer not in layer_segs:
            continue
        color = LAYER_COLORS.get(layer, "#000000")
        for xl_nm, yl_nm, xh_nm, yh_nm in layer_segs[layer]:
            xl, yl, xh, yh = xl_nm/1000, yl_nm/1000, xh_nm/1000, yh_nm/1000
            ax.add_patch(mpatches.Rectangle(
                (xl, yl), max(xh-xl, 1), max(yh-yl, 1),
                facecolor=color, edgecolor="none", alpha=0.15))
        ax.plot([], [], color=color, label=f"{layer} ({len(layer_segs[layer])} segs)",
                linewidth=6)

    ax.legend(loc="upper right", fontsize=12)
    ax.set_title("All routed guide segments (alpha-blended; bright = many overlapping nets)")
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
