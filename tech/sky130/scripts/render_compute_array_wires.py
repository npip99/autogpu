#!/usr/bin/env python3
"""Config-driven wire diagram for compute_array.

Reads pin_order.cfg files, gen_config.py constants, LEFs, and compute_array.sv
to produce a SINGLE source of truth diagram. Everything is derived; nothing
is duplicated in this script.

Usage:
    python3 tech/sky130/scripts/render_compute_array_wires.py
    # writes build/render/compute_array_wires.{png,svg}
"""
import re
import sys
import math
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

REPO = Path(__file__).resolve().parents[3]   # .../gpu
SUB = REPO / "tech/sky130/submodules"

COMPUTE_PIN_ORDER = SUB / "compute_array/compute_array.pin_order.cfg"
CMD_PIN_ORDER     = SUB / "cmd_unit/cmd_unit.pin_order.cfg"
GEN_CONFIG_PY     = SUB / "compute_array/gen_config.py"
COMPUTE_SV        = REPO / "compute_array/compute_array.sv"
OUT_PNG           = REPO / "build/render/compute_array_wires.png"
OUT_SVG           = REPO / "build/render/compute_array_wires.svg"


# ──────────────────────────────────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────────────────────────────────

def parse_pin_order(path: Path):
    """Parse pin_order.cfg → {'N': [(name, kind), ...], ...}
    kind is 'virtual' for $N entries, else 'pin'.
    Returns the per-side sequence in the order listed in the cfg.
    """
    sides = {"N": [], "E": [], "S": [], "W": []}
    current = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line: continue
        if line.startswith("#") and len(line) >= 2 and line[1] in "NESW":
            current = line[1]
            continue
        if line.startswith("#"):  # ignore other comments
            continue
        if line.startswith("$"):
            try:
                n = int(line[1:])
                if current: sides[current].append((n, "virtual"))
            except ValueError:
                pass
            continue
        if line.startswith("@"):  # annotations like @min_distance
            continue
        if current:
            sides[current].append((line, "pin"))
    return sides


def expand_pin_pattern(pattern, available):
    """Expand a pin_order regex pattern against a list of available pin names.
    The pattern is already a Python-compatible regex (e.g. push_a_bytes\\[.*\\])
    where .* should match digits.
    """
    rx = "^" + pattern.replace(".*", r"\d+") + "$"
    out = [n for n in available if re.match(rx, n)]
    def key(n):
        m = re.search(r"\[(\d+)\]", n)
        return int(m.group(1)) if m else 0
    out.sort(key=key)
    return out


def names_from_pattern(pattern, width_lookup):
    """For compute_array external pin_order, we don't have a LEF yet, so
    generate the pin names directly from the regex + width hint.
    Handles both `foo.*` and `foo\\[.*\\]` styles."""
    if ".*" in pattern:
        base = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)", pattern).group(1)
        w = width_lookup(base)
        return [f"{base}[{i}]" for i in range(w)]
    return [pattern]


def parse_lef(path: Path):
    """Returns ({pin_name: (cx, cy, side)}, macro_w, macro_h) in µm."""
    text = path.read_text()
    m = re.search(r"^\s*SIZE\s+(\S+)\s+BY\s+(\S+)\s*;", text, re.MULTILINE)
    w, h = float(m.group(1)), float(m.group(2))
    pins = {}
    for pn, body in re.findall(r"  PIN (\S+)\s+(.+?)\s+END \1", text, re.DOTALL):
        rm = re.search(r"RECT\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*;", body)
        if not rm: continue
        x1,y1,x2,y2 = (float(rm.group(i)) for i in (1,2,3,4))
        cx, cy = (x1+x2)/2, (y1+y2)/2
        side = min((cx,"W"), (w-cx,"E"), (cy,"S"), (h-cy,"N"))[1]
        pins[pn] = (cx, cy, side)
    return pins, w, h


