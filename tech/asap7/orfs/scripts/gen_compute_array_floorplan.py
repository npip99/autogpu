#!/usr/bin/env python3
"""Generate the asap7 compute_array floorplan + macro placement TCL + preview PNG.

Mirrors sky130's compute_array structure (cmd_unit in SW corner, skew_lane_a
column on the west, skew_lane_b row on the south, mac_tmem_cell 32x32 grid in
the interior) at asap7 scale.

Outputs into tech/asap7/orfs/:
  - compute_array.macro_placement.tcl   (place_macro lines for ORFS)
  - compute_array.floorplan_preview.png (visual sanity check)

The DIE_AREA values used here should be pasted into compute_array.config.mk.
"""
import os
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

# Repo layout.
REPO = Path(__file__).resolve().parents[4]
RESULTS = REPO / "build/orfs/results/asap7"
OUT_DIR = REPO / "tech/asap7/orfs"

# Mac-grid shape — matches compute_array.sv parameters MMA_M, MMA_N. Override
# with --rows / --cols / --suffix to generate a smaller variant for iteration.
import argparse
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--rows", type=int, default=32)
_parser.add_argument("--cols", type=int, default=32)
_parser.add_argument("--suffix", type=str, default="",
                     help="Suffix on output files (e.g. '_tiny' → compute_array_tiny.macro_placement.tcl)")
_args, _ = _parser.parse_known_args()
ROWS, COLS = _args.rows, _args.cols
SUFFIX = _args.suffix

# Pitch + channel spacing (µm). Mac pitch leaves a routing channel between
# adjacent cells; skew lanes get their own dedicated column / row.
MAC_PITCH         = 55.0   # 34.5 µm mac + 20.5 µm channel. Wider than the
                            # natural 5-10 µm so the row fragments between
                            # macros are big enough for PDN to route M1
                            # followpins without PDN-0179 errors.
CHANNEL_MAC_SKEW  = 10.0   # gap between skew lane and mac grid
CHANNEL_CMD_SKEW  = 10.0   # gap between cmd_unit and skew lanes
CHANNEL_DIE_EDGE  = 40.0   # margin from die edge to outermost macro


def macro_die_size(name: str) -> tuple[float, float]:
    """Read DIEAREA from a hardened macro's 6_final.def, return (W, H) in µm."""
    def_path = RESULTS / name / "base/6_final.def"
    if not def_path.exists():
        sys.exit(f"ERROR: {def_path} missing — harden {name} first")
    text = def_path.read_text()
    m = re.search(r"DIEAREA \( 0 0 \) \( (\d+) (\d+) \)", text)
    if not m:
        sys.exit(f"ERROR: no DIEAREA in {def_path}")
    return float(m.group(1)) / 1000, float(m.group(2)) / 1000


def parse_pin_sides() -> dict[str, list[str]]:
    """Parse the sky130 pin_order.cfg into {side: [pin_regex_or_name, ...]}.

    pin_order.cfg uses #W, #N, #S, #E section headers; each line under a header
    is a literal pin name or a `.*`-suffixed wildcard.
    """
    cfg = REPO / "tech/sky130/submodules/compute_array/compute_array.pin_order.cfg"
    sides: dict[str, list[str]] = {"W": [], "N": [], "S": [], "E": []}
    current = None
    for raw in cfg.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#"):
            current = line[1:].strip()
            continue
        if not line or current not in sides:
            continue
        sides[current].append(line)
    return sides


def parse_compute_array_ports() -> list[tuple[str, str, int]]:
    """Extract (direction, name, width) for every compute_array port.

    Reads compute_array.sv. Width is 1 unless an unpacked range like [31:0] is
    present, in which case width = high - low + 1. MMA_M/MMA_N parameter
    expressions are resolved with literal 32.
    """
    sv = (REPO / "compute_array/compute_array.sv").read_text()
    body = re.search(r"module compute_array.*?\(([\s\S]*?)\);", sv).group(1)
    ports = []
    for line in body.splitlines():
        m = re.match(
            r"\s*(input|output|inout)\s+(?:logic|wire|reg)?\s*"
            r"(?:\[([^\]]+)\])?\s*"
            r"([a-zA-Z_][\w]*)",
            line,
        )
        if not m:
            continue
        direction, rng, name = m.group(1), m.group(2), m.group(3)
        width = 1
        if rng:
            rng = rng.replace("MMA_M", "32").replace("MMA_N", "32").replace("N_SLOTS", "4")
            rng = re.sub(r"\$clog2\(([^)]+)\)", lambda x: str(int.bit_length(int(eval(x.group(1)))-1)), rng)
            try:
                hi, lo = map(int, eval(rng + ",", {}, {}) if "," in rng else [eval(rng.split(":")[0]), eval(rng.split(":")[1])])
                width = hi - lo + 1
            except Exception:
                width = 1
        ports.append((direction, name, width))
    return ports


