#!/usr/bin/env python3
"""
Generate chip_top's OpenLane config.yaml (real or stub) from a single
source of truth: tech/sky130/chip_top_floorplan.yaml.

Usage:
    gen_chip_top_config.py {real,stub} <repo_root> > config.[stub.]yaml

Real mode points MACROS at build/lef/<m>.lef + build/gds/<m>.gds +
build/lib/<m>.lib (hardened deliverables).
Stub mode points at build/lef-stub/<m>.lef + build/gds-stub/<m>.gds +
build/lib-stub/<m>.lib.

The output preserves OpenLane's expected structure:
  meta, DESIGN_NAME, VERILOG_FILES, MACROS (one entry per module
  with gds/lef/lib/instances).

This script makes adding a module a single-edit operation: append to
chip_top_floorplan.yaml + instantiate in chip_top.sv. No copy-paste
between two chip_top configs.
"""

import sys
from pathlib import Path

import yaml


HEADER_REAL = """\
meta:
  version: 2
DESIGN_NAME: chip_top
VERILOG_FILES:
- dir::../../build/sv2v/chip_top.v
CLOCK_PORT: clk
CLOCK_PERIOD: 100
PL_TIME_DRIVEN: false
RUN_LINTER: false
SYNTH_STRATEGY: AREA 3
SYNTH_SHARE_RESOURCES: false
SYNTH_ABC_BUFFERING: false
SYNTH_HIERARCHY_MODE: keep
FP_IO_HLAYER: met3
FP_IO_VLAYER: met4
RT_MAX_LAYER: met5
FP_SIZING: absolute
"""


def emit_macros(modules: dict, mode: str) -> list[str]:
    """Build the MACROS section for the given mode (real|stub)."""
    if mode == "real":
        lef_dir = "../../build/sv2v/lef"
        gds_dir = "../../build/sv2v/gds"
        lib_dir = "../../build/sv2v/lib"
    elif mode == "stub":
        lef_dir = "../../build/sv2v/lef-stub"
        gds_dir = "../../build/sv2v/gds-stub"
        lib_dir = "../../build/sv2v/lib-stub"
    else:
        sys.exit(f"unknown mode {mode}")

    lines = ["MACROS:"]
    for mod, spec in modules.items():
        instance = spec["instance"]
        loc = spec["location"]
        orient = spec.get("orientation", "N")
        lines += [
            f"  {mod}:",
            "    gds:",
            f"    - dir::{gds_dir}/{mod}.gds",
            "    lef:",
            f"    - dir::{lef_dir}/{mod}.lef",
            "    lib:",
            "      '*':",
            f"      - dir::{lib_dir}/{mod}.lib",
            "    instances:",
            f"      {instance}:",
            f"        location: [{loc[0]}, {loc[1]}]",
            f"        orientation: {orient}",
        ]
    return lines


def emit_die_area(chip: dict) -> list[str]:
    die = chip["die"]
    core = chip["core"]
    return [
        "DIE_AREA:",
        f"- 0",
        f"- 0",
        f"- {die[0]}",
        f"- {die[1]}",
        "CORE_AREA:",
        f"- {core[0]}",
        f"- {core[1]}",
        f"- {core[2]}",
        f"- {core[3]}",
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("real", "stub"):
        sys.exit(f"usage: {argv[0]} {{real|stub}} <repo_root>")
    mode = argv[1]
    repo = Path(argv[2]).resolve()
    floorplan_path = repo / "tech/sky130/chip_top_floorplan.yaml"
    if not floorplan_path.exists():
        sys.exit(f"ERROR: {floorplan_path} not found")
    fp = yaml.safe_load(floorplan_path.read_text())
    chip = fp.get("chip", {})
    modules = fp.get("modules", {})
    if not chip or not modules:
        sys.exit("ERROR: floorplan must define `chip` and `modules`")

    print(f"# Auto-generated from chip_top_floorplan.yaml (mode={mode}).")
    print(f"# DO NOT EDIT — edit the floorplan YAML and re-run "
          f"`make tech/sky130/config{'.stub' if mode == 'stub' else ''}.yaml`.")
    if mode == "stub":
        # Stub mode: chip-top-only Verilog + blackbox stubs for each submodule.
        print("meta:")
        print("  version: 2")
        print("DESIGN_NAME: chip_top")
        print("VERILOG_FILES:")
        print("- dir::../../build/sv2v/chip_top_only.v")
        for mod in modules.keys():
            print(f"- dir::../../build/sv2v/v-stub/{mod}.v")
        print("CLOCK_PORT: clk")
        print("CLOCK_PERIOD: 100")
        print("PL_TIME_DRIVEN: false")
        print("RUN_LINTER: false")
        print("SYNTH_STRATEGY: AREA 3")
        print("SYNTH_SHARE_RESOURCES: false")
        print("SYNTH_ABC_BUFFERING: false")
        print("SYNTH_HIERARCHY_MODE: keep")
        print("FP_IO_HLAYER: met3")
        print("FP_IO_VLAYER: met4")
        print("RT_MAX_LAYER: met5")
        print("FP_SIZING: absolute")
    else:
        sys.stdout.write(HEADER_REAL)
    print("\n".join(emit_die_area(chip)))
    print("\n".join(emit_macros(modules, mode)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
