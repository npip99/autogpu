#!/usr/bin/env python3
"""
Parse OpenROAD's after_grt.guide and render a per-layer congestion
heatmap, overlaid on the chip floorplan so we can see WHICH macros sit
under the hotspots.

Usage:
    render_grt_heatmap.py <after_grt.guide> [out_dir]

Output: build/render/grt_heatmap_<layer>.png  (one per routing layer)
"""

import re
import sys
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import yaml


REPO = Path(__file__).resolve().parents[3]

# GCell grid resolution: derived from the .guide file (rect coords in nm).
GCELL_NM = 6900  # 6.9 µm, confirmed by inspecting consecutive guides

# Sky130 track pitches in µm.
TRACK_PITCH = {"met1": 0.34, "met2": 0.46, "met3": 0.68, "met4": 0.92, "met5": 3.40}
H_LAYERS = {"met1", "met3", "met5"}
V_LAYERS = {"met2", "met4"}


def parse_guide(path):
    """Return {layer: np.ndarray[nx, ny]} of wire counts per GCell."""
    counts = {}
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
            continue
        if not in_net:
            continue
        # Parse: xl yl xh yh layer  (coords in nm)
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
        # Convert nm → GCell indices.
        gx0 = xl // GCELL_NM
        gx1 = max(xh // GCELL_NM, gx0)
        gy0 = yl // GCELL_NM
        gy1 = max(yh // GCELL_NM, gy0)
        if layer not in counts:
            counts[layer] = {}
        d = counts[layer]
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                d[(gx, gy)] = d.get((gx, gy), 0) + 1
    return counts


def to_grid(counts_dict, nx, ny):
    grid = np.zeros((nx, ny), dtype=np.float32)
    for (gx, gy), n in counts_dict.items():
        if 0 <= gx < nx and 0 <= gy < ny:
            grid[gx, gy] = n
    return grid


def gcell_capacity(layer):
    """Approximate per-GCell capacity (raw tracks, no PDN derating)."""
    pitch_um = TRACK_PITCH[layer]
    return (GCELL_NM / 1000) / pitch_um


COLORS_MOD = {
    "cmdproc":       "#ffb3b3",
    "barrier":       "#ffd9b3",
    "reset_seq":     "#fff2b3",
    "load":          "#c2f0c2",
    "smem":          "#b3d9ff",
    "compute_array": "#d9b3ff",
    "store":         "#ffb3e6",
}


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
            for run in sorted((REPO / f"tech/sky130/submodules/{mod}/runs").glob("RUN_*"), reverse=True):
                lef = run / "final" / "lef" / f"{mod}.lef"
                if lef.exists():
                    for line in lef.read_text().splitlines():
                        m = re.match(r"\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", line)
                        if m:
                            w, h = float(m.group(1)), float(m.group(2))
                            break
                    if w:
                        break
        modules.append((mod, x, y, w, h))
    return die_w, die_h, modules


def main(argv):
    guide_path = Path(argv[1])
    out_dir = Path(argv[2]) if len(argv) > 2 else REPO / "build/render"
    out_dir.mkdir(parents=True, exist_ok=True)

    die_w_um, die_h_um, modules = load_floorplan()
    nx = die_w_um * 1000 // GCELL_NM + 1
    ny = die_h_um * 1000 // GCELL_NM + 1
    print(f"die {die_w_um}x{die_h_um} µm → GCell grid {nx}x{ny} ({GCELL_NM/1000} µm cells)")

    print("parsing guide…")
    counts = parse_guide(guide_path)
    for layer, d in counts.items():
        print(f"  {layer}: {len(d)} GCells used, max wires/GCell = {max(d.values()) if d else 0}")

    # Render one heatmap per routing layer + an aggregate.
    layers_to_plot = ["met1", "met2", "met3", "met4", "met5"]
    layers_present = [l for l in layers_to_plot if l in counts]

    fig, axes = plt.subplots(2, 3, figsize=(24, 16))
    axes = axes.flatten()

    # Aggregate: max overflow ratio across all layers per GCell.
    agg = np.zeros((nx, ny), dtype=np.float32)
    for layer in layers_present:
        cap = gcell_capacity(layer)
        usage = to_grid(counts[layer], nx, ny)
        ratio = usage / cap
        np.maximum(agg, ratio, out=agg)

    plot_data = [("aggregate (max util across layers)", agg, 1.0)]
    for layer in layers_present:
        cap = gcell_capacity(layer)
        plot_data.append((f"{layer} (cap {cap:.1f} tracks/GCell)",
                          to_grid(counts[layer], nx, ny), cap))

    for ax, (title, grid, cap) in zip(axes, plot_data):
        ratio = grid / cap if cap > 0 else grid
        ax.imshow(ratio.T, origin="lower",
                  extent=[0, die_w_um, 0, die_h_um],
                  cmap="hot", vmin=0, vmax=1.5, aspect="equal")
        # Overlay modules.
        for mod, x, y, w, h in modules:
            ax.add_patch(mpatches.Rectangle((x, y), w, h,
                                            fill=False, edgecolor=COLORS_MOD.get(mod, "white"),
                                            linewidth=2))
            ax.text(x + w/2, y + h/2, mod,
                    ha="center", va="center",
                    fontsize=8, color="white", weight="bold",
                    bbox=dict(facecolor="black", alpha=0.5, pad=1, edgecolor="none"))
        ax.set_title(title)
        ax.set_xlabel("x (µm)")
        ax.set_ylabel("y (µm)")

    # Hide unused axes.
    for ax in axes[len(plot_data):]:
        ax.axis("off")

    out = out_dir / "grt_heatmap.png"
    fig.suptitle("GRT congestion heatmap (after 5 overflow iters, allow_congestion=true)\n"
                 "ratio = usage / GCell-capacity; bright = congested",
                 fontsize=14)
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv)
