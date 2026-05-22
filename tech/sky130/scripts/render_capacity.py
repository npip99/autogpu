#!/usr/bin/env python3
"""
Estimate per-channel routing capacity vs wire demand and render it as a
PNG over the floorplan.

For each inter-module pipe in PIPES, compute:
    available_tracks = effective_tracks_per_um * channel_width_um
    demand           = sum of bus widths in the pipe
    utilization      = demand / available_tracks

Color channels green (<50% util), yellow (50-90%), red (>90%).

Output: build/render/chip_top_capacity.png
"""

import re
import sys
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import yaml


REPO = Path(__file__).resolve().parents[3]


# sky130 routing layer pitches (um).
# Horizontal layers: met1, met3, met5. Vertical layers: met2, met4.
H_LAYERS = [("met1", 0.34), ("met3", 0.68), ("met5", 3.40)]
V_LAYERS = [("met2", 0.46), ("met4", 0.92)]

# Effective routing fraction after PDN, vias, macros, congestion overhead.
# 50% is a conservative rule of thumb; sky130 PDN + blockages often
# leave less, especially on met4/met5.
EFFECTIVE = 0.50


# Inter-module pipes: each entry lists every bus that crosses the channel
# between mod_a and mod_b. Source of truth is chip_top.sv connectivity +
# bus widths from the SV port lists.
PIPES = [
    # (mod_a, mod_b, [bus widths...], label)
    ("compute_array", "store",         [1024, 5, 2, 1, 1, 1, 1, 1], "drain bus"),
    ("compute_array", "smem",          [256, 256, 32, 32, 1, 1, 1, 1, 1, 1], "operand A+B reads"),
    ("load",          "smem",          [128, 32, 1, 1],              "smem write bus"),
    ("cmdproc",       "compute_array", [32, 32, 32, 32, 1, 1, 1, 1, 1, 1], "cmdproc → mma"),
    ("cmdproc",       "barrier",       [32, 16, 32, 1, 1, 1],        "cmdproc → barrier"),
    ("cmdproc",       "load",          [32, 32, 32, 32, 1, 1, 1, 1, 1, 1], "cmdproc → load"),
    ("cmdproc",       "store",         [32, 32, 1, 1, 1, 1, 1, 1],   "cmdproc → store"),
    ("load",          "barrier",       [32, 32, 32, 32, 32, 1, 1, 1], "load → barrier (add/sub/arrive_tx)"),
    ("compute_array", "barrier",       [32, 1],                       "mma arrive"),
]


def module_size(mod: str) -> tuple[float, float]:
    cfg = yaml.safe_load((REPO / f"tech/sky130/submodules/{mod}/config.yaml").read_text())
    die = cfg.get("DIE_AREA")
    if die and len(die) == 4:
        return die[2] - die[0], die[3] - die[1]
    if cfg.get("STUB_SIZE"):
        return tuple(cfg["STUB_SIZE"])
    runs_dir = REPO / f"tech/sky130/submodules/{mod}/runs"
    if runs_dir.exists():
        for run in sorted(runs_dir.glob("RUN_*"), reverse=True):
            lef = run / "final" / "lef" / f"{mod}.lef"
            if lef.exists():
                for line in lef.read_text().splitlines():
                    m = re.match(r"\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", line)
                    if m:
                        return float(m.group(1)), float(m.group(2))
    return 0.0, 0.0


COLORS_MOD = {
    "cmdproc":       "#ffb3b3",
    "barrier":       "#ffd9b3",
    "reset_seq":     "#fff2b3",
    "load":          "#c2f0c2",
    "smem":          "#b3d9ff",
    "compute_array": "#d9b3ff",
    "store":         "#ffb3e6",
}


def channel_geometry(a, b):
    """Return (gap_um, overlap_um, direction, channel_rect).

    direction: 'N-S' (vertical channel, uses V layers) or 'E-W' (horizontal).
    channel_rect: (x0, y0, x1, y1) of the channel area to color.
    """
    ax0, ay0, ax1, ay1 = a["x0"], a["y0"], a["x1"], a["y1"]
    bx0, by0, bx1, by1 = b["x0"], b["y0"], b["x1"], b["y1"]

    # Which face of A points at B?
    cands = []
    # A north of B: a above b, gap = b's top - a's bottom? no, a above b means ay0 > by1.
    # Let's just enumerate:
    if ay0 >= by1:  # a north of b
        gap = ay0 - by1
        ov = max(0, min(ax1, bx1) - max(ax0, bx0))
        rect = (max(ax0, bx0), by1, min(ax1, bx1), ay0)
        cands.append((gap, ov, "N-S", rect))
    if by0 >= ay1:  # b north of a
        gap = by0 - ay1
        ov = max(0, min(ax1, bx1) - max(ax0, bx0))
        rect = (max(ax0, bx0), ay1, min(ax1, bx1), by0)
        cands.append((gap, ov, "N-S", rect))
    if ax0 >= bx1:  # a east of b
        gap = ax0 - bx1
        ov = max(0, min(ay1, by1) - max(ay0, by0))
        rect = (bx1, max(ay0, by0), ax0, min(ay1, by1))
        cands.append((gap, ov, "E-W", rect))
    if bx0 >= ax1:  # b east of a
        gap = bx0 - ax1
        ov = max(0, min(ay1, by1) - max(ay0, by0))
        rect = (ax1, max(ay0, by0), bx0, min(ay1, by1))
        cands.append((gap, ov, "E-W", rect))

    if not cands:
        return None
    # Take the one with the largest overlap (canonical channel).
    cands.sort(key=lambda c: (-c[1], c[0]))
    return cands[0]


