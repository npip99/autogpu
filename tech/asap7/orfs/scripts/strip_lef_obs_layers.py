#!/usr/bin/env python3
"""Strip specific LAYER sections from the OBS block of an abstract LEF.

ORFS's write_abstract_lef -bloat_occupied_layers tags every routing layer
the macro internally used as one big obstruction rectangle. Parent designs
need leaves' lower layers (M1..M5) obstructed so GRT picks valid pin access
paths, but need the upper layers (M6..M7) clear so PDN can run stripes
over the macro. This script removes the layer blocks named on the command
line from the OBS section, in-place.

Usage:
    strip_lef_obs_layers.py <lef> M6 M7

LEF OBS block grammar:
    OBS
      LAYER <name> ;
        RECT ...
        RECT ...
        ...
      LAYER <name> ;
        RECT ...
    END
"""
import re
import sys
from pathlib import Path


def strip(lef_path: Path, layers_to_strip: set[str]) -> None:
    text = lef_path.read_text()

    # Find the OBS block and rewrite it.
    obs_pat = re.compile(r"^\s*OBS\b.*?^\s*END\b", re.MULTILINE | re.DOTALL)
    m = obs_pat.search(text)
    if not m:
        print(f"  no OBS block — nothing to strip", file=sys.stderr)
        return

    block = m.group(0)
    lines = block.splitlines()

    out: list[str] = []
    skipping = False
    stripped_layers: list[str] = []
    for line in lines:
        layer_m = re.match(r"\s*LAYER\s+(\S+)\s*;\s*$", line)
        if layer_m:
            name = layer_m.group(1)
            if name in layers_to_strip:
                skipping = True
                stripped_layers.append(name)
                continue
            else:
                skipping = False
                out.append(line)
                continue
        # End of block — flush the END line and stop skipping.
        if re.match(r"\s*END\b", line):
            out.append(line)
            continue
        if not skipping:
            out.append(line)

    new_block = "\n".join(out)
    new_text = text[: m.start()] + new_block + text[m.end():]
    lef_path.write_text(new_text)
    if stripped_layers:
        print(f"  stripped OBS for: {', '.join(stripped_layers)}")
    else:
        print(f"  no matching layers found in OBS")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    lef = Path(sys.argv[1])
    layers = set(sys.argv[2:])
    strip(lef, layers)
