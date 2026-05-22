#!/usr/bin/env python3
"""
Pick N random nets from the GR guide (filtering to inter-module signal
nets, not auto-buffer chain nets) and color each one across all its
segments — so you can see how a few representative wires route.

Usage:
    render_random_wires.py <after_grt.guide> [out.png] [N=20] [seed=1]
"""

import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import yaml


REPO = Path(__file__).resolve().parents[3]


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


def parse_guide_all(path):
    """Return {net_name: [(xl, yl, xh, yh, layer), ...]} for ALL nets."""
    out = defaultdict(list)
    current = None
    in_net = False
    for line in open(path):
        line = line.strip()
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
            current = line.replace("\\[", "[").replace("\\]", "]")
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


# Buses to sample from (chip-level signal-net prefixes).
BUS_PREFIXES = [
    "ca_drain_row_data[",
    "s_rd_a_data[",
    "s_rd_b_data[",
    "l_smem_wr_data[",
    "cp_mma_a[",
    "cp_mma_b[",
    "cp_mma_d[",
    "cp_load_g[",
    "cp_store_g[",
    "l_add_tx_bytes[",
    "m_arrive_bar_id[",
]


def main(argv):
    guide_path = Path(argv[1])
    out_path = Path(argv[2]) if len(argv) > 2 else REPO / "build/render/grt_random_wires.png"
    n_pick = int(argv[3]) if len(argv) > 3 else 20
    seed = int(argv[4]) if len(argv) > 4 else 1
    out_path.parent.mkdir(parents=True, exist_ok=True)

    nets = parse_guide_all(guide_path)
    # Filter to named buses we care about.
    candidates = []
    for name in nets:
        if any(name.startswith(p) for p in BUS_PREFIXES):
            candidates.append(name)
    print(f"{len(candidates)} candidate inter-module bus nets")
    rng = random.Random(seed)
    if len(candidates) > n_pick:
        picks = rng.sample(candidates, n_pick)
    else:
        picks = candidates
    print(f"picked {len(picks)}:")
    for p in picks:
        print(f"  {p}: {len(nets[p])} segments")

    die_w_um, die_h_um, modules = load_floorplan()

    fig, ax = plt.subplots(figsize=(18, 18))
    ax.set_xlim(-500, die_w_um + 500)
    ax.set_ylim(-500, die_h_um + 500)
    ax.set_aspect("equal")
    ax.add_patch(mpatches.Rectangle((0, 0), die_w_um, die_h_um,
                                    fill=False, edgecolor="black", linewidth=2))
    mod_colors = {
        "cmdproc": "#ffb3b3", "barrier": "#ffd9b3", "reset_seq": "#fff2b3",
        "load": "#c2f0c2", "smem": "#b3d9ff",
        "compute_array": "#d9b3ff", "store": "#ffb3e6",
    }
    for mod, x, y, w, h in modules:
        ax.add_patch(mpatches.Rectangle((x, y), w, h,
                                        facecolor=mod_colors.get(mod, "#dddddd"),
                                        edgecolor="black", linewidth=1, alpha=0.30))
        ax.text(x + w/2, y + h/2, mod,
                ha="center", va="center", fontsize=12, weight="bold", alpha=0.7)

    cmap = plt.get_cmap("tab20")
    for i, net in enumerate(sorted(picks)):
        color = cmap(i % 20)
        for xl, yl, xh, yh, layer in nets[net]:
            xl_, yl_, xh_, yh_ = xl/1000, yl/1000, xh/1000, yh/1000
            ax.add_patch(mpatches.Rectangle(
                (xl_, yl_), max(xh_-xl_, 5), max(yh_-yl_, 5),
                facecolor=color, edgecolor=color, linewidth=0.6, alpha=0.85))
        ax.plot([], [], color=color, label=net, linewidth=4)

    ax.legend(loc="upper right", fontsize=8, ncol=2)
    ax.set_title(f"random {len(picks)} inter-module nets — one color per net (seed {seed})")
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")
    ax.grid(True, linestyle=":", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main(sys.argv)
