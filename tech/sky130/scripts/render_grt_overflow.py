#!/usr/bin/env python3
"""
Render a BINARY overflow map: each GCell is either OK (usage <= capacity)
or FAIL (usage > capacity). One panel per layer. Failed cells drawn solid
red; OK cells transparent so the floorplan shows through.

Usage:
    render_grt_overflow.py <after_grt.guide> [out.png]
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import yaml


REPO = Path(__file__).resolve().parents[3]
GCELL_NM = 6900

TRACK_PITCH = {"met1": 0.34, "met2": 0.46, "met3": 0.68, "met4": 0.92, "met5": 3.40}


def parse_guide(path):
    counts = defaultdict(lambda: defaultdict(int))
    in_net = False
    for line in open(path):
        line = line.strip()
        if line == "(":
            in_net = True; continue
        if line == ")":
            in_net = False; continue
        if not in_net:
            continue
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            xl, yl, xh, yh = (int(p) for p in parts[:4])
        except ValueError:
            continue
        layer = parts[4]
        if layer not in TRACK_PITCH:
            continue
        gx0 = xl // GCELL_NM
        gx1 = max(xh // GCELL_NM, gx0)
        gy0 = yl // GCELL_NM
        gy1 = max(yh // GCELL_NM, gy0)
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                counts[layer][(gx, gy)] += 1
    return counts


def gcell_capacity(layer):
    return (GCELL_NM / 1000) / TRACK_PITCH[layer]


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


MOD_COLORS = {
    "cmdproc": "#ffb3b3", "barrier": "#ffd9b3", "reset_seq": "#fff2b3",
    "load": "#c2f0c2", "smem": "#b3d9ff",
    "compute_array": "#d9b3ff", "store": "#ffb3e6",
}


def main(argv):
    guide_path = Path(argv[1])
    out_path = Path(argv[2]) if len(argv) > 2 else REPO / "build/render/overflow.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    die_w, die_h, modules = load_floorplan()
    counts = parse_guide(guide_path)

    # Aggregate: any-layer fail.
    agg = set()
    fail_counts = {}
    for layer in TRACK_PITCH:
        cap = gcell_capacity(layer)
        d = counts.get(layer, {})
        fails = [(gx, gy) for (gx, gy), n in d.items() if n > cap]
        fail_counts[layer] = len(fails)
        agg.update(fails)
    fail_counts["aggregate"] = len(agg)

    fig, axes = plt.subplots(2, 3, figsize=(24, 18))
    axes = axes.flatten()

    panels = [("aggregate", agg)]
    for layer in ("met1", "met2", "met3", "met4", "met5"):
        d = counts.get(layer, {})
        cap = gcell_capacity(layer)
        fails = {(gx, gy) for (gx, gy), n in d.items() if n > cap}
        panels.append((layer, fails))

    for ax, (name, fails) in zip(axes, panels):
        # Draw modules first.
        for mod, x, y, w, h in modules:
            ax.add_patch(mpatches.Rectangle((x, y), w, h,
                                            facecolor=MOD_COLORS.get(mod, "#dddddd"),
                                            edgecolor="black", linewidth=1, alpha=0.4))
            ax.text(x + w/2, y + h/2, mod,
                    ha="center", va="center", fontsize=8, weight="bold", alpha=0.6)
        # Draw failed GCells solid red.
        for gx, gy in fails:
            x_um = gx * GCELL_NM / 1000
            y_um = gy * GCELL_NM / 1000
            ax.add_patch(mpatches.Rectangle(
                (x_um, y_um), GCELL_NM/1000, GCELL_NM/1000,
                facecolor="red", edgecolor="none", alpha=0.85))
        cap_str = "" if name == "aggregate" else f" cap={gcell_capacity(name):.1f}"
        ax.set_title(f"{name}{cap_str}: {len(fails)} failed GCells")
        ax.set_xlim(-500, die_w + 500)
        ax.set_ylim(-500, die_h + 500)
        ax.set_aspect("equal")
        ax.add_patch(mpatches.Rectangle((0, 0), die_w, die_h,
                                        fill=False, edgecolor="black", linewidth=2))
        ax.set_xlabel("x (µm)")
        ax.set_ylabel("y (µm)")
        ax.grid(True, linestyle=":", alpha=0.3)

    fig.suptitle(f"binary GR overflow map — solid red = GCell where usage > capacity\n"
                 f"total fails: aggregate {fail_counts['aggregate']}, "
                 f"per-layer met1={fail_counts['met1']}, met2={fail_counts['met2']}, "
                 f"met3={fail_counts['met3']}, met4={fail_counts['met4']}, met5={fail_counts['met5']}",
                 fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    print(f"wrote {out_path}")
    return fail_counts
    print(f"\nfail counts:")
    for k, v in fail_counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv)
