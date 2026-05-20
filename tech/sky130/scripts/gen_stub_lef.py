#!/usr/bin/env python3
"""
Generate a stub LEF for a module from its SV port list + config.yaml die
size + pin_order.cfg edge assignments.

A stub LEF has the same outline + pin positions as the eventual real
hardened module, but no internal geometry. Useful for hardening chip_top
in advance of (or independently from) the leaf hardens — chip_top's PnR
only needs LEF granularity for placement and routing topology.

Usage:
    gen_stub_lef.py <module_name> <repo_root> > module.lef

Reads:
    - <repo_root>/<module>/<module>.sv                (port declarations)
    - <repo_root>/tech/sky130/submodules/<module>/config.yaml      (die size)
    - <repo_root>/tech/sky130/submodules/<module>/<module>.pin_order.cfg

Writes a LEF on stdout. Exits non-zero with a clear message on error.

LEF contents:
    MACRO <module>
        SIZE die_w BY die_h
        PIN <name>
            DIRECTION INPUT|OUTPUT|INOUT
            USE SIGNAL
            PORT LAYER met3|met4 RECT ...
        END <name>
        ... (one PIN per bit of each bus, ordered by pin_order.cfg)
        OBS LAYER met1..met4 RECT 0 0 die_w die_h
    END <module>
"""

import re
import sys
from pathlib import Path

import yaml


PORT_RE = re.compile(
    r"^\s*(input|output|inout)\s+(?:wire|logic|reg)?\s*"
    r"(?:\[\s*([^\]]+?)\s*\])?\s*"
    r"(\w+)\s*[,)]",
    re.MULTILINE,
)


def parse_sv_ports(sv_path: Path, module_name: str) -> dict[str, dict]:
    """Return {pin_name: {direction, width}} for ports of <module_name>.

    width is an integer (1 for single-bit ports). Bus ranges with parameter
    expressions are resolved via the config.py constants if needed.
    """
    text = sv_path.read_text()
    # Find "module <name>" through "endmodule" — the entire module body.
    m = re.search(rf"module\s+{re.escape(module_name)}\b.*?endmodule",
                  text, re.DOTALL)
    if not m:
        sys.exit(f"ERROR: could not find module {module_name} in {sv_path}")
    module_body = m.group(0)
    # Strip line comments (// ... \n) and block comments (/* ... */) so a
    # comment between the last port and the closing `)` doesn't break our
    # port-end matching.
    module_body = re.sub(r"//[^\n]*", "", module_body)
    module_body = re.sub(r"/\*.*?\*/", "", module_body, flags=re.DOTALL)
    # Within the body, port declarations are input/output/inout lines until
    # the closing `);`. Just scan the whole body for direction-keyword lines.
    ports: dict[str, dict] = {}
    for direction, range_expr, name in PORT_RE.findall(module_body):
        if name in ports:
            continue  # duplicate from internal nets named like ports
        width = 1
        if range_expr:
            # Evaluate range like "31:0" or "BEAT_BYTES*8-1:0".
            parts = range_expr.split(":")
            if len(parts) == 2:
                hi = _eval_param_expr(parts[0])
                lo = _eval_param_expr(parts[1])
                if hi is not None and lo is not None:
                    width = abs(hi - lo) + 1
        ports[name] = {"direction": direction.upper(), "width": width}
    return ports


# Constants from config.py used to resolve parametric widths.
_PARAM_TABLE = {
    "BEAT_BYTES": 16,
    "MMA_M": 32,
    "MMA_N": 32,
    "TMEM_SLOTS": 4,
    "NUM_BARRIERS": 8,
    "INSTR_FIFO_DEPTH": 64,
    "IMEM_DEPTH": 64,
    "LOAD_FIFO_DEPTH": 8,
    "SCRUB_DEPTH": 128,
    "SMEM_SCRUB_DEPTH": 128,
    "SMEM_BYTES": 16384,
}


def _eval_param_expr(expr: str) -> int | None:
    """Try to evaluate a width expression using known constants."""
    expr = expr.strip()
    safe = re.sub(r"[A-Za-z_]\w*",
                  lambda m: str(_PARAM_TABLE.get(m.group(0), "0")),
                  expr)
    try:
        return int(eval(safe, {"__builtins__": {}}, {}))
    except Exception:
        return None


