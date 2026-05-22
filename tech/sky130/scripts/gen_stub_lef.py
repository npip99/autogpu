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
    "MMA_K": 32,
    "TMEM_SLOTS": 4,
    "N_SLOTS": 4,
    "NUM_BARRIERS": 8,
    "INSTR_FIFO_DEPTH": 64,
    "IMEM_DEPTH": 64,
    "LOAD_FIFO_DEPTH": 8,
    "SCRUB_DEPTH": 128,
    "SMEM_SCRUB_DEPTH": 128,
    "SMEM_BYTES": 16384,
}


def _clog2(x: int) -> int:
    """Ceiling log2 for integer x ≥ 1."""
    if x <= 1:
        return 1
    return (x - 1).bit_length()


def _eval_param_expr(expr: str) -> int | None:
    """Try to evaluate a width expression using known constants."""
    expr = expr.strip()
    # Translate $clog2(X) → _clog2(X) so the safe eval can resolve it.
    expr = re.sub(r"\$clog2\b", "_clog2", expr)
    safe = re.sub(r"[A-Za-z_]\w*",
                  lambda m: str(_PARAM_TABLE.get(m.group(0), m.group(0))),
                  expr)
    try:
        return int(eval(safe, {"__builtins__": {}, "_clog2": _clog2}, {}))
    except Exception:
        return None


def parse_pin_order(cfg_path: Path) -> list[tuple[str, object]]:
    """Return [(edge, item), ...] preserving order.

    edge is one of "N", "S", "E", "W". item is either a string (pin name
    or bus pattern with `.*`) or an int (virtual pin count from `$N`).
    Virtual pins consume track slots without producing real pins —
    this matches openlane's `equally_spaced_sequence` behavior so stub
    pin positions match what openlane will choose for the real harden.
    """
    out: list[tuple[str, object]] = []
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
        # Strip any inline comment
        line = line.split("//", 1)[0].split("#", 1)[0].strip()
        if not line:
            continue
        if current_edge is None:
            continue
        if line.startswith("$"):
            # Virtual pin marker: "$N" consumes N track slots.
            try:
                count = int(line[1:])
            except ValueError:
                continue
            out.append((current_edge, count))
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
    """Pin layer per openlane convention:
      N/S edges use FP_IO_VLAYER (met4) — pin extends vertically into macro
      E/W edges use FP_IO_HLAYER (met3) — pin extends horizontally into macro
    """
    return "met4" if edge in ("N", "S") else "met3"


_PDK_TRACKS = None


def load_pdk_tracks(pdk_root: Path | None = None) -> dict:
    """Parse sky130A tracks.info → {layer: {"X": (origin, step), "Y": (origin, step)}}.

    Cached after first call.
    """
    global _PDK_TRACKS
    if _PDK_TRACKS is not None:
        return _PDK_TRACKS
    if pdk_root is None:
        pdk_root = Path.home() / ".volare/volare/sky130/versions"
    versions = sorted(pdk_root.iterdir())
    if not versions:
        raise SystemExit(f"no sky130 PDK install found at {pdk_root}")
    info = versions[-1] / "sky130A/libs.tech/openlane/sky130_fd_sc_hd/tracks.info"
    if not info.exists():
        raise SystemExit(f"missing tracks.info: {info}")
    out: dict = {}
    for line in info.read_text().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        layer, axis, origin, step = parts[0], parts[1], float(parts[2]), float(parts[3])
        out.setdefault(layer, {})[axis] = (origin, step)
    _PDK_TRACKS = out
    return out


# sky130 met3/met4 width + spacing (from tech.lef). H_WIDTH/V_WIDTH = 2× minWidth
# (openlane's ver_width_mult / hor_width_mult default = 2).
LAYER_PIN_WIDTH = {"met3": 0.6, "met4": 0.6}     # = 2 × minWidth(0.3)
LAYER_SPACING   = {"met3": 0.3, "met4": 0.3}


