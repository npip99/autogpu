"""Generic per-macro layout visualizer.

For every hardened macro under `build/orfs/results/asap7/<macro>/base/`,
produce a `build/render/macro_layouts/<macro>.png` showing:

- The macro outline (SIZE from its .lef)
- Any sub-macros placed inside (parsed from 6_final.def), with their
  own pin clusters drawn on their edges
- Per-bus pin segments colored + sized by bit count, labeled with name
- Inter-macro bus connection lines (matched by pin-name between
  sub-macros) with line width proportional to bit count
- Chip-IO arrows for buses that exit the parent (no matching peer)

Usage:
    uv run --with matplotlib python3 tech/asap7/render_macro_layouts.py

Outputs into build/render/macro_layouts/. Skips macros without a hardened
LEF.
"""

import collections
import os
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "build/orfs/results/asap7"
OUT_DIR = REPO / "build/render/macro_layouts"
DBU_PER_UM = 1000.0

EDGE_COLOR = {"N": "#0066cc", "S": "#cc0000", "E": "#cc6600", "W": "#009933"}

# Distinct fill colors for sub-macros (cycled per unique module name).
SUB_PALETTE = ["#c7ecee", "#ffe082", "#7fcdcd", "#ffd29b", "#ffaaa5",
               "#a8d8ea", "#aa96da", "#fcb1cb", "#b8e0d2", "#fed9a6"]

# Distinct colors for inter-macro bus connection lines.
BUS_PALETTE = ["red", "blue", "green", "purple", "orange", "darkgreen",
               "brown", "magenta", "cyan", "navy", "olive", "teal"]

# ---------- module-name → LEF path index ----------
# Modules are placed in DEFs by their SV module name (e.g., "compute_array"
# is the module inside compute_array_abut/base/compute_array.lef).
# Build an index once so we can look up any module's geometry.

def build_module_index() -> dict[str, Path]:
    """Walk all `<macro>/base/*.lef`, extract MACRO names, map to file."""
    index: dict[str, Path] = {}
    for macro_dir in RESULTS.iterdir():
        if not macro_dir.is_dir():
            continue
        for lef_path in (macro_dir / "base").glob("*.lef"):
            try:
                for m in re.finditer(r"^MACRO\s+(\S+)", lef_path.read_text(), re.MULTILINE):
                    name = m.group(1)
                    if name not in index:
                        index[name] = lef_path
            except Exception:
                pass
    return index


MODULE_INDEX = build_module_index()