def side_for_pin(pin_name: str, sides_cfg: dict[str, list[str]]) -> str:
    """Find which side a pin belongs on by matching pin_order.cfg patterns.

    Order: W, N, S, E (matches sky130 convention). Patterns ending in `.*`
    match any pin that starts with the prefix (e.g. `rd_a_addr.*` matches
    `rd_a_addr`).
    """
    for side in ("W", "N", "S", "E"):
        for pat in sides_cfg[side]:
            base = pat.rstrip(".*").rstrip("*")
            if pin_name == base or (pat.endswith(".*") and pin_name.startswith(base)):
                return side
    return "E"  # default: clk/reset go east


def main():
    # Hardened-macro dimensions.
    mac_w, mac_h = macro_die_size("mac_tmem_cell")
    sa_w, sa_h   = macro_die_size("skew_lane_a")
    sb_w, sb_h   = macro_die_size("skew_lane_b")
    cu_w, cu_h   = macro_die_size("cmd_unit")
    print(f"Macro sizes (µm):")
    print(f"  mac_tmem_cell {mac_w} x {mac_h}")
    print(f"  skew_lane_a   {sa_w} x {sa_h}")
    print(f"  skew_lane_b   {sb_w} x {sb_h}")
    print(f"  cmd_unit      {cu_w} x {cu_h}")

    # ---- Layout coordinates (lower-left origin, µm) -----------------------
    margin = CHANNEL_DIE_EDGE

    cmd_x, cmd_y = margin, margin

    # skew_lane_a is a column on the west of the mac grid. Its 32 instances
    # share one x, share the same y-pitch as mac rows, and each is bottom-
    # aligned with the corresponding mac row (matches sky130 convention).
    sa_x = cmd_x + cu_w + CHANNEL_CMD_SKEW
    # skew_lane_b is a row on the south of the mac grid. Same idea swapped.
    sb_y = cmd_y + cu_h + CHANNEL_CMD_SKEW

    # Mac grid origin sits NE of both skew lanes.
    mac_x0 = sa_x + sa_w + CHANNEL_MAC_SKEW
    mac_y0 = sb_y + sb_h + CHANNEL_MAC_SKEW

    sa_y0 = mac_y0
    sb_x0 = mac_x0

    # Die = furthest-east mac column right edge + margin, etc.
    mac_right = mac_x0 + (COLS - 1) * MAC_PITCH + mac_w
    mac_top   = mac_y0 + (ROWS - 1) * MAC_PITCH + mac_h
    die_w = mac_right + margin
    die_h = mac_top   + margin
    # Round up to a clean number for DIE_AREA so chip_top integration math is easier.
    die_w = ((die_w // 50) + 1) * 50
    die_h = ((die_h // 50) + 1) * 50
    print(f"\nDIE: {die_w} x {die_h} µm")
    print(f"Mac grid: {COLS}x{ROWS} cells, pitch {MAC_PITCH} µm, "
          f"x=[{mac_x0}, {mac_right}], y=[{mac_y0}, {mac_top}]")

    # ---- Emit macro_placement.tcl ----------------------------------------
    tcl_lines = ["# Auto-generated by tech/asap7/orfs/scripts/gen_compute_array_floorplan.py"]
    tcl_lines.append("# Mac grid origin at NE of skew lanes; cmd_unit in SW corner.\n")

    # Yosys preserves SystemVerilog `gen_X[N].u_Y` instance names by escaping
    # the brackets — the ODB stores them with literal `\[N\]`. TCL braces
    # `{...}` pass the name through verbatim (no substitution).
    tcl_lines.append(f"place_macro -macro_name {{u_cmd}} -location {{{cmd_x} {cmd_y}}} -orientation R0")
    for i in range(ROWS):
        tcl_lines.append(
            f"place_macro -macro_name {{gen_a_skew\\[{i}\\].u_a}} "
            f"-location {{{sa_x} {sa_y0 + i*MAC_PITCH}}} -orientation R0"
        )
    for j in range(COLS):
        tcl_lines.append(
            f"place_macro -macro_name {{gen_b_skew\\[{j}\\].u_b}} "
            f"-location {{{sb_x0 + j*MAC_PITCH} {sb_y}}} -orientation R0"
        )
    for i in range(ROWS):
        for j in range(COLS):
            tcl_lines.append(
                f"place_macro -macro_name {{gen_row\\[{i}\\].gen_col\\[{j}\\].u_cell}} "
                f"-location {{{mac_x0 + j*MAC_PITCH} {mac_y0 + i*MAC_PITCH}}} -orientation R0"
            )
    tcl_path = OUT_DIR / f"compute_array{SUFFIX}.macro_placement.tcl"
    tcl_path.write_text("\n".join(tcl_lines) + "\n")
    print(f"\nWrote {tcl_path} ({len(tcl_lines)-2} macros)")

    # ---- Render preview PNG ----------------------------------------------
    sides_cfg = parse_pin_sides()
    ports = parse_compute_array_ports()

    # Bin pins by side and count flat-bit slots so we can space them along
    # the die edge. Total bit-slots per side = sum of port widths.
    side_pins: dict[str, list[tuple[str, int]]] = {"W": [], "N": [], "S": [], "E": []}
    for _, name, w in ports:
        side = side_for_pin(name, sides_cfg)
        side_pins[side].append((name, w))

    fig, ax = plt.subplots(figsize=(12, 12))
    ax.set_xlim(-die_w * 0.05, die_w * 1.05)
    ax.set_ylim(-die_h * 0.05, die_h * 1.05)
    ax.set_aspect("equal")
    ax.set_title(f"compute_array asap7 floorplan — {die_w:g} × {die_h:g} µm")
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")

    # Die outline.
    ax.add_patch(patches.Rectangle((0, 0), die_w, die_h,
                                   linewidth=2, edgecolor="black",
                                   facecolor="#fafafa"))

    # Macros.
    def macro(x, y, w, h, color, label=None):
        ax.add_patch(patches.Rectangle((x, y), w, h, linewidth=0.5,
                                       edgecolor="#333", facecolor=color, alpha=0.85))
        if label:
            ax.text(x + w/2, y + h/2, label, ha="center", va="center",
                    fontsize=6, color="black")

    macro(cmd_x, cmd_y, cu_w, cu_h, "#ffd29b", "cmd")
    for i in range(ROWS):
        macro(sa_x, sa_y0 + i*MAC_PITCH, sa_w, sa_h, "#a8d8ea")
    for j in range(COLS):
        macro(sb_x0 + j*MAC_PITCH, sb_y, sb_w, sb_h, "#aa96da")
    for i in range(ROWS):
        for j in range(COLS):
            macro(mac_x0 + j*MAC_PITCH, mac_y0 + i*MAC_PITCH, mac_w, mac_h, "#c7ecee")

    # IO pins as small dots along the die edges. Color = direction.
    dir_color = {"input": "#27ae60", "output": "#c0392b", "inout": "#8e44ad"}
    for side, pin_list in side_pins.items():
        bits = sum(w for _, w in pin_list) or 1
        for idx, (name, width) in enumerate(pin_list):
            # Center of this pin's bit-range along the edge.
            cum_before = sum(w for n, w in pin_list[:idx])
            center_frac = (cum_before + width / 2) / bits
            for b in range(width):
                bit_frac = (cum_before + b + 0.5) / bits
                if side == "W":
                    x, y = -8, bit_frac * die_h
                elif side == "E":
                    x, y = die_w + 8, bit_frac * die_h
                elif side == "S":
                    x, y = bit_frac * die_w, -8
                else:  # N
                    x, y = bit_frac * die_w, die_h + 8
                # Direction = whichever direction owns this name.
                direction = next((d for d, n, _ in ports if n == name), "input")
                ax.plot(x, y, "o", markersize=2.5,
                        color=dir_color.get(direction, "#000000"))
            # Label.
            if side == "W":
                ax.text(-15, center_frac * die_h, name, ha="right", va="center", fontsize=5)
            elif side == "E":
                ax.text(die_w + 15, center_frac * die_h, name, ha="left", va="center", fontsize=5)
            elif side == "S":
                ax.text(center_frac * die_w, -15, name, ha="center", va="top", fontsize=5, rotation=90)
            else:
                ax.text(center_frac * die_w, die_h + 15, name, ha="center", va="bottom", fontsize=5, rotation=90)

    # Legend.
    legend_patches = [
        patches.Patch(facecolor="#ffd29b", label="cmd_unit"),
        patches.Patch(facecolor="#a8d8ea", label="skew_lane_a (col, 32)"),
        patches.Patch(facecolor="#aa96da", label="skew_lane_b (row, 32)"),
        patches.Patch(facecolor="#c7ecee", label="mac_tmem_cell (32×32)"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=dir_color["input"], markersize=8, label="input pin"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=dir_color["output"], markersize=8, label="output pin"),
    ]
    ax.legend(handles=legend_patches, loc="upper right", fontsize=8)

    png_path = OUT_DIR / f"compute_array{SUFFIX}.floorplan_preview.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    print(f"Wrote {png_path}")

    print(f"\nFor compute_array.config.mk:")
    print(f"  CORE_MARGIN     = {CHANNEL_DIE_EDGE}")
    print(f"  DIE_AREA        = 0 0 {die_w} {die_h}")
    print(f"  CORE_AREA       = {CHANNEL_DIE_EDGE/2} {CHANNEL_DIE_EDGE/2} "
          f"{die_w - CHANNEL_DIE_EDGE/2} {die_h - CHANNEL_DIE_EDGE/2}")


if __name__ == "__main__":
    main()