def _slot_positions(edge: str, die_w: float, die_h: float,
                    sequence: list) -> list[float | None]:
    """Match openlane's `equally_spaced_sequence` exactly.

    `sequence` is the ordered list of items on this edge: each element is
    either a real pin (anything but int) or an int virtual-pin count.
    Returns one position per element: float for real pins, None for virtual.
    """
    layer = edge_layer(edge)
    tracks_info = load_pdk_tracks()
    axis = "Y" if edge in ("E", "W") else "X"
    origin, step = tracks_info[layer][axis]
    upper = die_h if axis == "Y" else die_w
    n_tracks = int((upper - origin) // step) + 1
    all_tracks = [origin + i * step for i in range(n_tracks)]
    min_dist = LAYER_PIN_WIDTH[layer] + LAYER_SPACING[layer]
    # ceil(min_dist / step) — use scaled ints to avoid float rounding.
    keep_every = max(1, -(-int(round(min_dist * 1000)) // int(round(step * 1000))))
    filtered = [all_tracks[i] for i in range(len(all_tracks)) if (i % keep_every) == 0]
    n_real = sum(1 for x in sequence if not isinstance(x, int))
    n_virtual = sum(x for x in sequence if isinstance(x, int))
    total = n_real + n_virtual
    n_avail = len(filtered)
    out: list[float | None] = []
    if total == 0:
        return [None] * len(sequence)
    if total > n_avail:
        raise SystemExit(
            f"edge {edge}: {total} pins (incl. virtual) need tracks "
            f"but only {n_avail} available on {layer}")
    if total == n_avail:
        cur = 0
        for item in sequence:
            if isinstance(item, int):
                cur += item
                out.append(None)
            else:
                out.append(filtered[cur])
                cur += 1
        return out
    tracks_per_pin = n_avail // total
    used = tracks_per_pin * (total - 1) + 1
    unused = n_avail - used
    cur = unused // 2
    for item in sequence:
        if isinstance(item, int):
            cur += tracks_per_pin * item
            out.append(None)
        else:
            out.append(filtered[cur])
            cur += tracks_per_pin
    return out


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
             pins_by_edge: dict[str, list]) -> str:
    """Build the LEF text. pins_by_edge maps edge → ordered list whose
    items are either (pin_name, direction) tuples for real pins, or int
    virtual-pin-counts that consume track slots without being emitted.

    Slot math mirrors openlane's `equally_spaced_sequence`: total slots =
    real_pin_count + sum(virtual_pin_counts); pins land at evenly spaced
    offsets within the edge margins. This makes the stub pin positions
    identical to what openlane's real ioplacer produces from the same
    pin_order.cfg.
    """
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
    for edge in ("N", "S", "E", "W"):
        items = pins_by_edge.get(edge, [])
        if not items:
            continue
        positions = _slot_positions(edge, die_w, die_h, items)
        layer = edge_layer(edge)
        for item, pos in zip(items, positions):
            if isinstance(item, int) or pos is None:
                continue
            pin_name, direction = item
            x0, y0, x1, y1 = pin_rect_on_edge(edge, pos, die_w, die_h)
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
    # Virtual-pin counts ($N) pass through unchanged so they consume
    # track slots in emit_lef just like openlane's ioplacer does.
    pins_by_edge: dict[str, list] = {"N": [], "S": [], "E": [], "W": []}
    for edge, item in edge_pins:
        if isinstance(item, int):
            pins_by_edge[edge].append(item)
            continue
        for pin_name in expand_pin_pattern(item, ports):
            base = pin_name.split("[")[0]
            port = ports.get(base)
            if port is None:
                continue  # pattern matched a name that isn't in the SV
            pins_by_edge[edge].append((pin_name, port["direction"]))

    sys.stdout.write(emit_lef(module, die_w, die_h, pins_by_edge))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
