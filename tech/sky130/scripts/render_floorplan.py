#!/usr/bin/env python3
"""
Render chip_top_floorplan.yaml to a PNG so you can eyeball the layout
without launching OpenLane.

By default also draws every pin from each module's stub LEF as a dot
(colored by net width) and draws lines between connected pins parsed
out of top/chip_top.sv. Pass --no-pins to skip pin/net rendering.

Usage:
    render_floorplan.py [--no-pins] [out.png]
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import yaml


REPO = Path(__file__).resolve().parents[3]
STUB_LEF_DIR = REPO / "build/sv2v/lef-stub"
CHIP_TOP_SV = REPO / "top/chip_top.sv"


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


def parse_stub_lef_pins(mod: str) -> dict[str, tuple[float, float]]:
    """Return {pin_name: (cx, cy)} in the module's local frame."""
    lef = STUB_LEF_DIR / f"{mod}.lef"
    if not lef.exists():
        return {}
    out = {}
    current_pin = None
    current_rect = None
    for line in lef.read_text().splitlines():
        line = line.strip()
        m = re.match(r"PIN\s+(\S+)", line)
        if m:
            current_pin = m.group(1)
            current_rect = None
            continue
        if line.startswith("END") and current_pin and line.split()[1:] == [current_pin]:
            if current_rect:
                x0, y0, x1, y1 = current_rect
                out[current_pin] = ((x0 + x1) / 2, (y0 + y1) / 2)
            current_pin = None
            continue
        m = re.match(r"RECT\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", line)
        if m and current_pin:
            current_rect = tuple(float(x) for x in m.groups())
    return out


def parse_chip_top_connections() -> dict[str, list[tuple[str, str]]]:
    """Return {net_name: [(instance_short_name, port_name), ...]}.

    instance_short_name is the module name (e.g. "cmdproc"), derived from
    "u_cmdproc" -> strip "u_". chip_top.sv uses one instance per module.
    """
    text = CHIP_TOP_SV.read_text()
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # Find each `module_name u_inst ( ... );` block.
    block_re = re.compile(
        r"(\w+)\s+(u_\w+)\s*\((.*?)\)\s*;",
        re.DOTALL,
    )
    port_re = re.compile(r"\.(\w+)\s*\(\s*([^)]+?)\s*\)")
    for m in block_re.finditer(text):
        mod_name, inst_name, body = m.group(1), m.group(2), m.group(3)
        if mod_name in ("module", "logic", "wire", "reg", "assign", "localparam",
                        "parameter", "input", "output"):
            continue
        short = inst_name.removeprefix("u_")
        for p in port_re.finditer(body):
            port, net = p.group(1), p.group(2).strip()
            # Strip bit selects / slicing / sizing to identify the base net.
            base = re.sub(r"\[[^\]]*\]$", "", net)
            base = base.split(",")[0].strip()
            out[base].append((short, port))
    return out


def pin_face(mod_w: float, mod_h: float, cx: float, cy: float) -> str:
    """Which edge a pin sits on, by distance to that edge."""
    dN = mod_h - cy
    dS = cy
    dW = cx
    dE = mod_w - cx
    return min(("N", dN), ("S", dS), ("W", dW), ("E", dE), key=lambda t: t[1])[0]


COLORS = {
    "cmdproc":       "#ffb3b3",
    "barrier":       "#ffd9b3",
    "reset_seq":     "#fff2b3",
    "load":          "#c2f0c2",
    "smem":          "#b3d9ff",
    "compute_array": "#d9b3ff",
    "store":         "#ffb3e6",
}


def main(argv):
    args = [a for a in argv[1:] if a]
    draw_pins = "--no-pins" not in args
    args = [a for a in args if a != "--no-pins"]
    out = Path(args[0]) if args else REPO / "build/render/chip_top_floorplan.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    fp = yaml.safe_load((REPO / "tech/sky130/chip_top_floorplan.yaml").read_text())
    die_w, die_h = fp["chip"]["die"]
    cx0, cy0, cx1, cy1 = fp["chip"]["core"]

    fig, ax = plt.subplots(figsize=(13, 14))
    ax.set_xlim(-500, die_w + 500)
    ax.set_ylim(-500, die_h + 500)
    ax.set_aspect("equal")

    ax.add_patch(mpatches.Rectangle((0, 0), die_w, die_h,
                                    fill=False, edgecolor="black", linewidth=2))
    ax.add_patch(mpatches.Rectangle((cx0, cy0), cx1 - cx0, cy1 - cy0,
                                    fill=False, edgecolor="gray",
                                    linestyle="--", linewidth=1))

    placements = {}
    pin_xy: dict[tuple[str, str], tuple[float, float]] = {}

    SMALL = 1500
    for mod, spec in fp["modules"].items():
        x, y = spec["location"]
        w, h = module_size(mod)
        placements[mod] = (x, y, w, h)
        ax.add_patch(mpatches.Rectangle((x, y), w, h,
                                        facecolor=COLORS.get(mod, "#dddddd"),
                                        edgecolor="black", linewidth=1))
        label = f"{mod}\n{w:.0f}x{h:.0f}"
        if w < SMALL or h < SMALL:
            ax.annotate(label,
                        xy=(x + w / 2, y + h),
                        xytext=(x + w / 2, y + h + 800),
                        ha="center", va="bottom", fontsize=8,
                        arrowprops=dict(arrowstyle="-", lw=0.5))
        else:
            ax.text(x + w / 2, y + h / 2, label,
                    ha="center", va="center", fontsize=10, weight="bold")

        if draw_pins:
            pins = parse_stub_lef_pins(mod)
            xs, ys = [], []
            for pname, (cx, cy) in pins.items():
                ax_x = x + cx
                ax_y = y + cy
                xs.append(ax_x)
                ys.append(ax_y)
                # Index by (instance, port) — strip [..] bit suffix from pin name
                base = re.sub(r"\[\d+\]$", "", pname)
                pin_xy[(mod, base)] = (ax_x, ax_y)  # last pin of a bus wins; fine for drawing one rep line
            ax.scatter(xs, ys, s=1.5, c="black", alpha=0.6, zorder=3)

    if draw_pins:
        nets = parse_chip_top_connections()
        for net, conns in nets.items():
            # Only draw inter-module nets (>1 distinct instance).
            instances = {c[0] for c in conns}
            if len(instances) < 2:
                continue
            # Strip any bit selects from port names.
            pts = []
            for inst, port in conns:
                port_base = re.sub(r"\[.*\]$", "", port)
                xy = pin_xy.get((inst, port_base))
                if xy is None:
                    # try the bit-0 fallback used by gen_stub_lef for buses
                    xy = pin_xy.get((inst, port_base))
                if xy:
                    pts.append((inst, xy))
            if len(pts) < 2:
                continue
            # Draw a line between each pair of distinct instances for this net.
            seen = set()
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    a_inst, a_xy = pts[i]
                    b_inst, b_xy = pts[j]
                    if a_inst == b_inst:
                        continue
                    key = tuple(sorted([a_inst, b_inst, net]))
                    if key in seen:
                        continue
                    seen.add(key)
                    ax.plot([a_xy[0], b_xy[0]], [a_xy[1], b_xy[1]],
                            color="red", alpha=0.18, lw=0.4, zorder=2)

    ax.set_title(f"chip_top floorplan — die {die_w:.0f} x {die_h:.0f} um "
                 f"({die_w * die_h / 1e6:.1f} mm^2)")
    ax.set_xlabel("x (um)")
    ax.set_ylabel("y (um)")
    ax.grid(True, linestyle=":", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out, dpi=300)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv)