def util_color(util: float) -> str:
    if util < 0.50:
        return "#80c080"  # green
    if util < 0.90:
        return "#e8c060"  # yellow
    return "#d06060"  # red


def main(argv):
    out = Path(argv[1] if len(argv) > 1 else REPO / "build/render/capacity.png")
    out.parent.mkdir(parents=True, exist_ok=True)

    fp = yaml.safe_load((REPO / "tech/sky130/chip_top_floorplan.yaml").read_text())
    die_w, die_h = fp["chip"]["die"]

    placements = {}
    for mod, spec in fp["modules"].items():
        x, y = spec["location"]
        w, h = module_size(mod)
        placements[mod] = dict(x0=x, y0=y, x1=x+w, y1=y+h, w=w, h=h)

    fig, ax = plt.subplots(figsize=(13, 14))
    ax.set_xlim(-500, die_w + 500)
    ax.set_ylim(-500, die_h + 500)
    ax.set_aspect("equal")

    ax.add_patch(mpatches.Rectangle((0, 0), die_w, die_h,
                                    fill=False, edgecolor="black", linewidth=2))

    # Per-direction effective tracks/um.
    h_tracks_per_um = sum(1 / p for _, p in H_LAYERS) * EFFECTIVE
    v_tracks_per_um = sum(1 / p for _, p in V_LAYERS) * EFFECTIVE

    # Draw module rectangles.
    for mod, p in placements.items():
        ax.add_patch(mpatches.Rectangle((p["x0"], p["y0"]), p["w"], p["h"],
                                        facecolor=COLORS_MOD.get(mod, "#dddddd"),
                                        edgecolor="black", linewidth=1, alpha=0.6))
        ax.text(p["x0"] + p["w"] / 2, p["y0"] + p["h"] / 2,
                f"{mod}\n{p['w']:.0f}x{p['h']:.0f}",
                ha="center", va="center", fontsize=8 if p["w"] < 1500 else 10,
                weight="bold")

    # For each pipe, color in the channel rectangle.
    print(f"{'pipe':<35} {'dir':<5} {'gap':>6} {'overlap':>8} {'demand':>7} {'avail':>7} {'util':>6}")
    print("-" * 90)
    for mod_a, mod_b, widths, label in PIPES:
        if mod_a not in placements or mod_b not in placements:
            continue
        geo = channel_geometry(placements[mod_a], placements[mod_b])
        if geo is None:
            print(f"{label:<35} no channel found")
            continue
        gap, overlap, direction, rect = geo
        if direction == "N-S":
            # vertical channel: routing layers used = V layers. The bottleneck
            # is the channel WIDTH (= overlap) for vertical-direction wires.
            # Wires go N-S in a vertical channel — they use V layers stacked
            # across the available X span (overlap).
            tracks_per_um = v_tracks_per_um
            limiting = overlap
        else:
            tracks_per_um = h_tracks_per_um
            limiting = overlap
        demand = sum(widths)
        avail = tracks_per_um * limiting
        util = demand / avail if avail > 0 else float("inf")
        x0, y0, x1, y1 = rect
        # Color the channel.
        ax.add_patch(mpatches.Rectangle((x0, y0), x1 - x0, y1 - y0,
                                        facecolor=util_color(util),
                                        edgecolor="none", alpha=0.45, zorder=1))
        # Label.
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        ax.text(cx, cy, f"{label}\n{demand}w / {avail:.0f}t = {util*100:.0f}%",
                ha="center", va="center", fontsize=7, color="black")
        print(f"{label:<35} {direction:<5} {gap:>6.0f} {overlap:>8.0f} {demand:>7d} "
              f"{avail:>7.0f} {util*100:>5.0f}%")

    # Legend
    legend = [
        mpatches.Patch(facecolor="#80c080", label="< 50% util"),
        mpatches.Patch(facecolor="#e8c060", label="50–90% util"),
        mpatches.Patch(facecolor="#d06060", label="> 90% util"),
    ]
    ax.legend(handles=legend, loc="upper right")

    ax.set_title(f"channel capacity vs demand — die {die_w:.0f}x{die_h:.0f} um\n"
                 f"H tracks/um = {sum(1/p for _,p in H_LAYERS):.2f} raw × {EFFECTIVE} eff = "
                 f"{h_tracks_per_um:.2f}, "
                 f"V tracks/um = {sum(1/p for _,p in V_LAYERS):.2f} raw × {EFFECTIVE} eff = "
                 f"{v_tracks_per_um:.2f}")
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    ax.grid(True, linestyle=":", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out, dpi=300)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main(sys.argv)
