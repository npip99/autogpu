"""Visualize a proposed chip_top floorplan with pin clusters + bus connections.

Goal: catch geometric layout problems (overlaps, crossings, edge over-packing)
BEFORE running ORFS. Each macro is a colored box. Each bus is allocated a
proportional segment of its edge (proportional to bit count) — drawn as a thick
colored bar with the absolute µm range labeled. Inter-macro bus lines connect
the centroids of each bus's pin segment so you see EXACTLY where it'll land.

Usage:
    uv run --with matplotlib python3 tech/asap7/render_chip_top_buses.py

Output: build/render/chip_top_proposed_buses.png

Iteration history:
  v1: smem (750 µm) overlapped compute_array. Big buses scattered.
  v2: Widened die so smem fits west of compute_array. Better macro placement.
  v3: cmdproc stacked NORTH of load/barrier (single column).
  v4: per-bus pin-segment allocation. Showed each bus's pin range.
  v5 (current): widened channels (compute_array gets 50+ µm channels on
      all sides), die grown to 2400×1800. Used SW wasted space by moving
      cmdproc directly against compute_array's W edge for short mma_*
      routing. Drain fan-in (1024 bits / 1300µm compute_array.S → 338µm
      store.N) is intrinsic and still visible as wider compute_array.S
      red bar vs narrower store.N red bar — would only fully fix by
      re-flooorplanning store at 1300×88 (separate decision).
"""
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import collections
import re
import os

# ---------- Hierarchical sub-macro reader ----------
# For each parent macro that contains hardened sub-macros, read its final DEF
# and extract the sub-macro instance positions. DEF coordinates are in DBUs
# (1000 DBUs / µm in asap7).
SUB_MACRO_PARENTS = {
    "store": "tile_buf_8row",
    "smem":  "smem_bank",
}
DBU_PER_UM = 1000.0
RESULTS = "/home/ubuntu/pipitone/gpu3/build/orfs/results/asap7"