def read_size(lef_path: Path | None) -> tuple[float, float] | None:
    if not lef_path or not lef_path.exists():
        return None
    text = lef_path.read_text()
    m = re.search(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", text)
    return (float(m.group(1)), float(m.group(2))) if m else None


def read_pins(lef_path: Path | None) -> list[tuple[str, float, float]]:
    """Return [(pin_name, x_center, y_center), ...] excluding power pins."""
    if not lef_path or not lef_path.exists():
        return []
    pins = []
    text = lef_path.read_text()
    for name, body in re.findall(r"PIN\s+(\S+)\s+(.*?)END\s+\1", text, re.DOTALL):
        if name in ("VDD", "VSS", "VPP"):
            continue
        if "USE POWER" in body or "USE GROUND" in body:
            continue
        m = re.search(r"RECT\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)", body)
        if not m:
            continue
        x = (float(m.group(1)) + float(m.group(3))) / 2
        y = (float(m.group(2)) + float(m.group(4))) / 2
        pins.append((name, x, y))
    return pins


def edge_of(x: float, y: float, w: float, h: float, tol: float = 2.0) -> str:
    if x < tol: return "W"
    if x > w - tol: return "E"
    if y < tol: return "S"
    if y > h - tol: return "N"
    return "I"


def signal_prefix(pin: str) -> str:
    return re.sub(r"\[\d+\]$", "", pin)


def read_sub_macros(def_path: Path) -> list[tuple[str, str, float, float]]:
    """Return [(instance_label, module_name, x_um, y_um), ...] from a DEF."""
    if not def_path.exists():
        return []
    out = []
    in_components = False
    for line in def_path.read_text().splitlines():
        s = line.strip()
        if s.startswith("COMPONENTS"):
            in_components = True
            continue
        if s.startswith("END COMPONENTS"):
            break
        if not in_components:
            continue
        m = re.match(r"-\s+(\S+)\s+(\S+)\s+\+\s+FIXED\s+\(\s*(\d+)\s+(\d+)\s*\)", s)
        if not m:
            continue
        instance, module = m.group(1), m.group(2)
        # Skip stdcells/fillers/tapcells — they aren't macros we want to draw.
        if re.match(r"^[A-Z]+\d*[a-z]*\w*_ASAP7", module):
            continue
        if module.startswith("FILL") or module.startswith("TAPCELL"):
            continue
        x_um = int(m.group(3)) / DBU_PER_UM
        y_um = int(m.group(4)) / DBU_PER_UM
        out.append((instance.replace("\\", ""), module, x_um, y_um))
    return out


def group_pins_by_bus(pins: list[tuple[str, float, float]], w: float, h: float
                       ) -> dict[str, list[tuple[str, str, float, float]]]:
    """Return {bus_prefix: [(pin_name, edge, x, y), ...]}."""
    groups: dict[str, list[tuple[str, str, float, float]]] = collections.defaultdict(list)
    for name, x, y in pins:
        e = edge_of(x, y, w, h)
        groups[signal_prefix(name)].append((name, e, x, y))
    return groups


def bus_segments_per_edge(bus_groups: dict, w: float, h: float
                          ) -> dict[str, dict[str, tuple[float, float, int]]]:
    """For each (edge, bus) compute (segment_start, segment_end, bits)
    as a proportional allocation along that edge.

    Returns: {bus_name: {edge: (start, end, bits_on_this_edge)}}
    """
    # First pass: count bits per edge per bus
    per_edge_bits: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    for prefix, items in bus_groups.items():
        edge_counts = collections.Counter(e for _, e, _, _ in items)
        for e, c in edge_counts.items():
            if e == "I":
                continue
            per_edge_bits[e].append((prefix, c))
    # Sort each edge's buses by name (deterministic; could sort by bit count)
    for e in per_edge_bits:
        per_edge_bits[e].sort(key=lambda t: -t[1])
    # Allocate proportional segments
    out: dict[str, dict[str, tuple[float, float, int]]] = collections.defaultdict(dict)
    for e, buses in per_edge_bits.items():
        L = w if e in ("N", "S") else h
        total = sum(c for _, c in buses)
        cursor = 0.0
        for prefix, bits in buses:
            seg_len = L * bits / total if total > 0 else 0
            out[prefix][e] = (cursor, cursor + seg_len, bits)
            cursor += seg_len
    return out


def bus_segment_midpoint(seg: tuple[float, float, int], edge: str,
                          origin_x: float, origin_y: float,
                          w: float, h: float) -> tuple[float, float]:
    """Map a bus-segment (start, end) on a sub-macro to absolute coords."""
    start, end, _ = seg
    mid = (start + end) / 2
    if edge == "N": return (origin_x + mid, origin_y + h)
    if edge == "S": return (origin_x + mid, origin_y)
    if edge == "E": return (origin_x + w, origin_y + mid)
    return (origin_x, origin_y + mid)  # W


def crosses_any_macro(p1: tuple[float, float], p2: tuple[float, float],
                       macros: list[tuple[str, float, float, float, float]],
                       exclude: set[str]) -> str | None:
    """Return name of any macro whose bbox the line p1→p2 passes through,
    excluding macros in `exclude` (typically the line's endpoints)."""
    for name, mx, my, mw, mh in macros:
        if name in exclude:
            continue
        for t in [i / 50 for i in range(1, 50)]:
            x = p1[0] + t * (p2[0] - p1[0])
            y = p1[1] + t * (p2[1] - p1[1])
            if mx < x < mx + mw and my < y < my + mh:
                return name
    return None


def render_macro(macro_dir: Path, out_path: Path) -> bool:
    macro_name = macro_dir.name
    candidates = list((macro_dir / "base").glob("*.lef"))
    if not candidates:
        return False
    lef_path = candidates[0]
    size = read_size(lef_path)
    if not size:
        return False
    w, h = size
    pins = read_pins(lef_path)
    bus_groups = group_pins_by_bus(pins, w, h)

    def_path = macro_dir / "base" / "6_final.def"
    sub_macros = read_sub_macros(def_path)

    # Look up each sub-macro's size + pin layout via the module index.
    sub_info: list[dict] = []
    for label, module, sx, sy in sub_macros:
        sub_lef = MODULE_INDEX.get(module)
        sub_size = read_size(sub_lef)
        if not sub_size:
            sub_size = (10.0, 10.0)  # placeholder
        sub_pins = read_pins(sub_lef) if sub_lef else []
        sw, sh = sub_size
        sub_bus_groups = group_pins_by_bus(sub_pins, sw, sh)
        sub_bus_segs = bus_segments_per_edge(sub_bus_groups, sw, sh)
        sub_info.append({
            "label": label, "module": module,
            "x": sx, "y": sy, "w": sw, "h": sh,
            "bus_groups": sub_bus_groups,
            "bus_segs": sub_bus_segs,
        })

    # ---------- Figure ----------
    fig_w = 16.0
    fig_h = max(10.0, min(18.0, fig_w * h / w + 2.0))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    margin = max(w, h) * 0.15
    ax.set_xlim(-margin, w + margin)
    ax.set_ylim(-margin, h + margin)
    ax.set_aspect("equal")
    sub_count_str = f" + {len(sub_macros)} sub-macros" if sub_macros else ""
    ax.set_title(f"{macro_name}: {w:.0f} × {h:.0f} µm, {len(pins)} pins{sub_count_str}",
                 fontsize=11, weight="bold")

    # Macro outline
    ax.add_patch(patches.Rectangle((0, 0), w, h, linewidth=2,
                                    edgecolor="black", facecolor="#fafafa"))

    # ---------- Sub-macros ----------
    module_color: dict[str, str] = {}
    for s in sub_info:
        if s["module"] not in module_color:
            module_color[s["module"]] = SUB_PALETTE[len(module_color) % len(SUB_PALETTE)]

    macro_bboxes = []  # for crossing checks: (name, x, y, w, h)
    for s in sub_info:
        c = module_color[s["module"]]
        ax.add_patch(patches.Rectangle((s["x"], s["y"]), s["w"], s["h"],
                                        linewidth=0.8, edgecolor="#222",
                                        facecolor=c, alpha=0.55))
        # Label: inside if big enough, else with a leader line to the side
        big_enough = s["w"] > w * 0.08 and s["h"] > h * 0.08
        if big_enough:
            ax.text(s["x"] + s["w"] / 2, s["y"] + s["h"] / 2,
                    s["module"], ha="center", va="center",
                    fontsize=max(7, min(12, int(s["w"] / 30))),
                    color="#111", weight="bold")
        else:
            # Tiny macro — label outside (above-right) so it doesn't get
            # crushed by sub-macros around it. Position varies by quadrant
            # to avoid overlap with neighbors.
            tx = s["x"] + s["w"] + max(w, h) * 0.005
            ty = s["y"] + s["h"] / 2
            ax.text(tx, ty, s["module"], ha="left", va="center",
                    fontsize=7, color="#333")
        macro_bboxes.append((s["label"], s["x"], s["y"], s["w"], s["h"]))

    # ---------- Per-bus pin segments on EACH sub-macro's edges ----------
    LW_SEG = 5  # constant points for edge segment lines
    for s in sub_info:
        for prefix, edges in s["bus_segs"].items():
            for edge, (a, b, bits) in edges.items():
                # Translate (a,b) on the sub-macro edge to absolute coords
                if edge == "N":
                    x0 = s["x"] + a; x1 = s["x"] + b; y0 = y1 = s["y"] + s["h"]
                elif edge == "S":
                    x0 = s["x"] + a; x1 = s["x"] + b; y0 = y1 = s["y"]
                elif edge == "E":
                    x0 = x1 = s["x"] + s["w"]; y0 = s["y"] + a; y1 = s["y"] + b
                else:  # W
                    x0 = x1 = s["x"]; y0 = s["y"] + a; y1 = s["y"] + b
                ax.plot([x0, x1], [y0, y1], color=EDGE_COLOR[edge],
                        linewidth=LW_SEG, alpha=0.85, solid_capstyle="butt")

    # ---------- Inter-macro bus connections ----------
    # For every bus name, collect (sub-macro, edge, segment) on each macro.
    # If 2+ macros have it, draw a connection line between consecutive
    # macros' segment midpoints.
    bus_owners: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    # bus_owners[bus_name] = [(sub_macro_idx, edge), ...]
    for idx, s in enumerate(sub_info):
        for prefix, edges in s["bus_segs"].items():
            for edge in edges:
                bus_owners[prefix].append((idx, edge))

    bus_color_idx = 0
    drawn_buses = set()
    def _bus_priority(kv):
        prefix_key, owners_list = kv
        return -sum(sub_info[i]["bus_segs"][prefix_key][e][2]
                     for i, e in owners_list)
    for prefix, owners in sorted(bus_owners.items(), key=_bus_priority):
        if len(owners) < 2:
            continue  # bus only on one macro — it's a chip-IO (handled below)
        # Pick a color from the palette for this bus
        color = BUS_PALETTE[bus_color_idx % len(BUS_PALETTE)]
        bus_color_idx += 1
        # Draw lines pair-by-pair (chain order: 0→1, 1→2, ...)
        for j in range(len(owners) - 1):
            i_a, e_a = owners[j]
            i_b, e_b = owners[j + 1]
            sa, sb = sub_info[i_a], sub_info[i_b]
            seg_a = sa["bus_segs"][prefix][e_a]
            seg_b = sb["bus_segs"][prefix][e_b]
            p1 = bus_segment_midpoint(seg_a, e_a, sa["x"], sa["y"], sa["w"], sa["h"])
            p2 = bus_segment_midpoint(seg_b, e_b, sb["x"], sb["y"], sb["w"], sb["h"])
            bits = seg_a[2]
            crossed = crosses_any_macro(p1, p2, macro_bboxes,
                                         {sa["label"], sb["label"]})
            line_color = "red" if crossed else color
            line_style = "--" if crossed else "-"
            lw = max(0.5, bits / 250)
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=line_color,
                     linewidth=lw, linestyle=line_style, alpha=0.65)
            # Only label substantial buses (>= 8 bits) to reduce clutter
            if prefix not in drawn_buses and bits >= 8:
                mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
                lbl = f"{prefix}×{bits}"
                if crossed:
                    lbl += f"\nCROSSES {crossed}"
                ax.text(mid[0], mid[1], lbl, ha="center", va="center",
                         fontsize=6,
                         bbox=dict(facecolor="white", alpha=0.85,
                                   edgecolor="red" if crossed else "none",
                                   pad=1))
                drawn_buses.add(prefix)

    # ---------- Parent's OWN pin segments on its outer edges ----------
    parent_bus_segs = bus_segments_per_edge(bus_groups, w, h)
    for prefix, edges in parent_bus_segs.items():
        for edge, (a, b, bits) in edges.items():
            if edge == "N":
                x0 = a; x1 = b; y0 = y1 = h
            elif edge == "S":
                x0 = a; x1 = b; y0 = y1 = 0
            elif edge == "E":
                x0 = x1 = w; y0 = a; y1 = b
            else:
                x0 = x1 = 0; y0 = a; y1 = b
            ax.plot([x0, x1], [y0, y1], color=EDGE_COLOR[edge],
                    linewidth=LW_SEG, alpha=0.9, solid_capstyle="butt")
            # Label only buses with >=8 bits or all small buses if few
            if bits >= 8 and (b - a) > 5:
                mid = (a + b) / 2
                off = max(w, h) * 0.025
                if edge == "N":
                    ax.text(mid, h + off, f"{prefix}×{bits}",
                            ha="center", va="bottom", fontsize=7,
                            color=EDGE_COLOR[edge])
                elif edge == "S":
                    ax.text(mid, -off, f"{prefix}×{bits}",
                            ha="center", va="top", fontsize=7,
                            color=EDGE_COLOR[edge])
                elif edge == "E":
                    ax.text(w + off, mid, f"{prefix}×{bits}",
                            ha="left", va="center", fontsize=7,
                            color=EDGE_COLOR[edge])
                else:
                    ax.text(-off, mid, f"{prefix}×{bits}",
                            ha="right", va="center", fontsize=7,
                            color=EDGE_COLOR[edge])

    # ---------- Chip-IO arrows: buses on a sub-macro that aren't paired
    # with another sub-macro, AND don't match a parent pin. These go to
    # the die edge (chip pin).
    for idx, s in enumerate(sub_info):
        for prefix, edges in s["bus_segs"].items():
            # Skip buses paired with another sub-macro
            if len([1 for (i, _) in bus_owners[prefix] if i != idx]):
                continue
            # Skip buses that the parent itself exposes (those are
            # already drawn as parent-edge segments)
            if prefix in parent_bus_segs:
                continue
            # This bus exits the parent — draw an arrow to the nearest
            # die edge.
            for edge, seg in edges.items():
                p1 = bus_segment_midpoint(seg, edge, s["x"], s["y"],
                                            s["w"], s["h"])
                # Target: die edge perpendicular to the sub-macro's edge
                if edge in ("N", "S"):
                    p2 = (p1[0], h if edge == "N" else 0)
                else:
                    p2 = (w if edge == "E" else 0, p1[1])
                bits = seg[2]
                ax.annotate("", xy=p2, xytext=p1,
                            arrowprops=dict(arrowstyle="->",
                                            color="#444",
                                            linewidth=max(0.4, bits / 300),
                                            alpha=0.55))

    # ---------- Legends ----------
    if sub_info:
        counts = collections.Counter(s["module"] for s in sub_info)
        sub_handles = [patches.Patch(facecolor=module_color[m],
                                      edgecolor="#222",
                                      label=f"{m} ×{counts[m]}")
                       for m in sorted(counts)]
        first = ax.legend(handles=sub_handles, loc="upper left",
                          bbox_to_anchor=(1.02, 1.0),
                          fontsize=7, framealpha=0.9, title="Sub-macros")
        ax.add_artist(first)
        edge_legend = [patches.Patch(color=EDGE_COLOR["N"], label="N pins"),
                       patches.Patch(color=EDGE_COLOR["S"], label="S pins"),
                       patches.Patch(color=EDGE_COLOR["E"], label="E pins"),
                       patches.Patch(color=EDGE_COLOR["W"], label="W pins"),
                       patches.Patch(color="red", label="line crosses macro")]
        ax.legend(handles=edge_legend, loc="center left",
                  bbox_to_anchor=(1.02, 0.4),
                  fontsize=7, framealpha=0.9, title="Pin edges / bus lines")
    else:
        edge_legend = [patches.Patch(color=EDGE_COLOR["N"], label="N pins"),
                       patches.Patch(color=EDGE_COLOR["S"], label="S pins"),
                       patches.Patch(color=EDGE_COLOR["E"], label="E pins"),
                       patches.Patch(color=EDGE_COLOR["W"], label="W pins")]
        ax.legend(handles=edge_legend, loc="upper left",
                  bbox_to_anchor=(1.02, 1.0),
                  fontsize=7, framealpha=0.9, title="Pin edges")

    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rendered = 0
    skipped = 0
    for macro_dir in sorted(RESULTS.iterdir()):
        if not macro_dir.is_dir():
            continue
        out_path = OUT_DIR / f"{macro_dir.name}.png"
        try:
            ok = render_macro(macro_dir, out_path)
        except Exception as e:
            print(f"  ! {macro_dir.name}: ERROR {e}", file=sys.stderr)
            skipped += 1
            continue
        if ok:
            print(f"  ✓ {macro_dir.name}")
            rendered += 1
        else:
            print(f"  - {macro_dir.name}: no LEF, skipped")
            skipped += 1
    print(f"\n{rendered} rendered, {skipped} skipped → {OUT_DIR.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