def parse_pin_order(cfg_path: Path) -> list[tuple[str, str]]:
    """Return [(edge, pin_or_pattern), ...] preserving order.

    edge is one of "N", "S", "E", "W". Patterns with `.*` are kept as-is
    and expanded later against the SV port widths.
    """
    out: list[tuple[str, str]] = []
    current_edge = None
    for raw in cfg_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", ";")):
            continue
        if line.startswith("#"):
            tag = line[1:].strip()
            if tag in ("N", "S", "E", "W"):
                current_edge = tag
            continue
        if line.startswith("$"):
            # Virtual spacer — ignore for stub purposes
            continue
        # Strip any inline comment
        line = line.split("//", 1)[0].split("#", 1)[0].strip()
        if not line:
            continue
        if current_edge is None:
            continue
        out.append((current_edge, line))
    return out


_BUS_RE = re.compile(r"^([A-Za-z_][\w]*)(?:\.\*|\\?\[\.\*\\?\])$")


def expand_pin_pattern(pattern: str, ports: dict[str, dict]) -> list[str]:
    """Expand `bus.*`, `bus\\[.*\\]`, `bus[.*]` to bus[0]..bus[N-1].

    Plain name stays as-is. Unknown ports are silently dropped.
    """
    m = _BUS_RE.match(pattern)
    if m:
        base = m.group(1)
        port = ports.get(base)
        if not port:
            return []
        if port["width"] <= 1:
            return [base]
        return [f"{base}[{i}]" for i in range(port["width"])]
    return [pattern]


def get_die_dimensions(cfg_yaml_path: Path, module: str,
                       repo: Path) -> tuple[float, float]:
    """Return (width, height) in microns, trying three sources in order.

    1. DIE_AREA in config.yaml (modules using FP_SIZING: absolute).
    2. STUB_SIZE: [w, h] in config.yaml (manual override for stub mode).
    3. SIZE line of the latest hardened LEF (modules using relative sizing
       that have already been hardened — we just copy their dims).
    """
    cfg = yaml.safe_load(cfg_yaml_path.read_text())
    die = cfg.get("DIE_AREA")
    if die and len(die) == 4:
        x0, y0, x1, y1 = (float(v) for v in die)
        return (x1 - x0, y1 - y0)
    stub_size = cfg.get("STUB_SIZE")
    if stub_size and len(stub_size) == 2:
        return (float(stub_size[0]), float(stub_size[1]))
    # Last resort: look at the latest run's LEF.
    runs_dir = repo / "tech/sky130/submodules" / module / "runs"
    if runs_dir.exists():
        runs = sorted(runs_dir.glob("RUN_*"), reverse=True)
        for run in runs:
            lef = run / "final" / "lef" / f"{module}.lef"
            if lef.exists():
                for line in lef.read_text().splitlines():
                    m = re.match(r"\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", line)
                    if m:
                        return (float(m.group(1)), float(m.group(2)))
    sys.exit(
        f"ERROR: cannot determine die size for {module}. "
        f"Set DIE_AREA or STUB_SIZE in {cfg_yaml_path}, or harden the module once."
    )


def edge_layer(edge: str) -> str:
    """met3 for N/S (horizontal edges); met4 for E/W (vertical edges)."""
    return "met3" if edge in ("N", "S") else "met4"


def pin_rect_on_edge(edge: str, offset: float, die_w: float, die_h: float,
                     pin_w: float = 0.6, pin_d: float = 4.0) -> tuple[float, float, float, float]:
    """Return RECT (x0, y0, x1, y1) for a pin at `offset` along `edge`.

    Pin extends `pin_d` µm into the macro from the edge, and is `pin_w` µm
    wide. Layout convention matches what OpenLane's io_place produces.
    """
    if edge == "N":
        return (offset - pin_w / 2, die_h - pin_d, offset + pin_w / 2, die_h)
    if edge == "S":
        return (offset - pin_w / 2, 0.0, offset + pin_w / 2, pin_d)
    if edge == "E":
        return (die_w - pin_d, offset - pin_w / 2, die_w, offset + pin_w / 2)
    if edge == "W":
        return (0.0, offset - pin_w / 2, pin_d, offset + pin_w / 2)
    raise ValueError(f"unknown edge {edge}")


