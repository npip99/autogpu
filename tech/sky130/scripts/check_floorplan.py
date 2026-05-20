#!/usr/bin/env python3
"""
Fast floorplan sanity check — for each wide bus between two modules,
verify the two faces it lives on actually face each other and are
geometrically close enough to route.

Use this to iterate on chip_top_floorplan.yaml in seconds instead of
running stub-mode chip_top harden (which takes hours).

Usage:
    check_floorplan.py [repo_root]

Reports per-bus:
    - source module + edge
    - destination module + edge
    - geometric distance between those edges
    - verdict: OK / WARN / FAIL

A bus FAILs if the two faces don't face each other (e.g. both on south,
or perpendicular without overlap). WARNs if the faces face each other
but distance > 2000 um (long route inevitable).
"""

import re
import sys
from pathlib import Path

import yaml


# Threshold widths
WIDE_BUS_THRESHOLD = 30  # bits; only check pipes >= this width
LONG_DIST_WARN     = 2000  # um


def read_floorplan(repo: Path):
    fp = yaml.safe_load((repo / "tech/sky130/chip_top_floorplan.yaml").read_text())
    placements = {}
    for mod, spec in fp["modules"].items():
        x, y = spec["location"]
        # Get size from submodule config or hardened-LEF fallback
        cfg = yaml.safe_load((repo / f"tech/sky130/submodules/{mod}/config.yaml").read_text())
        die = cfg.get("DIE_AREA")
        if die and len(die) == 4:
            w, h = die[2] - die[0], die[3] - die[1]
        elif cfg.get("STUB_SIZE"):
            w, h = cfg["STUB_SIZE"]
        else:
            # Look at the latest hardened LEF
            runs_dir = repo / f"tech/sky130/submodules/{mod}/runs"
            w, h = None, None
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
            if w is None:
                w, h = 0, 0  # unknown — will skip distance checks
        placements[mod] = {
            "instance": spec["instance"],
            "x0": x, "y0": y,
            "x1": x + w, "y1": y + h,
            "w": w, "h": h,
        }
    return placements


def read_pin_order(repo: Path, module: str) -> dict[str, str]:
    """Return {pin_pattern_or_name: edge_letter}."""
    path = repo / f"tech/sky130/submodules/{module}/{module}.pin_order.cfg"
    if not path.exists():
        return {}
    out = {}
    current_edge = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and line[1:].strip() in ("N", "S", "E", "W"):
            current_edge = line[1:].strip()
            continue
        if line.startswith("$") or line.startswith("#"):
            continue
        line = line.split("//")[0].split("#")[0].strip()
        if not line or current_edge is None:
            continue
        out[line] = current_edge
    return out


# Hand-curated pipe inventory: for each fat/medium bus, name the modules
# and the canonical "base name" that appears in both modules' pin_order
# patterns. Width sourced from the connection matrix earlier.
PIPES = [
    # (mod_a, port_pattern_a, mod_b, port_pattern_b, width, label)
    ("compute_array", "drain_row_data", "store",         "drain_row_data", 1024, "drain bus"),
    ("compute_array", "rd_a_data",      "smem",          "rd_a_data",      256,  "operand A read"),
    ("compute_array", "rd_b_data",      "smem",          "rd_b_data",      256,  "operand B read"),
    ("load",          "smem_wr_data",   "smem",          "wr_data",        128,  "SMEM write bus"),
    ("load",          "add_tx_bytes",   "barrier",       "add_tx_bytes",   32,   "barrier add_tx"),
    ("cmdproc",       "load_gmem_ptr",  "load",          "gmem_ptr",       32,   "cmdproc → load"),
    ("cmdproc",       "mma_a_smem_offset", "compute_array", "issue_a_off",  32,   "cmdproc → mma"),
    ("cmdproc",       "store_gmem_ptr", "store",         "gmem_ptr",       32,   "cmdproc → store"),
    ("cmdproc",       "init_bar_id",    "barrier",       "init_bar_id",    32,   "cmdproc → barrier"),
    ("compute_array", "arrive_bar_id",  "barrier",       "arrive_bar_id_b", 32,   "mma arrive"),
]


EDGE_OPP = {"N": "S", "S": "N", "E": "W", "W": "E"}


def edge_of(pin_order: dict, target: str) -> str | None:
    """Find which edge a pin pattern lives on. Match by prefix / wildcard."""
    for pat, edge in pin_order.items():
        # Normalize: strip trailing wildcards
        base = pat.rstrip("*").rstrip(".").rstrip("[").rstrip("\\")
        if target.startswith(base) or pat.startswith(target):
            return edge
    return None


def edge_face_distance(a: dict, edge_a: str, b: dict, edge_b: str) -> tuple[float, str]:
    """Return (distance_um, status_string) for the gap between A's edge_a
    face and B's edge_b face.

    status: 'OK' if faces face each other and overlap; 'WARN' if face
    but no overlap; 'FAIL' if faces don't face each other.
    """
    if edge_a is None or edge_b is None:
        return (0, "FAIL: pin not found in pin_order")
    # For a clean abutment, edge_a and edge_b must be opposites (N↔S, E↔W).
    if EDGE_OPP[edge_a] != edge_b:
        return (0, f"FAIL: edges don't face ({edge_a} vs {edge_b})")
    # Compute gap between the two faces.
    if edge_a == "N":
        gap = b["y0"] - a["y1"]
        overlap = max(0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    elif edge_a == "S":
        gap = a["y0"] - b["y1"]
        overlap = max(0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    elif edge_a == "E":
        gap = b["x0"] - a["x1"]
        overlap = max(0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    else:  # W
        gap = a["x0"] - b["x1"]
        overlap = max(0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    if gap < 0:
        return (gap, f"FAIL: faces overlap geometrically (gap={gap:.0f} um)")
    if overlap <= 0:
        return (gap, f"WARN: faces face each other but no edge overlap (route through margin)")
    if gap > LONG_DIST_WARN:
        return (gap, f"WARN: large gap {gap:.0f} um")
    return (gap, f"OK: gap={gap:.0f} um, overlap={overlap:.0f} um")


def main(argv):
    repo = Path(argv[1] if len(argv) > 1 else ".").resolve()
    placements = read_floorplan(repo)
    pin_orders = {mod: read_pin_order(repo, mod) for mod in placements}

    print(f"{'PIPE':<35} {'WIDTH':>5}  {'A':<14}{'B':<14}  STATUS")
    print("-" * 110)

    ok, warn, fail = 0, 0, 0
    for mod_a, port_a, mod_b, port_b, width, label in PIPES:
        if width < WIDE_BUS_THRESHOLD:
            continue
        if mod_a not in placements or mod_b not in placements:
            continue
        edge_a = edge_of(pin_orders[mod_a], port_a)
        edge_b = edge_of(pin_orders[mod_b], port_b)
        dist, status = edge_face_distance(placements[mod_a], edge_a,
                                          placements[mod_b], edge_b)
        a_label = f"{mod_a}.{edge_a or '?'}"
        b_label = f"{mod_b}.{edge_b or '?'}"
        print(f"{label:<35} {width:>5}  {a_label:<14}{b_label:<14}  {status}")
        if "OK" in status:
            ok += 1
        elif "WARN" in status:
            warn += 1
        else:
            fail += 1

    print("-" * 110)
    print(f"summary: {ok} OK / {warn} WARN / {fail} FAIL")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
