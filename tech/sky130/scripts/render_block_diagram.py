#!/usr/bin/env python3
"""
Render a logical block diagram: each module as a box with its pin
faces (N/S/E/W) labeled with the buses that live there. Arrows show
inter-module connections, color-coded by whether the connection lands
on facing edges (green) or makes a U-turn (red).
"""

import re
import sys
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import yaml


REPO = Path(__file__).resolve().parents[3]

EDGE_OPP = {"N": "S", "S": "N", "E": "W", "W": "E"}


def read_pin_order(mod):
    p = REPO / f"tech/sky130/submodules/{mod}/{mod}.pin_order.cfg"
    if not p.exists():
        return {}
    out = {"N": [], "S": [], "E": [], "W": []}
    current = None
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line: continue
        if line.startswith("#") and line[1:].strip() in ("N", "S", "E", "W"):
            current = line[1:].strip(); continue
        if line.startswith("#") or line.startswith("$"): continue
        line = line.split("//")[0].split("#")[0].strip()
        if not line or current is None: continue
        # Normalize: strip wildcards + brackets for readability
        clean = line.rstrip("*").rstrip(".").rstrip("\\").rstrip("[")
        out[current].append(clean)
    return out


# Bus connectivity intent: who-talks-to-who, width, label.
# (mod_a, bus_a_pattern, mod_b, bus_b_pattern, width, label)
PIPES = [
    ("compute_array", "drain_row_data", "store",  "drain_row_data", 1024, "drain"),
    ("compute_array", "rd_a_data",      "smem",   "rd_a_data",      256, "operandA"),
    ("compute_array", "rd_b_data",      "smem",   "rd_b_data",      256, "operandB"),
    ("load",          "smem_wr_data",   "smem",   "wr_data",        128, "smem_wr"),
    ("load",          "add_tx_bytes",   "barrier","add_tx_bytes",    32, "add_tx"),
    ("cmdproc",       "load_gmem_ptr",  "load",   "gmem_ptr",        32, "cmdproc→load"),
    ("cmdproc",       "mma_a_smem_offset","compute_array","issue_a_off",32,"cmdproc→mma"),
    ("cmdproc",       "store_gmem_ptr", "store",  "gmem_ptr",        32, "cmdproc→store"),
    ("cmdproc",       "init_bar_id",    "barrier","init_bar_id",     32, "cmdproc→barrier"),
    ("compute_array", "arrive_bar_id",  "barrier","arrive_bar_id_b", 32, "mma_arrive"),
]


def edge_of(pin_order, base):
    for face, pins in pin_order.items():
        for p in pins:
            if base.startswith(p) or p.startswith(base):
                return face
    return None


# Module box positions in the *diagram* (not floorplan).
DIAG_POS = {
    # name: (x, y) — center
    "cmdproc":       (1, 4),
    "barrier":       (3, 4),
    "reset_seq":     (5, 4),
    "load":          (1, 2.5),
    "smem":          (1, 1),
    "compute_array": (3, 1.5),
    "store":         (3, -0.5),
}
DIAG_SIZE = {
    "cmdproc": (0.8, 0.8), "barrier": (0.8, 0.8), "reset_seq": (0.8, 0.8),
    "load": (0.8, 0.6), "smem": (0.8, 1.2),
    "compute_array": (2.4, 2.4), "store": (0.8, 0.8),
}


def main(argv):
    out = Path(argv[1] if len(argv) > 1 else REPO / "build/render/block_diagram.png")
    out.parent.mkdir(parents=True, exist_ok=True)

    pin_orders = {m: read_pin_order(m) for m in DIAG_POS}

    fig, ax = plt.subplots(figsize=(20, 14))
    ax.set_xlim(-0.5, 6.5)
    ax.set_ylim(-1.5, 6)
    ax.set_aspect("equal")
    ax.axis("off")

    mod_colors = {
        "cmdproc": "#ffcccc", "barrier": "#ffe5b3", "reset_seq": "#fff5b3",
        "load": "#ccffcc", "smem": "#cce0ff",
        "compute_array": "#e5ccff", "store": "#ffcce5",
    }

    # Draw each module box with faces labeled.
    mod_face_xy = {}  # (mod, face) → (x, y) center of face for arrow attach
    for mod, (cx, cy) in DIAG_POS.items():
        w, h = DIAG_SIZE[mod]
        x0, y0 = cx - w/2, cy - h/2
        ax.add_patch(mpatches.Rectangle((x0, y0), w, h,
                                        facecolor=mod_colors[mod],
                                        edgecolor="black", linewidth=2))
        ax.text(cx, cy, mod, ha="center", va="center",
                fontsize=11, weight="bold")
        # Per-face labels: collect bus base names.
        face_centers = {
            "N": (cx, y0 + h),
            "S": (cx, y0),
            "E": (cx + w/2, cy),
            "W": (cx - w/2, cy),
        }
        face_text_offset = {
            "N": (0, 0.05, "bottom"),
            "S": (0, -0.05, "top"),
            "E": (0.05, 0, "center"),
            "W": (-0.05, 0, "center"),
        }
        for face in ("N", "S", "E", "W"):
            mod_face_xy[(mod, face)] = face_centers[face]
            pins = pin_orders[mod].get(face, [])
            # Skip clk/reset listing for compactness
            content = [p for p in pins if p not in ("clk", "reset")]
            if not content:
                continue
            txt = "\n".join(content[:8])
            if len(content) > 8:
                txt += f"\n…({len(content)-8} more)"
            dx, dy, va = face_text_offset[face]
            ha = {"N": "center", "S": "center", "E": "left", "W": "right"}[face]
            ax.text(face_centers[face][0] + dx, face_centers[face][1] + dy, txt,
                    ha=ha, va=va, fontsize=6, color="#333",
                    family="monospace")

    # Draw connection arrows.
    legend_drawn = {"facing": False, "uturn": False}
    for (a, pa, b, pb, w, label) in PIPES:
        edge_a = edge_of(pin_orders[a], pa)
        edge_b = edge_of(pin_orders[b], pb)
        if edge_a is None or edge_b is None:
            color, status = "gray", "?"
        elif EDGE_OPP.get(edge_a) == edge_b:
            color, status = "green", "facing"
        else:
            color, status = "red", "uturn"
        xy_a = mod_face_xy[(a, edge_a)] if edge_a else DIAG_POS[a]
        xy_b = mod_face_xy[(b, edge_b)] if edge_b else DIAG_POS[b]
        lw = 1 + min(w/100, 4)
        ax.annotate("", xy=xy_b, xytext=xy_a,
                    arrowprops=dict(arrowstyle="<|-|>", color=color,
                                     lw=lw, alpha=0.6))
        # Mid-label
        mx, my = (xy_a[0]+xy_b[0])/2, (xy_a[1]+xy_b[1])/2
        ax.text(mx, my, f"{label}\n{w}b",
                ha="center", va="center", fontsize=7,
                bbox=dict(facecolor="white", edgecolor=color,
                          boxstyle="round,pad=0.15", linewidth=1))
        if status == "facing": legend_drawn["facing"] = True
        elif status == "uturn": legend_drawn["uturn"] = True

    # Legend
    ax.text(-0.4, 5.7,
            "🟢 facing edge (clean) | 🔴 non-facing edge (route must wrap)",
            fontsize=10, va="top")
    ax.set_title("chip_top logical block diagram — buses + pin face assignments\n"
                 "(green = pins on facing edges; red = pins on edges that don't face each other)",
                 fontsize=13)

    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv)