def emit_lef(module: str, die_w: float, die_h: float,
             pins_by_edge: dict[str, list[tuple[str, str]]]) -> str:
    """Build the LEF text. pins_by_edge maps edge → [(pin_name, direction), ...]."""
    lines = [
        "VERSION 5.7 ;",
        '  NOWIREEXTENSIONATPIN ON ;',
        '  DIVIDERCHAR "/" ;',
        '  BUSBITCHARS "[]" ;',
        f"MACRO {module}",
        "  CLASS BLOCK ;",
        f"  FOREIGN {module} ;",
        "  ORIGIN 0.000 0.000 ;",
        f"  SIZE {die_w:.3f} BY {die_h:.3f} ;",
    ]
    edge_length = {"N": die_w, "S": die_w, "E": die_h, "W": die_h}
    for edge in ("N", "S", "E", "W"):
        pins = pins_by_edge.get(edge, [])
        if not pins:
            continue
        # Distribute pins evenly along the edge (start/end margins of 50 µm).
        margin = 50.0
        span = max(edge_length[edge] - 2 * margin, 1.0)
        step = span / max(len(pins) - 1, 1) if len(pins) > 1 else 0.0
        layer = edge_layer(edge)
        for i, (pin_name, direction) in enumerate(pins):
            offset = margin + i * step if len(pins) > 1 else edge_length[edge] / 2
            x0, y0, x1, y1 = pin_rect_on_edge(edge, offset, die_w, die_h)
            lines += [
                f"  PIN {pin_name}",
                f"    DIRECTION {direction} ;",
                "    USE SIGNAL ;",
                "    PORT",
                f"      LAYER {layer} ;",
                f"        RECT {x0:.3f} {y0:.3f} {x1:.3f} {y1:.3f} ;",
                "    END",
                f"  END {pin_name}",
            ]
    # Obstruction over the whole die on met1-met5 (chip_top routing must
    # go AROUND the macro via die margins, NOT over it on top metal).
    # This matches "no over-macro transit" intent — stubs being more
    # conservative than real LEFs is fine because real LEFs will only
    # ever be more permissive.
    lines += [
        "  OBS",
    ]
    for layer in ("met1", "met2", "met3", "met4", "met5"):
        lines += [
            f"    LAYER {layer} ;",
            f"      RECT 0.000 0.000 {die_w:.3f} {die_h:.3f} ;",
        ]
    lines += [
        "  END",
        f"END {module}",
        "END LIBRARY",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.exit(f"usage: {argv[0]} <module_name> <repo_root>")
    module = argv[1]
    repo = Path(argv[2]).resolve()
    sv_path = repo / module / f"{module}.sv"
    cfg_yaml = repo / "tech/sky130/submodules" / module / "config.yaml"
    pin_cfg = repo / "tech/sky130/submodules" / module / f"{module}.pin_order.cfg"

    for p in (sv_path, cfg_yaml, pin_cfg):
        if not p.exists():
            sys.exit(f"ERROR: missing {p}")

    ports = parse_sv_ports(sv_path, module)
    die_w, die_h = get_die_dimensions(cfg_yaml, module, repo)
    edge_pins = parse_pin_order(pin_cfg)

    # Group pins by edge, expanding bus patterns and looking up directions.
    pins_by_edge: dict[str, list[tuple[str, str]]] = {"N": [], "S": [], "E": [], "W": []}
    for edge, pattern in edge_pins:
        for pin_name in expand_pin_pattern(pattern, ports):
            base = pin_name.split("[")[0]
            port = ports.get(base)
            if port is None:
                continue  # pattern matched a name that isn't in the SV
            pins_by_edge[edge].append((pin_name, port["direction"]))

    sys.stdout.write(emit_lef(module, die_w, die_h, pins_by_edge))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
