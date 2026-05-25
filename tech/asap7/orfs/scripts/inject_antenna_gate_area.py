#!/usr/bin/env python3
"""inject_antenna_gate_area.py — add ANTENNAGATEAREA to every INPUT pin of
every MACRO in the asap7 stdcell LEF.

                       ============= WARNING =============
The gate-area number injected here is NOT foundry-derived. It is a
single conservative per-pin estimate computed from public asap7-paper
geometry (Clark, L.T. et al., "ASAP7: A 7-nm finFET predictive process
design kit", Microelectronics Journal vol. 53, 2016, pp. 105-115). It
exists so that OpenROAD's `check_antennas` has a non-zero denominator
when computing PAR/CAR ratios — without ANTENNAGATEAREA on at least one
input pin per net, every check returns "no violations" vacuously. Real
foundry sign-off requires a per-cell, per-pin gate area extracted from
the actual GDS / SPICE deck. This script does NOT do that.

DERIVATION
----------
asap7 stdcells use a 7nm finFET gate length L_g = 21 nm = 0.021 um
(Clark et al. Table 1). The 7p5t library has finFETs with fin pitch
27 nm and per-fin width that contributes to "effective" gate width.
For digital-grade input pins the smallest stdcell footprint (a single-
fin INV) has roughly:
    W_eff per fin ~= 2*h_fin + t_fin ~= 2*32nm + 6.5nm ~= 70 nm
    L_g           ~= 21 nm
    A_gate (1 fin) ~= W_eff * L_g ~= 70e-3 * 21e-3 um^2
                     ~= 0.00147 um^2

Most asap7 stdcell input pins drive 2..4 fins of NMOS+PMOS combined.
We pick a single CONSERVATIVE estimate of:
    A_gate = 0.005 um^2 per input pin
which is ~3.4x the 1-fin minimum — covers small-x cells without being
so loose that large-x cells (which have proportionally more fins) get
under-reported. This is at the SAME order of magnitude as published
7nm-class antenna gate areas and is comparable to sky130's
~0.06 um^2 / 13.5 (linear scale ratio of node feature sizes).

Real per-cell gate areas would range from ~0.003 um^2 (xp33 cells) up
to ~0.06 um^2 (x16 buffers). A single value over-protects small cells
and under-protects large ones; we err toward over-protection (smaller
A_gate => tighter ratio => more violations flagged) on the assumption
that "investigate" is the right default posture for predictive sign-off.

USAGE
-----
    inject_antenna_gate_area.py <input.lef> <output.lef>

Behavior:
- Deterministic: same input → identical bytewise output.
- Idempotent: a pin that already has ANTENNAGATEAREA is left untouched.
- Touches ONLY input-pin blocks (DIRECTION INPUT). VDD/VSS/output pins
  are not modified.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Per-pin gate area in um^2. See module docstring for derivation.
GATE_AREA_UM2 = 0.005

# Match a "PIN <name>" line, capturing leading indentation so we can
# emit the ANTENNAGATEAREA line at the same indentation as the body.
PIN_RE = re.compile(r"^(?P<indent>\s*)PIN\s+(?P<name>\S+)\s*$")
END_PIN_RE = re.compile(r"^\s*END\s+(?P<name>\S+)\s*$")
DIRECTION_INPUT_RE = re.compile(r"^\s*DIRECTION\s+INPUT\s*;\s*$")
ANTENNA_GATEAREA_RE = re.compile(r"^\s*ANTENNAGATEAREA\b")
# PORT block delimiters — we want to insert before the first PORT (the
# LEF spec requires ANTENNAGATEAREA between the pin header lines and
# the PORT block).
PORT_RE = re.compile(r"^(?P<indent>\s*)PORT\s*$")


def patch_lef(src: Path, dst: Path) -> tuple[int, int]:
    """Patch `src` → `dst`. Return (pins_patched, pins_already_had).

    Algorithm: stream the file line by line. Buffer each PIN block until
    we see END <name>. While buffering, record whether DIRECTION INPUT
    appeared and whether ANTENNAGATEAREA was already present. On END,
    emit the block — for INPUT pins without an existing gate-area line,
    inject one immediately before the first PORT (or, failing that,
    immediately before END if there's no PORT).
    """
    lines_in = src.read_text().splitlines(keepends=True)
    out: list[str] = []

    in_pin = False
    pin_buf: list[str] = []
    pin_name = ""
    pin_indent = ""
    pin_is_input = False
    pin_has_gatearea = False

    patched = 0
    already = 0

    for line in lines_in:
        if not in_pin:
            m = PIN_RE.match(line)
            if m:
                in_pin = True
                pin_buf = [line]
                pin_name = m.group("name")
                pin_indent = m.group("indent")
                pin_is_input = False
                pin_has_gatearea = False
                continue
            out.append(line)
            continue

        # Inside a PIN block.
        pin_buf.append(line)
        if DIRECTION_INPUT_RE.match(line):
            pin_is_input = True
        elif ANTENNA_GATEAREA_RE.match(line):
            pin_has_gatearea = True

        end_m = END_PIN_RE.match(line)
        if end_m and end_m.group("name") == pin_name:
            # Time to flush the buffered block.
            if pin_is_input and not pin_has_gatearea:
                # Inject ANTENNAGATEAREA just before the first PORT
                # inside this pin block (or before END if no PORT).
                injected = False
                new_buf: list[str] = []
                # Indentation: match the indent of the first non-PIN
                # line inside the block, if any; else pin_indent + 2sp.
                body_indent = pin_indent + "  "
                for inner in pin_buf:
                    pm = PORT_RE.match(inner)
                    if pm and not injected:
                        body_indent = pm.group("indent")
                        new_buf.append(
                            f"{body_indent}ANTENNAGATEAREA {GATE_AREA_UM2} ;\n"
                        )
                        injected = True
                    if not injected and END_PIN_RE.match(inner):
                        # No PORT was found; inject right before END.
                        new_buf.append(
                            f"{body_indent}ANTENNAGATEAREA {GATE_AREA_UM2} ;\n"
                        )
                        injected = True
                    new_buf.append(inner)
                out.extend(new_buf)
                if injected:
                    patched += 1
                else:
                    # Should never happen — every PIN block has END.
                    raise RuntimeError(f"failed to inject into PIN {pin_name}")
            else:
                out.extend(pin_buf)
                if pin_is_input and pin_has_gatearea:
                    already += 1
            in_pin = False
            pin_buf = []
            pin_name = ""

    if in_pin:
        raise RuntimeError(f"unterminated PIN block: {pin_name}")

    # Prepend a banner so anybody reading the patched LEF knows the
    # source. Idempotency: only add the banner if the input doesn't
    # already start with one (i.e. we're not patching a previously
    # patched file). The original platform LEF starts with "# BSD".
    body = "".join(out)
    if body.startswith("# === PATCHED by inject_antenna_gate_area.py ==="):
        new_text = body
    else:
        banner = (
            "# === PATCHED by inject_antenna_gate_area.py ===\n"
            f"# Source: {src.name}\n"
            f"# Injected ANTENNAGATEAREA {GATE_AREA_UM2} um^2 on every\n"
            "# DIRECTION INPUT pin that did not already have one. This is\n"
            "# a PREDICTIVE value — see script docstring for derivation.\n"
            "# Do NOT use for foundry sign-off.\n"
            "# =============================================\n"
        )
        new_text = banner + body
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(new_text)
    return patched, already


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(argv[1])
    dst = Path(argv[2])
    if not src.is_file():
        print(f"ERROR: source LEF {src} not found", file=sys.stderr)
        return 1
    patched, already = patch_lef(src, dst)
    print(
        f"inject_antenna_gate_area: {src.name} -> {dst}: "
        f"injected {patched} pin(s); {already} already had ANTENNAGATEAREA"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