def parse_gen_config_constants():
    """Extract CMD_OFFSET, CMD_HALO, EAST_MARGIN, NORTH_MARGIN constants from gen_config.py."""
    text = GEN_CONFIG_PY.read_text()
    def get_const(name, default):
        m = re.search(rf"^\s*{name}\s*=\s*(\d+(?:\.\d+)?)", text, re.MULTILINE)
        return float(m.group(1)) if m else default
    return {
        "CMD_OFFSET":   get_const("CMD_OFFSET", 100),
        "CMD_HALO":     get_const("CMD_HALO", 50),
        "EAST_MARGIN":  get_const("EAST_MARGIN", 50),
        "NORTH_MARGIN": get_const("NORTH_MARGIN", 50),
    }


def detect_byte_reverse():
    """Inspect compute_array.sv: did push_a_bytes / push_b_bytes use reversed bit-to-lane?
    Look for pattern push_a_bytes[(MMA_M-1-gi_a)*8 +: 8] or push_a_bytes[gi_a*8 +: 8].
    """
    text = COMPUTE_SV.read_text()
    rev_a = bool(re.search(r"push_byte\s*\(push_a_bytes\[\(MMA_M[-_ ]*1[-_ ]*gi_a\)\*8", text))
    rev_b = bool(re.search(r"push_byte\s*\(push_b_bytes\[\(MMA_N[-_ ]*1[-_ ]*gj_b\)\*8", text))
    return rev_a, rev_b


# ──────────────────────────────────────────────────────────────────────────
# Layout derivation (same math as gen_config.py)
# ──────────────────────────────────────────────────────────────────────────

def snap(v): return math.ceil(v / 50) * 50

def derive_layout(constants, lef_sizes):
    """Compute all macro positions, mirroring gen_config.py logic."""
    CMD_W, CMD_H = lef_sizes["cmd_unit"]
    SAW, _ = lef_sizes["skew_lane_a"]
    _, SBH = lef_sizes["skew_lane_b"]
    CW, CH = lef_sizes["mac_tmem_cell"]
    MMA = 32
    CMD_OFFSET = constants["CMD_OFFSET"]
    CMD_HALO   = constants["CMD_HALO"]
    NORTH_MARGIN = constants["NORTH_MARGIN"]
    EAST_MARGIN  = constants["EAST_MARGIN"]
    cw_p = snap(CW + 60); ch_p = snap(CH + 60)
    origin_x = snap(max(CMD_OFFSET + CMD_W + CMD_HALO, snap(SAW)))
    origin_y = snap(max(CMD_OFFSET + CMD_H + CMD_HALO, snap(SBH)))
    askew_x = origin_x - snap(SAW)
    bskew_y = origin_y - snap(SBH)
    die_w = origin_x + MMA * cw_p + EAST_MARGIN
    die_h = origin_y + MMA * ch_p + NORTH_MARGIN
    return {
        "cmd_origin": (CMD_OFFSET, CMD_OFFSET),
        "cmd_w": CMD_W, "cmd_h": CMD_H,
        "origin_x": origin_x, "origin_y": origin_y,
        "askew_x": askew_x, "bskew_y": bskew_y,
        "cw_p": cw_p, "ch_p": ch_p,
        "die_w": die_w, "die_h": die_h,
        "MMA": MMA,
    }


def assign_face_positions(side, face_seq, available_pins, face_start, face_end):
    """Given the pin_order side sequence and available pin names from LEF,
    compute (pin_name → coord_along_face). Expands regex patterns and accounts
    for $N virtual pin slots.
    """
    # Expand each entry to a list of pin names; collect total slot count
    expanded = []
    for entry, kind in face_seq:
        if kind == "virtual":
            expanded.extend([None] * entry)
        else:
            names = expand_pin_pattern(entry, available_pins)
            if not names and re.match(r"^[A-Za-z_][A-Za-z_0-9]*$", entry) and entry in available_pins:
                names = [entry]
            expanded.extend(names)
    total = len(expanded)
    if total == 0:
        return {}
    pitch = (face_end - face_start) / total
    out = {}
    for i, name in enumerate(expanded):
        if name is None: continue
        out[name] = face_start + (i + 0.5) * pitch
    return out


