#!/usr/bin/env python3
"""
Generate a Verilog stub for a module: same `module ... endmodule`
declaration as the real SV (parameters, ports, widths, defaults all
preserved) but with the body replaced by nothing and a `(* blackbox *)`
attribute attached.

Usage:
    gen_stub_verilog.py <module_name> <repo_root>

Writes <repo_root>/build/sv2v/v-stub/<module>.v.

Yosys reads these stubs alongside the chip-top-only Verilog so chip_top
synthesis sees submodules as opaque blackboxes with full parameter +
port info but no body — letting hierarchical PnR proceed even before
the submodule is hardened.
"""

import re
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.exit(f"usage: {argv[0]} <module_name> <repo_root>")
    module = argv[1]
    repo = Path(argv[2]).resolve()
    sv_path = repo / module / f"{module}.sv"
    out_path = repo / "build/sv2v/v-stub" / f"{module}.v"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text = sv_path.read_text()
    # Strip line + block comments so they don't interfere with paren counting.
    no_line = re.sub(r"//[^\n]*", "", text)
    clean = re.sub(r"/\*.*?\*/", "", no_line, flags=re.DOTALL)

    # Match the module declaration: from "module <name>" up through the
    # closing `);` of the port list. Paren-aware so nested parens in
    # parameter expressions / port widths don't confuse us.
    start = re.search(rf"module\s+{re.escape(module)}\b", clean)
    if not start:
        sys.exit(f"ERROR: could not find 'module {module}' in {sv_path}")

    # Walk forward until we find the closing `;` that terminates the port
    # list, tracking paren depth.
    pos = start.start()
    end = pos
    depth = 0
    in_string = False
    while end < len(clean):
        c = clean[end]
        if c == '"' and (end == 0 or clean[end - 1] != "\\"):
            in_string = not in_string
        elif not in_string:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == ";" and depth == 0:
                break
        end += 1
    if end >= len(clean):
        sys.exit(f"ERROR: could not find end of module {module} declaration in {sv_path}")

    header = clean[pos : end + 1]  # include the `;`

    # Output: just the header + an empty body + endmodule. No `(* blackbox *)`
    # attribute — that triggers yosys's `$abstract\` prefix which strips
    # parameter info and breaks chip_top's instantiation with #(.PARAM(...)).
    # Yosys will synthesize an empty module to no logic; with
    # SYNTH_HIERARCHY_MODE: keep this stays as a boundary in the netlist.
    out_path.write_text(
        "// Auto-generated stub for {module} — header copied from {sv_path}.\n"
        "// Same parameters/ports as the real module, empty body.\n"
        "{header}\n"
        "endmodule\n".format(module=module, sv_path=sv_path.relative_to(repo), header=header)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
