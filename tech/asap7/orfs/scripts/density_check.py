"""Density measurement for asap7 hardened modules.

Run inside the openroad/orfs:latest container via KLayout's batch mode:
    klayout -b -r density_check.py \
        -rd gds=<path>            \
        -rd top=<cell_name>       \
        -rd report=<path>         \
        -rd bands=<path>          # optional: TSV with layer/min%/max% rows

Reports global metal-density per routing layer (M1..M9) against documented
bands. Global density = (sum of merged polygon area on layer) /
(top-cell bounding-box area). Real-foundry sign-off uses windowed density
in 20-50 um windows; this script computes the global number as an
early-warning signal -- if global density is already out of band, local
windows will be worse, so re-floorplan now rather than at PDK swap.

Exit codes:
    0 -- all layers within bands (or untouched: zero density)
    1 -- at least one layer exceeds max density (real signal: too dense)
    2 -- required arg missing / bad inputs
    3 -- at least one layer below min density (typically need fill cells
        post-route; not a tape-out blocker on its own but flagged so the
        fill methodology gets exercised at the right time)

Min-density violations are exit-code-distinguished from max-density
violations because they have different remediation paths: min violations
are fixed by adding dummy-metal fill (a post-route step we haven't built
yet -- see tech/asap7/PDK_GAPS.md); max violations are fixed by re-
floorplanning or reducing routing density (architectural rework).
"""
from __future__ import annotations

import os
import sys

import pya  # provided by KLayout


# asap7 GDS layer/datatype map. Same numbers used by lvs.py.
METAL_LAYERS = [
    ("M1", 19, 0),
    ("M2", 20, 0),
    ("M3", 30, 0),
    ("M4", 40, 0),
    ("M5", 50, 0),
    ("M6", 60, 0),
    ("M7", 70, 0),
    ("M8", 80, 0),
    ("M9", 90, 0),
]

# Default density bands. See PDK_GAPS.md "Metal density / fill sign-off"
# for the derivation: ASAP7 publishes only M5 (15/90) and Pad (20/80)
# rules; for the other layers we use 20/70 (thin/mid) and 20/80 (thick)
# as conservative early-warning targets representative of public 7nm-class
# CMP rules (sky130, IHP130, public-source TSMC N7 bands).
DEFAULT_BANDS = {
    "M1": (20.0, 70.0),
    "M2": (20.0, 70.0),
    "M3": (20.0, 70.0),
    "M4": (20.0, 70.0),
    "M5": (15.0, 90.0),  # ASAP7-published
    "M6": (20.0, 70.0),
    "M7": (20.0, 70.0),
    "M8": (20.0, 80.0),
    "M9": (20.0, 80.0),
}


def _args_from_globals():
    g = globals()
    for key in ("gds", "top", "report"):
        if key not in g:
            sys.stderr.write(
                f"missing -rd {key}=... -- see tech/asap7/orfs/density_check.sh\n"
            )
            sys.exit(2)
    return g["gds"], g["top"], g["report"], g.get("bands")


def parse_bands(path: str) -> dict:
    """Load layer/min%/max% from a 3-column TSV (default bands if path empty)."""
    if not path:
        return DEFAULT_BANDS
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 3:
                continue
            layer, lo, hi = parts
            out[layer] = (float(lo), float(hi))
    return out


def measure(gds_path: str, top_name: str):
    """Yield (layer_name, density_pct, die_area_um2, layer_area_um2)."""
    ly = pya.Layout()
    ly.read(gds_path)
    top = ly.cell(top_name)
    if top is None:
        top = ly.top_cell()
        sys.stderr.write(
            f"density_check: '{top_name}' not in GDS; using actual top '{top.name}'\n"
        )
    bbox = top.dbbox()  # die bbox in um
    die_area_um2 = bbox.area()
    if die_area_um2 <= 0:
        raise RuntimeError(f"top cell {top.name} has zero bounding-box area")

    for layer_name, lid, dt in METAL_LAYERS:
        li = ly.layer(lid, dt)
        region = pya.Region(top.begin_shapes_rec(li))
        region.merge()
        layer_area_dbu = region.area()
        layer_area_um2 = layer_area_dbu * ly.dbu * ly.dbu
        density_pct = (layer_area_um2 / die_area_um2) * 100.0
        yield layer_name, density_pct, die_area_um2, layer_area_um2


def classify(layer: str, density_pct: float, bands: dict) -> str:
    lo, hi = bands.get(layer, (0.0, 100.0))
    if density_pct < lo:
        return f"UNDER (min {lo}%)"
    if density_pct > hi:
        return f"OVER (max {hi}%)"
    return "ok"


def main() -> int:
    gds, top, report, bands_path = _args_from_globals()
    bands = parse_bands(bands_path or "")

    lines = []
    over_count = 0
    under_count = 0
    has_data = False

    rows = list(measure(gds, top))
    die_area_um2 = rows[0][2] if rows else 0.0
    lines.append("=" * 78)
    lines.append("Metal density report")
    lines.append("=" * 78)
    lines.append(f"GDS         : {gds}")
    lines.append(f"Top cell    : {top}")
    lines.append(f"Die area    : {die_area_um2:.1f} um^2 ({die_area_um2/1e6:.4f} mm^2)")
    lines.append(f"Bands source: {'default (PDK_GAPS.md)' if not bands_path else bands_path}")
    lines.append("")
    lines.append(f"{'layer':<6} {'density':>12} {'band':>14} {'status':<22} {'area (um^2)':>14}")
    lines.append("-" * 78)

    for layer, dens, _, area in rows:
        lo, hi = bands.get(layer, (0.0, 100.0))
        status = classify(layer, dens, bands)
        lines.append(
            f"{layer:<6} {dens:>11.2f}% {lo:>5.1f}-{hi:.1f}% "
            f"{status:<22} {area:>14.1f}"
        )
        if "OVER" in status:
            over_count += 1
        elif "UNDER" in status and dens > 0:
            under_count += 1
        if dens > 0:
            has_data = True

    lines.append("")
    if over_count:
        lines.append(
            f"OVER MAX  : {over_count} layer(s) -- re-floorplan or thin metal use."
        )
    if under_count:
        lines.append(
            f"UNDER MIN : {under_count} layer(s) -- needs dummy-metal fill at "
            f"tape-out (no fill methodology shipped yet -- see PDK_GAPS.md)."
        )
    if not (over_count or under_count) and has_data:
        lines.append("WITHIN BANDS: every populated layer is inside its target.")

    lines.append("")
    lines.append(
        f"SUMMARY: top={top} die_um2={die_area_um2:.0f} "
        f"over={over_count} under={under_count}"
    )
    report_text = "\n".join(lines) + "\n"

    os.makedirs(os.path.dirname(report) or ".", exist_ok=True)
    with open(report, "w") as fh:
        fh.write(report_text)
    sys.stdout.write(report_text)

    if over_count:
        return 1
    if under_count:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