def cmd_pin_xy(face, name, cmd_x, cmd_y, cmd_w, cmd_h, pos):
    if face == "N": return pos[name], cmd_y + cmd_h
    if face == "S": return pos[name], cmd_y
    if face == "W": return cmd_x, pos[name]
    if face == "E": return cmd_x + cmd_w, pos[name]


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    constants = parse_gen_config_constants()
    print(f"gen_config constants: {constants}")

    # Load LEFs
    def latest(m):
        return sorted((SUB/m/"runs").glob("RUN_*/final/lef/*.lef"))[-1]
    cmd_pins, cmd_w_lef, cmd_h_lef = parse_lef(latest("cmd_unit"))
    cell_pins, cw_lef, ch_lef = parse_lef(latest("mac_tmem_cell"))
    skew_a_pins, _, _ = parse_lef(latest("skew_lane_a"))
    skew_b_pins, _, _ = parse_lef(latest("skew_lane_b"))

    # Prefer macro DIE_AREA from config.yaml over LEF size — so we can iterate
    # on layout without re-hardening. This makes the simulation reflect the
    # CURRENT config, not the LAST hardened size.
    def yaml_die(macro):
        p = SUB / macro / "config.yaml"
        if not p.exists(): return None
        m = re.search(r"DIE_AREA:\s*\[\s*0\s*,\s*0\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]", p.read_text())
        return (float(m.group(1)), float(m.group(2))) if m else None

    cmd_w, cmd_h = yaml_die("cmd_unit") or (cmd_w_lef, cmd_h_lef)
    print(f"cmd_unit size: LEF=({cmd_w_lef},{cmd_h_lef}) effective=({cmd_w},{cmd_h})")
    lef_sizes = {
        "cmd_unit": (cmd_w, cmd_h),
        "mac_tmem_cell": (cw_lef, ch_lef),
        "skew_lane_a": (250.0, 300.0),
        "skew_lane_b": (250.0, 300.0),
    }
    L = derive_layout(constants, lef_sizes)
    print(f"layout: cmd@{L['cmd_origin']} origin=({L['origin_x']},{L['origin_y']}) die=({L['die_w']},{L['die_h']})")

    # Parse pin orders
    cmd_order = parse_pin_order(CMD_PIN_ORDER)
    ext_order = parse_pin_order(COMPUTE_PIN_ORDER)
    print(f"cmd_unit pin_order: N={sum(1 for _,k in cmd_order['N'] if k=='pin')} entries, "
          f"E={sum(1 for _,k in cmd_order['E'] if k=='pin')} entries, "
          f"S={sum(1 for _,k in cmd_order['S'] if k=='pin')} entries, "
          f"W={sum(1 for _,k in cmd_order['W'] if k=='pin')} entries")

    # Compute cmd_unit pin coords (along face) using its pin_order + available pins from LEF
    cmd_pin_names = list(cmd_pins.keys())
    cmd_x, cmd_y = L["cmd_origin"]
    cmd_n_pos = assign_face_positions("N", cmd_order["N"], cmd_pin_names, cmd_x, cmd_x + cmd_w)
    cmd_s_pos = assign_face_positions("S", cmd_order["S"], cmd_pin_names, cmd_x, cmd_x + cmd_w)
    cmd_e_pos = assign_face_positions("E", cmd_order["E"], cmd_pin_names, cmd_y, cmd_y + cmd_h)
    cmd_w_pos = assign_face_positions("W", cmd_order["W"], cmd_pin_names, cmd_y, cmd_y + cmd_h)

    # compute_array external pins — generate names from patterns directly (no LEF yet)
    ext_pos = {"N": {}, "E": {}, "S": {}, "W": {}}
    for side, lst in ext_order.items():
        s_start, s_end = (50, L["die_w"]-50) if side in ("N","S") else (50, L["die_h"]-50)
        # Expand patterns sequentially
        expanded = []
        for entry, kind in lst:
            if kind == "virtual":
                expanded.extend([None] * entry)
            else:
                names = names_from_pattern(entry, guess_width)
                expanded.extend(names)
        total = len(expanded)
        if total == 0: continue
        pitch = (s_end - s_start) / total
        for i, name in enumerate(expanded):
            if name is None: continue
            ext_pos[side][name] = s_start + (i + 0.5) * pitch
    print(f"ext N pins: {len(ext_pos['N'])}, S: {len(ext_pos['S'])}, E: {len(ext_pos['E'])}, W: {len(ext_pos['W'])}")

    # Detect bit reverse
    rev_a, rev_b = detect_byte_reverse()
    print(f"compute_array.sv: push_a reversed={rev_a}, push_b reversed={rev_b}")

    # Find which face each cmd_unit pin is on
    def cmd_pin_pos(name):
        if name in cmd_n_pos: return cmd_n_pos[name], cmd_y + cmd_h, "N"
        if name in cmd_s_pos: return cmd_s_pos[name], cmd_y, "S"
        if name in cmd_e_pos: return cmd_x + cmd_w, cmd_e_pos[name], "E"
        if name in cmd_w_pos: return cmd_x, cmd_w_pos[name], "W"
        return None

    # ── Build wire list ──
    wires = []   # (sx, sy, dx, dy, group)

    # push_a_bytes
    for b in range(256):
        p = cmd_pin_pos(f"push_a_bytes[{b}]")
        if not p: continue
        byte = b // 8
        lane = (31 - byte) if rev_a else byte
        ay = L["origin_y"] + lane * L["ch_p"] + 150  # skew center y
        wires.append((p[0], p[1], L["askew_x"], ay, "push_a"))

    # push_b_bytes
    for b in range(256):
        p = cmd_pin_pos(f"push_b_bytes[{b}]")
        if not p: continue
        byte = b // 8
        lane = (31 - byte) if rev_b else byte
        bx = L["origin_x"] + lane * L["cw_p"] + 125
        wires.append((p[0], p[1], bx, L["bskew_y"]+300, "push_b"))

    # chip-IO → ext N (and any side they actually land on)
    for name in ["mma_issue","mma_accum","mma_busy","mma_done","arrive_en","scrub_en"] + \
                [f"mma_slot[{i}]" for i in range(2)] + \
                [f"mma_bar_id[{i}]" for i in range(32)] + \
                [f"issue_a_off[{i}]" for i in range(32)] + \
                [f"issue_b_off[{i}]" for i in range(32)] + \
                [f"issue_a_stride[{i}]" for i in range(32)] + \
                [f"issue_b_stride[{i}]" for i in range(32)] + \
                [f"arrive_bar_id[{i}]" for i in range(32)]:
        p = cmd_pin_pos(name)
        if not p: continue
        # Where does this land on compute_array external edge?
        for side, names_pos in ext_pos.items():
            if name in names_pos:
                if side == "N": ex, ey = names_pos[name], L["die_h"]
                elif side == "S": ex, ey = names_pos[name], 0
                elif side == "E": ex, ey = L["die_w"], names_pos[name]
                elif side == "W": ex, ey = 0, names_pos[name]
                wires.append((p[0], p[1], ex, ey, "chip_io"))
                break

    # rd_a + rd_b
    for sig in ["rd_a_en"] + [f"rd_a_addr[{i}]" for i in range(32)] + \
               [f"rd_a_data[{i}]" for i in range(256)] + ["rd_a_valid","rd_a_stall_in"] + \
               ["rd_b_en"] + [f"rd_b_addr[{i}]" for i in range(32)] + \
               [f"rd_b_data[{i}]" for i in range(256)] + ["rd_b_valid","rd_b_stall_in"]:
        p = cmd_pin_pos(sig)
        if not p: continue
        for side, names_pos in ext_pos.items():
            if sig in names_pos:
                if side == "W": ex, ey = 0, names_pos[sig]
                elif side == "E": ex, ey = L["die_w"], names_pos[sig]
                elif side == "N": ex, ey = names_pos[sig], L["die_h"]
                elif side == "S": ex, ey = names_pos[sig], 0
                wires.append((p[0], p[1], ex, ey, "rd_ab"))
                break

    # drain status + clk/reset
    for name in ["drain_issue","drain_busy","drain_done","drain_row_valid","drain_last","clk","reset"] + \
                [f"drain_slot[{i}]" for i in range(2)] + \
                [f"drain_row_idx[{i}]" for i in range(5)]:
        p = cmd_pin_pos(name)
        if not p: continue
        for side, names_pos in ext_pos.items():
            if name in names_pos:
                if side == "S": ex, ey = names_pos[name], 0
                elif side == "N": ex, ey = names_pos[name], L["die_h"]
                elif side == "E": ex, ey = L["die_w"], names_pos[name]
                elif side == "W": ex, ey = 0, names_pos[name]
                grp = "clk_reset" if name in ("clk","reset") else "drain_status"
                wires.append((p[0], p[1], ex, ey, grp))
                break

    # broadcasts: push_now, push_slot, push_accum → all skews;  drain_en + drain_slot → all cells
    bcast_pins = ["push_now_o","push_accum_o"] + [f"push_slot_o[{i}]" for i in range(2)]
    for name in bcast_pins:
        p = cmd_pin_pos(name)
        if not p: continue
        for r in range(32):
            wires.append((p[0], p[1], L["askew_x"], L["origin_y"] + r*L["ch_p"] + 150, "bcast_skews"))
        for c in range(32):
            wires.append((p[0], p[1], L["origin_x"] + c*L["cw_p"] + 125, L["bskew_y"]+300, "bcast_skews"))

    bcast_cell_pins = ["drain_en_o"] + [f"drain_slot_to_cells[{i}]" for i in range(2)]
    for name in bcast_cell_pins:
        p = cmd_pin_pos(name)
        if not p: continue
        for r in range(0, 32, 2):
            for c in range(0, 32, 2):
                cx = L["origin_x"] + c*L["cw_p"] + cw_lef/2
                cy = L["origin_y"] + r*L["ch_p"] + ch_lef/2
                wires.append((p[0], p[1], cx, cy, "bcast_cells"))

    # drain_row_data: from row 0 cells N face to S edge external pin
    for c in range(32):
        cell_x = L["origin_x"] + c*L["cw_p"] + cw_lef/2
        cell_y = L["origin_y"] + ch_lef
        for b in range(32):
            name = f"drain_row_data[{c*32+b}]"
            if name in ext_pos["S"]:
                wires.append((cell_x, cell_y, ext_pos["S"][name], 0, "drain_row_data"))

    print(f"wires: {len(wires)}")

    # ── Draw ──
    fig, ax = plt.subplots(figsize=(22, 22))
    ax.set_xlim(-800, L["die_w"]+800); ax.set_ylim(-800, L["die_h"]+800)
    ax.set_aspect("equal"); ax.set_facecolor("white")
    ax.add_patch(plt.Rectangle((0,0), L["die_w"], L["die_h"], fill=False, edgecolor="black", linewidth=2))
    ax.add_patch(plt.Rectangle(L["cmd_origin"], L["cmd_w"], L["cmd_h"],
                               fill=True, facecolor="#ffeeee", edgecolor="red", linewidth=2, zorder=5))
    ax.text(L["cmd_origin"][0]+L["cmd_w"]/2, L["cmd_origin"][1]+L["cmd_h"]/2, "cmd_unit",
            ha="center", va="center", fontsize=11, fontweight="bold", zorder=6)
    for r in range(L["MMA"]):
        ax.add_patch(plt.Rectangle((L["askew_x"], L["origin_y"]+r*L["ch_p"]), 250, 300,
                                   fill=True, facecolor="#eef", edgecolor="blue", linewidth=0.3, zorder=3))
    for c in range(L["MMA"]):
        ax.add_patch(plt.Rectangle((L["origin_x"]+c*L["cw_p"], L["bskew_y"]), 250, 300,
                                   fill=True, facecolor="#efe", edgecolor="green", linewidth=0.3, zorder=3))
    ax.add_patch(plt.Rectangle((L["origin_x"], L["origin_y"]), L["MMA"]*L["cw_p"], L["MMA"]*L["ch_p"],
                               fill=False, edgecolor="gray", linewidth=0.3, linestyle="--", zorder=1))

    COLORS = {"push_a":"#1f77b4","push_b":"#2ca02c","rd_ab":"#ff7f0e","chip_io":"#9467bd",
              "bcast_skews":"orange","bcast_cells":"gold","drain_status":"#8c564b",
              "drain_row_data":"#17becf","clk_reset":"red"}
    LW = 0.04
    for sx,sy,dx,dy,g in wires:
        if g.startswith("bcast"):
            ax.plot([sx,dx],[sy,dy], color=COLORS[g], lw=0.03, alpha=0.25, solid_capstyle="butt", zorder=1)
        elif g == "clk_reset":
            ax.plot([sx,dx],[sy,dy], color=COLORS[g], lw=1.0, linestyle="--", alpha=0.9, zorder=4)
        elif g == "drain_status":
            ax.plot([sx,dx],[sy,dy], color=COLORS[g], lw=0.5, alpha=0.9, solid_capstyle="butt", zorder=2)
        elif g == "drain_row_data":
            ax.plot([sx,dx],[sy,dy], color=COLORS[g], lw=LW*0.7, alpha=0.5, zorder=2)
        else:
            ax.plot([sx,dx],[sy,dy], color=COLORS[g], lw=LW, alpha=0.9, solid_capstyle="butt", zorder=2)

    # Dots
    for side, names_pos in ext_pos.items():
        for name, coord in names_pos.items():
            if side == "N": x, y = coord, L["die_h"]
            elif side == "S": x, y = coord, 0
            elif side == "E": x, y = L["die_w"], coord
            elif side == "W": x, y = 0, coord
            if name.startswith("drain_row_data"): c, sz = "#17becf", 1.8
            elif name in ("clk","reset"): c, sz = "red", 6
            elif name.startswith(("rd_a_","rd_b_")): c, sz = "#ff7f0e", 2.0
            elif name.startswith("drain_"): c, sz = "#8c564b", 4
            else: c, sz = "#9467bd", 2.5
            ax.plot(x, y, marker="o", markersize=sz, color=c, zorder=6)

    legend = [
        Line2D([0],[0], color=COLORS["push_a"], lw=2, label=f"push_a_bytes (rev={rev_a})"),
        Line2D([0],[0], color=COLORS["push_b"], lw=2, label=f"push_b_bytes (rev={rev_b})"),
        Line2D([0],[0], color=COLORS["rd_ab"], lw=2, label="rd_a + rd_b"),
        Line2D([0],[0], color=COLORS["chip_io"], lw=2, label="chip-IO"),
        Line2D([0],[0], color=COLORS["bcast_skews"], lw=2, label="bcast→skews"),
        Line2D([0],[0], color=COLORS["bcast_cells"], lw=2, label="bcast→cells"),
        Line2D([0],[0], color=COLORS["drain_status"], lw=2, label="drain status"),
        Line2D([0],[0], color=COLORS["drain_row_data"], lw=2, label="drain_row_data"),
        Line2D([0],[0], color=COLORS["clk_reset"], lw=2, linestyle="--", label="clk + reset"),
    ]
    ax.legend(handles=legend, loc="upper right", fontsize=11, framealpha=0.95)
    ax.set_title(f"compute_array — config-driven render\n"
                 f"CMD_OFFSET={int(constants['CMD_OFFSET'])} CMD_HALO={int(constants['CMD_HALO'])} "
                 f"N_MARGIN={int(constants['NORTH_MARGIN'])} E_MARGIN={int(constants['EAST_MARGIN'])}",
                 fontsize=12, pad=15)
    ax.grid(True, alpha=0.1)

    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
    plt.savefig(OUT_SVG, bbox_inches="tight")
    print(f"\nwrote {OUT_PNG}")
    print(f"wrote {OUT_SVG}")


def guess_width(base):
    """Hardcoded width lookup for known signals (TODO: derive from compute_array.sv)."""
    widths = {
        "mma_slot": 2, "mma_bar_id": 32, "issue_a_off": 32, "issue_b_off": 32,
        "issue_a_stride": 32, "issue_b_stride": 32, "arrive_bar_id": 32,
        "rd_a_addr": 32, "rd_a_data": 256, "rd_b_addr": 32, "rd_b_data": 256,
        "drain_slot": 2, "drain_row_idx": 5, "drain_row_data": 1024,
    }
    return widths.get(base, 1)


if __name__ == "__main__":
    main()