def read_sub_macros(parent, sub_name):
    """Return list of (instance_label, x_um, y_um, w_um, h_um) for each
    sub_name instance placed in parent's final DEF."""
    def_path = f"{RESULTS}/{parent}/base/6_final.def"
    lef_path = f"{RESULTS}/{sub_name}/base/{sub_name}.lef"
    if not (os.path.exists(def_path) and os.path.exists(lef_path)):
        return []
    sub_text = open(lef_path).read()
    m = re.search(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", sub_text)
    if not m: return []
    sub_w, sub_h = float(m.group(1)), float(m.group(2))
    out = []
    for line in open(def_path):
        m = re.match(r"\s*-\s+(\S+)\s+" + sub_name + r"\s+\+\s+FIXED\s+\(\s*(\d+)\s+(\d+)\s*\)", line)
        if m:
            label = m.group(1).replace("\\", "")
            x_um = int(m.group(2)) / DBU_PER_UM
            y_um = int(m.group(3)) / DBU_PER_UM
            out.append((label, x_um, y_um, sub_w, sub_h))
    return out

# --- chip_top floorplan v6 (µm) ---
# v6: shifted NW cluster (cmdproc + load + barrier + reset_seq) ~150 µm
# WEST so they're not crammed against compute_array. Gap to compute_array
# grows from 57 µm to 200 µm — plenty of breathing room for routing channel
# while mma_* (130b) is still a short east-west hop.
M = {
    "compute_array":  (900,  450, 1300, 1300),   # unchanged
    "store":          (1381,  50,  338,  338),   # unchanged
    "smem":           (  50, 700,  750,  300),   # unchanged
    "cmdproc":        ( 550,1500,  143,  143),   # moved 150 µm west; cmdproc.E → compute_array.W gap = 207 µm
    "load":           ( 550,1430,   60,   60),   # below cmdproc
    "barrier":        ( 650,1430,   38,   38),   # east of load
    "reset_seq":      ( 710,1430,    8,    8),
}
DIE = (2400, 1800)

# Per-bus spec: {macro: {bus: (edge, bit_count, peer_macro_or_None)}}
# peer_macro is used to decide WHERE on the edge to place the segment
# (closest to that peer's centroid for minimum routing distance).
BUSES = {
    "compute_array": {
        "drain_row_data":   ("S", 1024, "store"),
        "rd_a_data":        ("W",  256, "smem"),
        "rd_b_data":        ("W",  256, "smem"),
        "rd_a/b_addr":      ("W",   64, "smem"),
        "mma_*":            ("W",  130, "cmdproc"),
        "arrive_en/bar":    ("W",   33, "barrier"),
        "clk/reset":        ("E",    2, None),
    },
    "store": {
        "drain_row_data":   ("N", 1024, "compute_array"),
        "drain_ctrl":       ("N",   10, "compute_array"),
        "mc_wr_*":          ("S",  161, None),
        "store_ctrl":       ("W",   66, "cmdproc"),
    },
    "smem": {
        "rd_a_data":        ("E",  256, "compute_array"),
        "rd_b_data":        ("E",  256, "compute_array"),
        "rd_a/b_addr":      ("E",   64, "compute_array"),
        "wr_*":             ("N",  162, "load"),
        "scrub_*":          ("N",   34, "reset_seq"),
    },
    "cmdproc": {
        "mma_*":            ("E",  130, "compute_array"),
        "load_*":           ("S",  129, "load"),
        "store_*":          ("E",   66, "store"),
        "init/query_*":     ("S",   83, "barrier"),
        "push_instr":       ("N",  256, None),
        "status":           ("W",   50, None),
    },
    "load": {
        "gmem_*":           ("W",  162, None),
        "smem_wr_*":        ("S",  162, "smem"),
        "tx/arrive_*":      ("E",  163, "barrier"),
        "issue_*":          ("N",  129, "cmdproc"),
    },
    "barrier": {
        "init/query_*":     ("N",   83, "cmdproc"),
        "arrive_*":         ("E",   33, "compute_array"),
        "tx_*":             ("W",  163, "load"),
    },
    "reset_seq": {
        "scrub_en":         ("S",    1, "smem"),
        "chip_in_reset":    ("N",    1, None),
    },
}

# Inter-macro bus connection list: (src_macro, src_bus, dst_macro, dst_bus, color)
# (bit_count is read from BUSES spec to keep one source of truth)
CONNS = [
    ("compute_array", "drain_row_data",     "store",        "drain_row_data",   "red"),
    ("compute_array", "rd_a_data",          "smem",         "rd_a_data",        "blue"),
    ("compute_array", "rd_b_data",          "smem",         "rd_b_data",        "blue"),
    ("compute_array", "rd_a/b_addr",        "smem",         "rd_a/b_addr",      "blue"),
    ("compute_array", "mma_*",              "cmdproc",      "mma_*",            "green"),
    ("compute_array", "arrive_en/bar",      "barrier",      "arrive_*",         "purple"),
    ("smem",          "wr_*",               "load",         "smem_wr_*",        "orange"),
    ("cmdproc",       "load_*",             "load",         "issue_*",          "darkgreen"),
    ("cmdproc",       "store_*",            "store",        "store_ctrl",       "brown"),
    ("cmdproc",       "init/query_*",       "barrier",      "init/query_*",     "magenta"),
    ("load",          "tx/arrive_*",        "barrier",      "tx_*",             "cyan"),
]


def macro_center(m):
    x, y, w, h = M[m]
    return (x + w/2, y + h/2)


def edge_span(macro, edge):
    """Return (start, end) of an edge in absolute coords, plus axis ('x' or 'y')."""
    x, y, w, h = M[macro]
    if edge == "N": return (x, x+w), "x", y+h
    if edge == "S": return (x, x+w), "x", y
    if edge == "E": return (y, y+h), "y", x+w
    if edge == "W": return (y, y+h), "y", x


def compute_segments(macro):
    """For each edge, allocate sub-segments per bus proportional to bit count,
    ordered by proximity to peer macro (peer's position projected onto the edge).
    Returns {bus: (start_coord, end_coord, perp_coord)} in absolute µm."""
    out = {}
    by_edge = collections.defaultdict(list)
    for bus, (edge, bits, peer) in BUSES[macro].items():
        by_edge[edge].append((bus, bits, peer))
    for edge, buses in by_edge.items():
        (lo, hi), axis, perp = edge_span(macro, edge)
        # Compute peer projection onto edge axis to order buses
        def peer_proj(b):
            bus, bits, peer = b
            if peer is None:
                # No peer — push toward die edge (chip-IO target)
                return lo  # arbitrary; will end up at the "low" end
            px, py = macro_center(peer)
            return px if axis == "x" else py
        buses_sorted = sorted(buses, key=peer_proj)
        # Allocate proportional to bit count
        total_bits = sum(b[1] for b in buses_sorted)
        edge_len = hi - lo
        # Add a small per-bus padding so adjacent segments don't visually merge
        pad = min(2.0, edge_len * 0.005)
        usable = edge_len - pad * (len(buses_sorted) - 1)
        cursor = lo
        for bus, bits, peer in buses_sorted:
            seg_len = usable * bits / total_bits
            out[bus] = (cursor, cursor + seg_len, perp, edge)
            cursor += seg_len + pad
    return out


def bus_centroid(macro, bus, seg):
    """Return (x, y) of the midpoint of a bus's pin segment."""
    start, end, perp, edge = seg
    mid = (start + end) / 2
    return (mid, perp) if edge in ("N", "S") else (perp, mid)


SEGMENTS = {m: compute_segments(m) for m in M}


def crosses_macro(p1, p2, exclude_set):
    for name, (mx, my, mw, mh) in M.items():
        if name in exclude_set: continue
        x1, y1 = p1; x2, y2 = p2
        for t in [i / 50 for i in range(1, 50)]:
            x = x1 + t*(x2-x1); y = y1 + t*(y2-y1)
            if mx < x < mx+mw and my < y < my+mh:
                return name
    return None


# ---------- Render ----------
fig, ax = plt.subplots(figsize=(18, 14))
ax.set_xlim(-50, DIE[0] + 50)
ax.set_ylim(-50, DIE[1] + 50)
ax.set_aspect("equal")
ax.set_title("chip_top floorplan — per-bus pin-segment allocation\n"
             "Thick colored bars = each bus's pin range. Lines connect segment midpoints.\n"
             "Bar length ∝ bit count. Ordering driven by proximity to peer macro.")

ax.add_patch(patches.Rectangle((0, 0), DIE[0], DIE[1], linewidth=2,
                                edgecolor="black", facecolor="#fafafa"))

COLORS = {
    "compute_array": "#c7ecee",
    "store":         "#ffe082",
    "smem":          "#7fcdcd",
    "cmdproc":       "#ffd29b",
    "load":          "#ffaaa5",
    "barrier":       "#a8d8ea",
    "reset_seq":     "#aa96da",
}
for name, (x, y, w, h) in M.items():
    ax.add_patch(patches.Rectangle((x, y), w, h, linewidth=1.2,
                                    edgecolor="black", facecolor=COLORS[name], alpha=0.4))
    # Draw nested sub-macros for hierarchical parents
    if name in SUB_MACRO_PARENTS:
        subs = read_sub_macros(name, SUB_MACRO_PARENTS[name])
        for label, sx, sy, sw, sh in subs:
            ax.add_patch(patches.Rectangle((x + sx, y + sy), sw, sh, linewidth=0.6,
                                            edgecolor="#222", facecolor="#666", alpha=0.5))
            # Small label for sub-macro
            ax.text(x + sx + sw/2, y + sy + sh/2,
                    f"{SUB_MACRO_PARENTS[name]}",
                    ha="center", va="center", fontsize=4.5, color="white")
        # Parent label moved to a corner so it doesn't collide with sub-macros
        ax.text(x + 5, y + h - 5, f"{name}\n({len(subs)}×{SUB_MACRO_PARENTS[name]})",
                ha="left", va="top", fontsize=8, weight="bold")
    else:
        ax.text(x + w/2, y + h/2, name, ha="center", va="center", fontsize=10, weight="bold")

# Per-bus segment bars on each edge
BUS_COLORS = {}
def color_for_bus(macro, bus):
    if (macro, bus) in BUS_COLORS: return BUS_COLORS[(macro, bus)]
    # default per-source-color from CONNS
    for src_m, src_b, dst_m, dst_b, c in CONNS:
        if (src_m, src_b) == (macro, bus) or (dst_m, dst_b) == (macro, bus):
            BUS_COLORS[(macro, bus)] = c
            return c
    return "gray"

for macro, segs in SEGMENTS.items():
    for bus, (start, end, perp, edge) in segs.items():
        c = color_for_bus(macro, bus)
        if edge in ("N", "S"):
            ax.plot([start, end], [perp, perp], color=c, linewidth=8, alpha=0.8, solid_capstyle="butt")
        else:
            ax.plot([perp, perp], [start, end], color=c, linewidth=8, alpha=0.8, solid_capstyle="butt")

# Inter-macro bus lines (segment midpoint → segment midpoint), labeled with bit count
for src_m, src_b, dst_m, dst_b, color in CONNS:
    if src_b not in SEGMENTS[src_m]: continue
    if dst_b not in SEGMENTS[dst_m]: continue
    bits = BUSES[src_m][src_b][1]
    p1 = bus_centroid(src_m, src_b, SEGMENTS[src_m][src_b])
    p2 = bus_centroid(dst_m, dst_b, SEGMENTS[dst_m][dst_b])
    crossed = crosses_macro(p1, p2, {src_m, dst_m})
    lw = max(0.5, bits / 250)
    line_color = "red" if crossed else color
    line_style = "--" if crossed else "-"
    ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=line_color, linewidth=lw,
             linestyle=line_style, alpha=0.6)
    mid = ((p1[0]+p2[0])/2, (p1[1]+p2[1])/2)
    label = f"{src_b} {bits}b"
    if crossed: label += f"\nCROSSES {crossed}"
    ax.text(mid[0], mid[1], label, ha="center", va="center", fontsize=6,
             bbox=dict(facecolor="white", alpha=0.85,
                       edgecolor="red" if crossed else "none", pad=1))

