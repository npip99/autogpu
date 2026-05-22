#!/usr/bin/env python3
"""
Color whole buffer-chain wire routes: for each "logical" net (e.g.
ca_drain_row_data[N]), trace through buffer cells in the post-CTS
netlist to collect every sub-net name, then highlight all those guide
rectangles in one color on the layout.

Usage:
    render_wires_chain.py <netlist.v> <after_grt.guide> [out.png]
        [net1 net2 ...]
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import yaml


REPO = Path(__file__).resolve().parents[3]

# Sky130 buffer/inverter cell name patterns. These are pass-through
# 1-input/1-output cells we want to walk past.
BUF_RE = re.compile(r"sky130_(?:fd|ef)_sc_hd__(?:buf|clkbuf|clkinv|inv)_\d+")

# Pattern: <cell> <inst_name> (.A(<net>), ... .X(<net>) ...) ;
# Buffers in this netlist look like:
#   sky130_fd_sc_hd__buf_12 wire5926 (.A(\ca_drain_row_data[0] ),
#       .X(net5926));
INST_RE = re.compile(
    r"\s*(sky130_(?:fd|ef)_sc_hd__\S+?)\s+(\S+)\s*\("
    r"(?:[^)]*)\)\s*;",
    re.DOTALL,
)
PIN_RE = re.compile(r"\.([A-Z]+)\(([^)]+?)\)")


def parse_buffer_chains(netlist_path):
    """Return (fwd, bwd) maps for 1-in/1-out buffer/inverter cells.
    Walks the netlist line-by-line, accumulating each instance block
    (cell_name inst_name ( ... );) before parsing pins."""
    fwd = defaultdict(list)
    bwd = defaultdict(list)
    inst_start = re.compile(r"^\s*(sky130_(?:fd|ef)_sc_hd__\S+?)\s+(\S+)\s*\(")
    buf_re = BUF_RE
    pin_re = re.compile(r"\.([A-Z]+)\(\s*([^)]+?)\s*\)")

    with open(netlist_path) as f:
        block = []
        in_block = False
        cell = None
        for line in f:
            if not in_block:
                m = inst_start.match(line)
                if m:
                    cell = m.group(1)
                    block = [line]
                    in_block = True
                    if ";" in line and ")" in line:
                        # one-liner
                        _process_block(block, cell, buf_re, pin_re, fwd, bwd)
                        in_block = False
                continue
            block.append(line)
            if ";" in line:
                _process_block(block, cell, buf_re, pin_re, fwd, bwd)
                in_block = False
    return fwd, bwd


def _process_block(block, cell, buf_re, pin_re, fwd, bwd):
    if not buf_re.match(cell):
        return
    body = "".join(block)
    pins = dict(pin_re.findall(body))
    a = pins.get("A")
    x = pins.get("X") or pins.get("Y")
    if not a or not x:
        return
    a = a.strip().rstrip().strip("\\").strip()
    x = x.strip().rstrip().strip("\\").strip()
    fwd[a].append(x)
    bwd[x].append(a)


def trace_chain(start, fwd, max_depth=20):
    """BFS forward from start net through buffers. Returns set of nets."""
    seen = {start}
    queue = [start]
    while queue:
        net = queue.pop(0)
        for out in fwd.get(net, []):
            if out not in seen and len(seen) < 1000:
                seen.add(out)
                queue.append(out)
    return seen


def parse_guide_all(path):
    """Return {net: [(xl, yl, xh, yh, layer), ...]} for ALL guide nets."""
    out = defaultdict(list)
    in_net = False
    current = None
    for raw in open(path):
        line = raw.strip()
        if not line: continue
        if line == "(":
            in_net = True; continue
        if line == ")":
            in_net = False; current = None; continue
        if not in_net:
            current = line.replace("\\[", "[").replace("\\]", "]")
            continue
        if current is None: continue
        parts = line.split()
        if len(parts) != 5: continue
        try:
            xl, yl, xh, yh = (int(p) for p in parts[:4])
        except ValueError:
            continue
        out[current].append((xl, yl, xh, yh, parts[4]))
    return out


def parse_guide(path, wanted_set):
    out = defaultdict(list)
    current = None
    in_net = False
    for raw in open(path):
        line = raw.strip()
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
            normalized = line.replace("\\[", "[").replace("\\]", "]")
            current = normalized if normalized in wanted_set else None
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
            # Fallback: latest hardened LEF
            runs_dir = REPO / f"tech/sky130/submodules/{mod}/runs"
            if runs_dir.exists():
                for run in sorted(runs_dir.glob("RUN_*"), reverse=True):
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


DEFAULT_NETS = [
    "ca_drain_row_data[0]",
    "ca_drain_row_data[128]",
    "ca_drain_row_data[256]",
    "ca_drain_row_data[384]",
    "ca_drain_row_data[512]",
    "ca_drain_row_data[640]",
    "ca_drain_row_data[768]",
    "ca_drain_row_data[896]",
    "ca_drain_row_data[1023]",
]

BUS_PREFIXES = [
    "ca_drain_row_data[",
    "s_rd_a_data[",
    "s_rd_b_data[",
    "l_smem_wr_data[",
    "ca_drain_row_idx[",
    "l_add_tx_bytes[",
    "m_arrive_bar_id[",
]


def list_all_starts(netlist_path):
    """Find all named buses in the netlist matching BUS_PREFIXES."""
    found = set()
    pat = re.compile(r"\\?([\w]+\[\d+\])")
    for line in open(netlist_path):
        if "wire " not in line:
            continue
        m = pat.search(line)
        if not m:
            continue
        name = m.group(1)
        for pref in BUS_PREFIXES:
            if name.startswith(pref):
                found.add(name)
                break
    return sorted(found)


def list_starts_in_region(netlist_path, guide_path, region_um):
    """Pick base nets whose buffer-chain segments intersect the µm region
    (x0, y0, x1, y1). A 'base net' = either a named bus (no leading _) or
    an _NNN_ net that has no upstream buffer (the source of a chain)."""
    fwd, bwd = parse_buffer_chains(netlist_path)
    # Find nets that have guide segments in the region.
    rx0, ry0, rx1, ry1 = (v * 1000 for v in region_um)
    net_hits: set = set()
    in_net = False
    current = None
    for line in open(guide_path):
        line = line.strip()
        if not line: continue
        if line == "(":
            in_net = True; continue
        if line == ")":
            in_net = False; current = None; continue
        if not in_net:
            current = line.replace("\\[", "[").replace("\\]", "]")
            continue
        if current is None: continue
        parts = line.split()
        if len(parts) != 5: continue
        try:
            xl, yl, xh, yh = (int(p) for p in parts[:4])
        except ValueError:
            continue
        if xl < rx1 and xh > rx0 and yl < ry1 and yh > ry0:
            net_hits.add(current)
    # Walk backward through buffer chains to find each hit's base.
    bases = set()
    for n in net_hits:
        cur = n
        seen = {cur}
        # Climb backward via buffers
        while True:
            parents = bwd.get(cur, [])
            if not parents:
                break
            p = parents[0]
            if p in seen:
                break
            seen.add(p)
            cur = p
        bases.add(cur)
    return sorted(bases)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    netlist_path = argv[1]
    guide_path = argv[2]
    out_path = Path(argv[3]) if len(argv) > 3 else REPO / "build/render/wires.png"
    # If --random N seed, pick N random nets from all named buses.
    extras = argv[4:]
    starts = None
    show_all_gray = False
    if extras and extras[0] == "--all":
        # Show ALL guide nets in gray semi-transparent + optional N colored.
        show_all_gray = True
        extras = extras[1:]
    if extras and extras[0] == "--random":
        import random as _r
        n = int(extras[1]) if len(extras) > 1 else 20
        seed = int(extras[2]) if len(extras) > 2 else 1
        all_nets = list_all_starts(netlist_path)
        print(f"found {len(all_nets)} named bus nets; picking {n} (seed {seed})")
        starts = _r.Random(seed).sample(all_nets, min(n, len(all_nets)))
    elif extras and extras[0] == "--region":
        # --region x0 y0 x1 y1 N seed
        import random as _r
        region = tuple(float(v) for v in extras[1:5])
        n = int(extras[5]) if len(extras) > 5 else 30
        seed = int(extras[6]) if len(extras) > 6 else 1
        candidates = list_starts_in_region(netlist_path, guide_path, region)
        print(f"region {region}: {len(candidates)} nets touch it; picking {n}")
        starts = _r.Random(seed).sample(candidates, min(n, len(candidates)))
    elif extras:
        starts = extras
    else:
        starts = DEFAULT_NETS
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("parsing netlist for buffer chains…")
    fwd, bwd = parse_buffer_chains(netlist_path)
    print(f"  {sum(len(v) for v in fwd.values())} buffer edges")

    # Trace each logical net through its buffer chain.
    chains = {}
    all_nets = set()
    for net in starts:
        chain = trace_chain(net, fwd)
        chains[net] = chain
        all_nets.update(chain)
        print(f"  {net}: chain has {len(chain)} sub-nets")

    print(f"\nparsing guide for {len(all_nets)} net names…")
    guide_rects = parse_guide(guide_path, all_nets)
    print(f"  matched {len(guide_rects)} nets in guide")

    # If --all, also parse every guide net (not just the highlighted ones).
    every_guide = None
    if show_all_gray:
        print("parsing FULL guide (all nets) for background layer…")
        every_guide = parse_guide_all(guide_path)
        print(f"  total nets: {len(every_guide)}")

    die_w_um, die_h_um, modules = load_floorplan()

    # Bump figure size + DPI for the all-gray view so thin lines are visible.
    fig_size = 24 if show_all_gray else 18
    dpi = 400 if show_all_gray else 200
    fig, ax = plt.subplots(figsize=(fig_size, fig_size))
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
    MIN_BOX = 1500   # µm — bump tiny macros so they're visible
    for mod, x, y, w, h in modules:
        ax.add_patch(mpatches.Rectangle((x, y), w, h,
                                        facecolor=mod_colors.get(mod, "#dddddd"),
                                        edgecolor="black", linewidth=1, alpha=0.30))
        dw, dh = max(w, MIN_BOX), max(h, MIN_BOX)
        if dw > w or dh > h:
            dx = x + w/2 - dw/2
            dy = y + h/2 - dh/2
            ax.add_patch(mpatches.Rectangle((dx, dy), dw, dh,
                                            facecolor="none",
                                            edgecolor="black", linewidth=1.5,
                                            linestyle="--", alpha=0.6))
            ax.text(dx + dw/2, dy + dh + 200,
                    f"{mod} ({w:.0f}x{h:.0f})",
                    ha="center", va="bottom", fontsize=10, weight="bold")
        else:
            ax.text(x + w/2, y + h/2, mod,
                    ha="center", va="center", fontsize=12, weight="bold",
                    color="black", alpha=0.7)

    # --all: paint every guide segment in gray first (background).
    if every_guide is not None:
        H_LAYERS = {"met1", "met3", "met5", "li1"}
        for net, rects in every_guide.items():
            for xl_nm, yl_nm, xh_nm, yh_nm, layer in rects:
                xl, yl, xh, yh = xl_nm/1000, yl_nm/1000, xh_nm/1000, yh_nm/1000
                cx, cy = (xl + xh) / 2, (yl + yh) / 2
                if layer in H_LAYERS:
                    ax.plot([xl, xh], [cy, cy], color="gray", lw=0.18, alpha=0.18,
                            solid_capstyle="butt")
                else:
                    ax.plot([cx, cx], [yl, yh], color="gray", lw=0.18, alpha=0.18,
                            solid_capstyle="butt")

    cmap = plt.get_cmap("tab20")
    for i, net in enumerate(starts):
        color = cmap(i % 20)
        chain = chains[net]
        # Build a single polyline by connecting the centerlines of each
        # guide segment. A guide rect on an H layer (met1/met3/met5)
        # represents a horizontal wire from (xl, cy) to (xh, cy); a V
        # layer rect represents a vertical wire from (cx, yl) to
        # (cx, yh). Where two adjacent segments overlap we draw a small
        # dot at the via (their shared corner).
        H_LAYERS = {"met1", "met3", "met5", "li1"}
        endpoints = []   # list of (x, y, layer) at the ends of each segment
        n_rects = 0
        for sub in chain:
            for xl_nm, yl_nm, xh_nm, yh_nm, layer in guide_rects.get(sub, []):
                xl, yl, xh, yh = xl_nm/1000, yl_nm/1000, xh_nm/1000, yh_nm/1000
                cx, cy = (xl + xh) / 2, (yl + yh) / 2
                if layer in H_LAYERS:
                    p0 = (xl, cy); p1 = (xh, cy)
                else:
                    p0 = (cx, yl); p1 = (cx, yh)
                ax.plot([p0[0], p1[0]], [p0[1], p1[1]],
                        color=color, lw=1.5, alpha=0.85, solid_capstyle="round")
                endpoints.append((p0, layer))
                endpoints.append((p1, layer))
                n_rects += 1
        # Mark vias at any point where multiple segments meet on different layers.
        from collections import Counter
        pt_layers: dict = {}
        for pt, lyr in endpoints:
            key = (round(pt[0], 1), round(pt[1], 1))
            pt_layers.setdefault(key, set()).add(lyr)
        for (px, py), layers in pt_layers.items():
            if len(layers) > 1:
                ax.plot([px], [py], "o", color=color, ms=2.5, alpha=0.9)
        ax.plot([], [], color=color, label=f"{net} ({len(chain)} nets, {n_rects} segs)",
                linewidth=4)

    ax.legend(loc="upper right", fontsize=9)
    ax.set_title("buffer-chain colored wires (one color per logical net, all hops included)")
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")
    ax.grid(True, linestyle=":", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
