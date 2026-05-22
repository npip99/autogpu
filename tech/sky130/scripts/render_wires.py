#!/usr/bin/env python3
"""
Highlight selected nets by drawing all their guide rectangles in one
color (regardless of layer/via), overlaid on the floorplan.

Usage:
    render_wires.py <after_grt.guide> [out.png] [pattern1 pattern2 ...]

If no patterns given, uses a default sample set across the major buses.

Each net's segments share one color; nets get distinct colors so you can
see how each wire is routed. Note: this shows the first-hop segments
from each pin; buffer-chain continuation segments live in unnamed
`_NNN_` nets and are not traced here.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import yaml


REPO = Path(__file__).resolve().parents[3]

# Sample nets to highlight: cover the major inter-macro buses.
DEFAULT_PATTERNS = [
    # 4 drain bits across the bus width
    "ca_drain_row_data[0]",
    "ca_drain_row_data[256]",
    "ca_drain_row_data[512]",
    "ca_drain_row_data[1023]",
    # operand A bits (compute_array.W ↔ smem.E)
    "s_rd_a_data[0]",
    "s_rd_a_data[128]",
    "s_rd_a_data[255]",
    # cmdproc control signals to store
    "cp_store_g[0]",
    "cp_store_g[16]",
]


def parse_guide(path, wanted_set):
    """Return {net_name: [(xl, yl, xh, yh, layer), ...]} for wanted nets."""
    out = defaultdict(list)
    current = None
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
            current = None
            continue
        if not in_net:
            # net name line — could be escaped form like "ca_drain_row_data\[0\]"
            normalized = line.replace("\\[", "[").replace("\\]", "]")
            current = normalized if normalized in wanted_set else None
            continue
        if current is None:
            continue
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            xl, yl, xh, yh = (int(p) for p in parts[:4])
        except ValueError:
            continue
        out[current].append((xl, yl, xh, yh, parts[4]))
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
        modules.append((mod, x, y, w, h))
    return die_w, die_h, modules


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    guide_path = Path(argv[1])
    out_path = Path(argv[2]) if len(argv) > 2 else REPO / "build/render/wires_simple.png"
    patterns = argv[3:] if len(argv) > 3 else DEFAULT_PATTERNS
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wanted = set(patterns)
    nets = parse_guide(guide_path, wanted)
    print(f"{len(nets)} nets matched (of {len(wanted)} requested)")
    for n, segs in nets.items():
        print(f"  {n}: {len(segs)} segments")

    die_w_um, die_h_um, modules = load_floorplan()

    fig, ax = plt.subplots(figsize=(16, 16))
    ax.set_xlim(-500, die_w_um + 500)
    ax.set_ylim(-500, die_h_um + 500)
    ax.set_aspect("equal")
    ax.add_patch(mpatches.Rectangle((0, 0), die_w_um, die_h_um,
                                    fill=False, edgecolor="black", linewidth=2))

    # Module rectangles + labels.
    mod_colors = {
        "cmdproc":       "#ffb3b3",
        "barrier":       "#ffd9b3",
        "reset_seq":     "#fff2b3",
        "load":          "#c2f0c2",
        "smem":          "#b3d9ff",
        "compute_array": "#d9b3ff",
        "store":         "#ffb3e6",
    }
    for mod, x, y, w, h in modules:
        ax.add_patch(mpatches.Rectangle((x, y), w, h,
                                        facecolor=mod_colors.get(mod, "#dddddd"),
                                        edgecolor="black", linewidth=1, alpha=0.35))
        ax.text(x + w/2, y + h/2, mod,
                ha="center", va="center", fontsize=10, weight="bold",
                color="black", alpha=0.6)

    # Per-net color.
    cmap = plt.get_cmap("tab10")
    for i, net in enumerate(sorted(nets)):
        color = cmap(i % 10)
        for xl_nm, yl_nm, xh_nm, yh_nm, layer in nets[net]:
            xl, yl, xh, yh = xl_nm/1000, yl_nm/1000, xh_nm/1000, yh_nm/1000
            ax.add_patch(mpatches.Rectangle(
                (xl, yl), max(xh-xl, 2), max(yh-yl, 2),
                facecolor=color, edgecolor=color, linewidth=0.8, alpha=0.85))
        ax.plot([], [], color=color, label=net, linewidth=4)

    ax.legend(loc="upper right", fontsize=8)
    ax.set_title("selected nets — guide rectangles per net (color = same net)")
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")
    ax.grid(True, linestyle=":", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