# Chip-IO connections: any bus with peer=None routes to the die edge
# perpendicular to its pin segment. Draw a dashed arrow to that die edge
# and label "→ chip pin".
for macro, buses in BUSES.items():
    for bus, (edge, bits, peer) in buses.items():
        if peer is not None: continue
        if bus not in SEGMENTS[macro]: continue
        start, end, perp, e = SEGMENTS[macro][bus]
        if e in ("N", "S"):
            mid_x = (start + end) / 2
            die_y = DIE[1] if e == "N" else 0
            p1 = (mid_x, perp)
            p2 = (mid_x, die_y)
        else:
            mid_y = (start + end) / 2
            die_x = DIE[0] if e == "E" else 0
            p1 = (perp, mid_y)
            p2 = (die_x, mid_y)
        ax.annotate("", xy=p2, xytext=p1,
                    arrowprops=dict(arrowstyle="->", color="black",
                                    linewidth=max(0.4, bits/300), alpha=0.5))
        midp = ((p1[0]+p2[0])/2, (p1[1]+p2[1])/2)
        ax.text(midp[0], midp[1], f"{bus} {bits}b → chip pin",
                 ha="center", va="center", fontsize=5.5, color="black",
                 bbox=dict(facecolor="lightyellow", alpha=0.8, edgecolor="none", pad=0.5))

# Per-bus segment annotations: small text near the bar showing absolute range
for macro, segs in SEGMENTS.items():
    for bus, (start, end, perp, edge) in segs.items():
        # Place label outside the macro
        mid = (start + end) / 2
        offset = 8
        if edge == "N":
            tx, ty, ha, va = mid, perp + offset, "center", "bottom"
        elif edge == "S":
            tx, ty, ha, va = mid, perp - offset, "center", "top"
        elif edge == "E":
            tx, ty, ha, va = perp + offset, mid, "left", "center"
        else:  # W
            tx, ty, ha, va = perp - offset, mid, "right", "center"
        # Only show range label for buses with >=30 bits, to avoid clutter
        if (BUSES[macro][bus][1] >= 30):
            ax.text(tx, ty, f"{bus}\n[{start:.0f}-{end:.0f}]",
                     ha=ha, va=va, fontsize=5.5, color=color_for_bus(macro, bus),
                     bbox=dict(facecolor="white", alpha=0.6, edgecolor="none", pad=0.5))

ax.set_xlabel("x (µm)")
ax.set_ylabel("y (µm)")
out = "/home/ubuntu/pipitone/gpu3/build/render/chip_top_proposed_buses.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"Wrote {out}")
