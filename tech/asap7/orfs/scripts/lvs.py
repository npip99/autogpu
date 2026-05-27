"""LVS (Layout-vs-Schematic) driver for asap7 hardened blocks.

Run inside the openroad/orfs:latest container via KLayout's batch mode.
Compares post-route GDS to the post-route gate-level Verilog netlist.

Method: cell-instance LVS. Standard cells (ASAP7_75t_*) and hardened
macros are treated as black-box subcircuits. We extract the top-level
metal routing into a gate-level netlist via KLayout's `LayoutToNetlist`
and compare against the netlist parsed from `6_final.v`. LVS passes
only when BOTH checks below come back clean:

1. Structural cell-instance compare via pya.NetlistComparer.
   Catches: misplaced cells, shorts/opens, pin swaps, mis-routed
   buses, dangling CTS-clkload output mismatches.

2. Macro power-pin connectivity check (run BEFORE simplify).
   Catches: PSM-0069 — floating macro VDD/VSS pins (PDN didn't wire
   parent power rails to leaf macro power pins).

What it does NOT catch (asap7 PDK gap — see DESIGN.md):
  - Standard-cell-internal transistor-level bugs (foundry's job and we
    have no asap7 LVS rules to check them).

Usage: see tech/asap7/orfs/lvs.sh.
"""
import os
import re
import sys
import time

import pya  # provided by KLayout


# -- ASAP7 GDS layer/datatype map --------------------------------------------
# Numbers come from asap7.lyt connectivity definitions.
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
# Inter-metal via layers (V1 connects M1<->M2, V2 connects M2<->M3, ...).
VIA_LAYERS = [
    ("V1", 21, 0),
    ("V2", 25, 0),
    ("V3", 35, 0),
    ("V4", 45, 0),
    ("V5", 55, 0),
    ("V6", 65, 0),
    ("V7", 75, 0),
    ("V8", 85, 0),
]
# Pin polygon + text label layers per metal.
PIN_POLY_DATATYPE = 251
PIN_LABEL_DATATYPE = 2


def setup_l2n(ly, top_cell):
    """Configure a LayoutToNetlist with the asap7 metal/via stack.

    We deliberately exclude the device layers (poly, active, V0) — the
    extraction stops at the cell-instance level, treating each subcell
    (standard cell or hardened macro) as a black-box subcircuit whose
    pins come from its internal M1.PIN / M{N}.PIN labels.
    """
    l2n = pya.LayoutToNetlist(pya.RecursiveShapeIterator(ly, top_cell, []))
    l2n.threads = max(1, (os.cpu_count() or 1))

    metals = {}
    for name, lid, dt in METAL_LAYERS:
        metals[name] = l2n.make_polygon_layer(ly.layer(lid, dt), name)
    vias = {}
    for name, lid, dt in VIA_LAYERS:
        vias[name] = l2n.make_polygon_layer(ly.layer(lid, dt), name)

    # Each metal connects to itself (so disjoint polygons on the same
    # layer merge if they overlap).
    for name, layer in metals.items():
        l2n.connect(layer)
    # Via stack: V{n} connects M{n} to M{n+1}.
    metal_order = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9"]
    via_order = ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8"]
    for i, vname in enumerate(via_order):
        lower, upper = metal_order[i], metal_order[i + 1]
        l2n.connect(metals[lower], vias[vname])
        l2n.connect(vias[vname], metals[upper])

    # Pin/label layers per metal. Each metal layer M{N} has two associated
    # ancillary layers from ORFS / asap7 streamout:
    #   M{N}.PIN (datatype 251): polygon/box shapes marking pin geometry.
    #     For top-level ports, these are drawn at the chip boundary and
    #     usually extend further than the routed metal underneath.
    #   M{N}.LABEL (datatype 2): text labels at pin positions, naming the
    #     external net.
    # For label-to-net association we need three connections per metal:
    #   - metal connects to its own pin polys (bridges floating pin boxes
    #     into the routed nets they overlap)
    #   - metal connects to its label texts (interior cells whose pin label
    #     sits directly on routed M{N})
    #   - pin polys connect to label texts (top-level pins whose label sits
    #     over the pin box but with no routing directly beneath)
    pin_polys = {}
    for mname, mlid, _ in METAL_LAYERS:
        pp = l2n.make_polygon_layer(ly.layer(mlid, PIN_POLY_DATATYPE),
                                    f"{mname}.PIN")
        lt = l2n.make_text_layer(ly.layer(mlid, PIN_LABEL_DATATYPE),
                                 f"{mname}.LABEL")
        l2n.connect(metals[mname], pp)
        l2n.connect(metals[mname], lt)
        l2n.connect(pp, lt)
        # Some flows place the pin label directly on the .PIN datatype (no
        # separate .LABEL layer), so also pick up texts there.
        lt2 = l2n.make_text_layer(ly.layer(mlid, PIN_POLY_DATATYPE),
                                  f"{mname}.PIN_text")
        l2n.connect(metals[mname], lt2)
        l2n.connect(pp, lt2)
        pin_polys[mname] = pp

    return l2n


# -- Verilog reference parser -------------------------------------------------
# Parses a single-module structural netlist as produced by ORFS at
# 6_final.v. Format constraints we assume (verified against asap7 output):
#   - One module
#   - input/output/inout statements with optional [hi:lo] bus widths
#   - wire declarations (always single-bit in OpenROAD output)
#   - Cell instances: TYPE inst_name (.pin(net), .pin(net), ...);
#   - Net references: identifier, identifier[index], or escaped identifier
#   - No concatenation {a,b}, no parameters, no generate blocks


_TOK_ESCAPED = re.compile(r"\\[!-~]+")
_TOK_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _parse_port_decl(decl):
    """Parse `input [hi:lo] a, b, c;` style. Returns list of (name, bit_indices).

    For a scalar port, bit_indices is [None]; for a bus of width W (hi:lo) it
    is [hi, hi-1, ..., lo].
    """
    # split off bus width if present (word boundary after keyword)
    m = re.match(r"\s*\b(input|output|inout)\b\s*(\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s*(.+)\s*;",
                 decl, flags=re.S)
    if not m:
        return []
    bus = m.group(2)
    if bus:
        hi, lo = int(m.group(3)), int(m.group(4))
        step = -1 if hi >= lo else 1
        bits = list(range(hi, lo + step, step))
    else:
        bits = [None]
    raw = m.group(5)
    names = [n.strip() for n in raw.split(",") if n.strip()]
    return [(name, bits) for name in names]


def _bit_net_name(name, idx):
    return name if idx is None else f"{name}[{idx}]"


def _tokenize_pin_value(s):
    """Return list of bit-level net names referenced by a pin connection.

    Handles plain identifier (`a`, `_001_`, `\\u_fma.x[3] `), indexed bus
    reference (`bus[3]`), and concatenation `{a, b, c[2]}`. For concat,
    returns elements in declaration (left-to-right MSB) order.
    """
    s = s.strip()
    if not s:
        return []
    # Concatenation
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        # split top-level commas (no nested concats in OR output)
        return [bit for elt in inner.split(",") for bit in _tokenize_pin_value(elt.strip())]
    # Escaped identifier ends at whitespace; keep the whole thing as net name.
    if s.startswith("\\"):
        return [s.rstrip()]
    # Plain or indexed bus reference: foo or foo[3] or foo [3:0] (unlikely)
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_$]*)\s*(?:\[\s*(\d+)\s*\])?$", s)
    if m:
        name, idx = m.group(1), m.group(2)
        return [name if idx is None else f"{name}[{idx}]"]
    # Literal constant (e.g. 1'b0): represent as a synthetic net name; LVS
    # will treat each occurrence as a distinct net. OpenROAD's post-resize
    # netlist normally replaces these with TIE cells, so this is rare.
    if re.match(r"^\d+'[bBdDoOhH][0-9a-fA-F_xz]+$", s):
        return [f"$lit${s}"]
    return [s]  # fallthrough: take as-is, may cause mismatch — flagged in logs


def parse_verilog(path, top_name):
    """Parse a structural Verilog netlist into a pya.Netlist.

    The returned Netlist has one top circuit (named `top_name`) plus one
    blank subcircuit per cell type used. Subcircuit pin order is determined
    by first-seen order in the netlist (so it matches the layout-extracted
    side, which infers pin order from labels too).
    """
    with open(path) as fh:
        text = _strip_comments(fh.read())

    # Find the module matching top_name; fall back to the first module
    # if no name match (some flows write a netlist whose only module is
    # the design top without echoing the requested name).
    matches = list(re.finditer(
        r"\bmodule\s+(\w+)\s*\(([^;]*)\)\s*;(.*?)\bendmodule\b",
        text, flags=re.S))
    if not matches:
        raise RuntimeError(f"no module found in {path}")
    m = next((x for x in matches if x.group(1) == top_name), None)
    if m is None:
        if len(matches) == 1:
            m = matches[0]
            print(f"  warn: expected module {top_name}, found "
                  f"{m.group(1)} — proceeding (single-module netlist)",
                  file=sys.stderr)
        else:
            raise RuntimeError(
                f"module {top_name} not found in {path}; available: "
                f"{[x.group(1) for x in matches]}")
    body = m.group(3)
    actual_top = m.group(1)

    # Port direction + bus declarations. Require a word boundary after the
    # direction keyword: instance names like `output95` (OpenROAD-generated)
    # would otherwise false-match against the `output` literal.
    port_bits = {}  # bit_net_name -> direction
    for d in re.finditer(r"\b(input|output|inout)\b([^;]*);", body):
        decl = d.group(0)
        for (pname, bits) in _parse_port_decl(decl):
            for b in bits:
                port_bits[_bit_net_name(pname, b)] = d.group(1)

    # Build Netlist.
    nl = pya.Netlist()
    nl.case_sensitive = True
    top = pya.Circuit()
    top.name = actual_top
    nl.add(top)

    # Pre-create pin objects on the top circuit in declared order.
    top_pins = {}
    for bit_name in port_bits:
        pin = top.create_pin(bit_name)
        net = top.create_net(bit_name)
        top.connect_pin(pin, net)
        top_pins[bit_name] = (pin, net)

    # Helper: get-or-create a net inside a circuit.
    def get_or_make_net(circ, name):
        n = circ.net_by_name(name)
        if n is None:
            n = circ.create_net(name)
        return n

    # Cache of cell-type circuits (blank with pins matching first-seen order).
    cell_circuits = {}

    def get_or_make_cell_circuit(cell_type, pin_names_in_order):
        if cell_type in cell_circuits:
            return cell_circuits[cell_type]
        c = pya.Circuit()
        c.name = cell_type
        for pn in pin_names_in_order:
            c.create_pin(pn)
        nl.add(c)
        cell_circuits[cell_type] = c
        return c

    # Cell instance grammar: capture cell type, instance name, and the
    # parenthesised pin list. Pin list grammar inside (...): .pin(value), ...
    # Pins/values can contain nested brackets but not nested parentheses.
    inst_re = re.compile(
        r"^[ \t]*(\\?[A-Za-z_][A-Za-z0-9_$]*)\s+"
        r"(\\[!-~]+\s|[A-Za-z_][A-Za-z0-9_$]*)\s*\("
        r"(.*?)\)\s*;",
        flags=re.M | re.S,
    )

    pin_re = re.compile(r"\.(\w+)\s*\(\s*([^()]*?)\s*\)")

    for im in inst_re.finditer(body):
        cell_type, inst_name, pin_list = im.group(1), im.group(2), im.group(3)
        # Skip Verilog keywords that may match the grammar (input/output/wire
        # decls are already extracted above and don't have the .pin() syntax).
        if cell_type in {"input", "output", "inout", "wire", "reg",
                         "assign", "module", "endmodule"}:
            continue
        inst_name = inst_name.rstrip()

        # Collect pin connections in declared order.
        pin_connections = []
        for pm in pin_re.finditer(pin_list):
            pname = pm.group(1)
            pval = pm.group(2).strip()
            pin_connections.append((pname, pval))
        if not pin_connections:
            continue

        # Expand concat-bus pin connections like `.init_data({a31, ..., a0})`
        # into per-bit pin slots `init_data[31]..init_data[0]`. This matches
        # the asap7 macro LEFs where each bit is a separate pin in the GDS.
        expanded = []  # list of (pin_name, net_name) — one entry per single bit
        for pn, pval in pin_connections:
            bits = _tokenize_pin_value(pval)
            if len(bits) == 1:
                expanded.append((pn, bits[0]))
            else:
                # Verilog concat is MSB-first; the bus pin's MSB index is
                # len(bits)-1 in our slot space (the cell's actual LEF order
                # we don't know — but the convention is consistent between
                # parent.v and the macro.lef so the binding is symmetric).
                width = len(bits)
                for k, net_name in enumerate(bits):
                    bit_idx = width - 1 - k
                    expanded.append((f"{pn}[{bit_idx}]", net_name))

        cell_circ = get_or_make_cell_circuit(
            cell_type, [pn for pn, _ in expanded])
        existing_pins = {p.name(): p for p in cell_circ.each_pin()}
        for pn, _ in expanded:
            if pn not in existing_pins:
                p = cell_circ.create_pin(pn)
                existing_pins[pn] = p

        sub = top.create_subcircuit(cell_circ, inst_name)
        for pn, net_name in expanded:
            net = get_or_make_net(top, net_name)
            sub.connect_pin(existing_pins[pn], net)

    # asap7 standard cells all have VDD + VSS power pins, but Verilog
    # netlists omit power declarations. To make the reference match the
    # layout extraction, inject VDD/VSS pins onto every cell circuit and
    # wire them to global VDD/VSS nets at the top level.
    vdd_net = get_or_make_net(top, "VDD")
    vss_net = get_or_make_net(top, "VSS")
    # Also expose VDD/VSS as top-level pins (the layout extracts them as
    # such, since pin labels for power straps live on M6).
    for pname, pnet in (("VDD", vdd_net), ("VSS", vss_net)):
        if top.net_by_name(pname) is None or top.pin_by_name(pname) is None:
            existing = top.pin_by_name(pname) if hasattr(top, "pin_by_name") else None
            if existing is None:
                pin = top.create_pin(pname)
                top.connect_pin(pin, pnet)

    for cell_circ in list(nl.each_circuit()):
        if cell_circ is top:
            continue
        existing = {p.name(): p for p in cell_circ.each_pin()}
        for power_name, power_net in (("VSS", vss_net), ("VDD", vdd_net)):
            if power_name not in existing:
                p = cell_circ.create_pin(power_name)
                existing[power_name] = p
        # Wire every subcircuit instance's VDD/VSS pin to the top-level
        # global net.
        for sub in top.each_subcircuit():
            if sub.circuit_ref() is cell_circ:
                sub.connect_pin(existing["VDD"], vdd_net)
                sub.connect_pin(existing["VSS"], vss_net)

    return nl


# -- Comparison helpers -------------------------------------------------------


# (KLayout 0.30.7 segfaults inside compare_impl when a Python-subclassed
# GenericNetlistCompareLogger is attached, regardless of which callbacks
# are overridden. We deliberately do not register a logger; on failure
# we mine pya.NetlistComparer.unmatched_circuits_{a,b} and walk the
# netlists for diagnostics instead — see main().)


# -- Physical-only-cell filter ------------------------------------------------
# Cells that ORFS inserts during fill / tapcell / antenna-diode stages and
# which therefore appear in the GDS but never in the gate-level Verilog.
# Pattern-match by prefix so all drive-strength variants are covered.
_PHYSICAL_PREFIXES = (
    "FILLER",       # area filler (FILLERxp5_ASAP7_75t_R, FILLER_ASAP7_75t_R)
    "TAPCELL",      # well taps
    "DECAP",        # decoupling-capacitor cells (DECAPx*_ASAP7_75t_R)
    "VIA_",         # KLayout's representation of GDS VIA cells
)


def _is_physical_only(name):
    return any(name.startswith(p) for p in _PHYSICAL_PREFIXES)


def _check_macro_power(top_circuit, log):
    """Report subcircuit instances whose VDD/VSS pins land on a
    single-fanout net at the parent level.

    A 1-fanout power net at the parent means the parent's PDN didn't
    actually wire that instance's power pin to the global rail — the
    PSM-0069 failure mode. We must check this BEFORE the global
    simplify() because simplify purges single-fanout nets, erasing the
    evidence.

    Returns a list of (instance_name, cell_type, pin_name) violations.
    """
    violations = []
    for sub in top_circuit.each_subcircuit():
        ref = sub.circuit_ref()
        for pin in ref.each_pin():
            pname = pin.name()
            if pname not in ("VDD", "VSS"):
                continue
            net = sub.net_for_pin(pin.id())
            if net is None:
                violations.append((sub.name, ref.name, pname, "unconnected"))
                continue
            # Count connections on this net. A real global power net has
            # hundreds; a single-fanout dangling net is a PDN bug.
            n_subc = sum(1 for _ in net.each_subcircuit_pin())
            n_top = sum(1 for _ in net.each_pin())
            if n_subc + n_top <= 1:
                violations.append((sub.name, ref.name, pname,
                                   f"dangling (net {net.expanded_name()})"))
    if violations:
        log(f"  PDN VIOLATIONS: {len(violations)} power pins land on "
            f"dangling/unconnected nets")
        # Group by cell type for compact reporting
        from collections import defaultdict
        by_cell = defaultdict(list)
        for inst, cell, pin, why in violations:
            by_cell[cell].append((inst, pin, why))
        for cell, lst in sorted(by_cell.items()):
            log(f"    {cell}: {len(lst)} pins")
            for inst, pin, why in lst[:3]:
                log(f"      {inst}.{pin}: {why}")
            if len(lst) > 3:
                log(f"      ... ({len(lst) - 3} more)")
    return violations


def _blackbox_macros(netlist, top_name, log):
    """Strip every hardened-macro subcircuit's internals down to its pins.

    At a parent-level extraction (e.g., compute_array), each instantiated
    macro (mac_tmem_cell, skew_lane_a/b, cmd_unit, ...) appears in the
    GDS as a full nested subcell with its own routed metal, and KLayout
    extracts its full internal netlist. The Verilog reference, however,
    only references those macros by name + pin list — their internals
    aren't redefined. To enable apples-to-apples comparison at the parent
    level, blackbox each macro on the layout side by removing all its
    internal subcircuit instances and non-pin-bearing nets.

    A "macro" is any extracted circuit that:
      - is not the top cell, AND
      - does not have an ASAP7 leaf-cell naming pattern (`*_ASAP7_75t_*`),
        and isn't a known physical-only cell.

    """
    LEAF_PAT = re.compile(r"_ASAP7_75t_")
    n_macros = 0
    n_pins_kept = 0
    for circ in netlist.each_circuit():
        if circ.name == top_name:
            continue
        if LEAF_PAT.search(circ.name) or _is_physical_only(circ.name):
            continue
        # Macro: blackbox it.
        n_macros += 1
        n_pins_kept += circ.pin_count()
        # Remove all subcircuit instances.
        for sub in list(circ.each_subcircuit()):
            circ.remove_subcircuit(sub)
        # Remove all devices, if any (we don't extract any but be defensive).
        try:
            for dev in list(circ.each_device()):
                circ.remove_device(dev)
        except Exception:
            pass
        # Remove non-pin-bearing internal nets. Pin-bearing nets must stay
        # so the cell still has its external interface.
        for net in list(circ.each_net()):
            if sum(1 for _ in net.each_pin()) == 0:
                circ.remove_net(net)
    if n_macros:
        log(f"  blackboxed {n_macros} macro circuits "
            f"({n_pins_kept} pins preserved)")
    return n_macros


def _add_dangling_nets(netlist):
    """Create a unique dangling net for every subcircuit instance pin that
    wasn't bound by the Verilog parser.

    The most common case: CTS-inserted INV/BUF "clkload" cells whose Y
    output is unconnected (intentional clock-tree load balancing). Adding
    dangling nets here keeps the reference netlist topology aligned with
    the GDS extraction, which always finds the cell's output metal net
    even when nothing else drives or loads it.
    """
    added = 0
    for circ in netlist.each_circuit():
        for sub in circ.each_subcircuit():
            ref = sub.circuit_ref()
            for pin in ref.each_pin():
                net = sub.net_for_pin(pin.id())
                if net is None:
                    n = circ.create_net(f"$dangling${added}")
                    sub.connect_pin(pin, n)
                    added += 1
    return added


def _purge_physical_only(netlist, log):
    """Remove instances of physical-only cell types from every circuit.

    Returns the number of subcircuit instances removed. Iterates each
    circuit; for each subcircuit instance whose referenced circuit's
    name matches a physical-only prefix, delete the instance. Then
    purge the now-unused circuit definitions.
    """
    removed = 0
    bad_circuits = set()
    for circ in netlist.each_circuit():
        to_delete = []
        for sub in circ.each_subcircuit():
            ref = sub.circuit_ref()
            if _is_physical_only(ref.name):
                to_delete.append(sub)
                bad_circuits.add(ref.name)
        for sub in to_delete:
            circ.remove_subcircuit(sub)
            removed += 1
    for nm in bad_circuits:
        c = netlist.circuit_by_name(nm)
        if c is not None:
            netlist.purge_circuit(c)
    if removed:
        log(f"  purged {removed} physical-only instances "
            f"({len(bad_circuits)} cell types: "
            f"{', '.join(sorted(bad_circuits)[:6])}...)")
    return removed


def _dump_failure_diagnostics(ext_nl, ref_nl, ext_top, ref_top, comparer, log):
    """Best-effort failure diagnostics for an LVS mismatch.

    KLayout's pya.NetlistComparer doesn't expose net-level mismatch details
    when no Python logger is attached (and attaching one crashes on
    hierarchical designs in 0.30.7). We mine what we can from the
    `unmatched_circuits` API plus a self-rolled net-fingerprint differ.
    """
    log("")
    log("--- diagnostics: top-level topology ---")
    if ext_top is None:
        log("  ext top: <handle destroyed by simplify()>")
    else:
        log(f"  ext top: {ext_top.pin_count()} pins, "
            f"{sum(1 for _ in ext_top.each_net())} nets, "
            f"{sum(1 for _ in ext_top.each_subcircuit())} subcircuits")
    if ref_top is None:
        log("  ref top: <handle destroyed by simplify()>")
    else:
        log(f"  ref top: {ref_top.pin_count()} pins, "
            f"{sum(1 for _ in ref_top.each_net())} nets, "
            f"{sum(1 for _ in ref_top.each_subcircuit())} subcircuits")

    log("--- diagnostics: unmatched circuits ---")
    a_unmatched = comparer.unmatched_circuits_a(ext_nl, ref_nl)
    b_unmatched = comparer.unmatched_circuits_b(ext_nl, ref_nl)
    log(f"  layout-only: "
        f"{[c.name for c in a_unmatched] if a_unmatched else 'none'}")
    log(f"  reference-only: "
        f"{[c.name for c in b_unmatched] if b_unmatched else 'none'}")

    log("--- diagnostics: per-cell pin-set comparison ---")
    ref_circs = {c.name: c for c in ref_nl.each_circuit()}
    ext_circs = {c.name: c for c in ext_nl.each_circuit()}
    pin_diffs = 0
    for name in sorted(set(ref_circs) & set(ext_circs)):
        rp = sorted([p.name() for p in ref_circs[name].each_pin()])
        ep = sorted([p.name() for p in ext_circs[name].each_pin()])
        if rp != ep:
            pin_diffs += 1
            log(f"  {name}: ref={rp} ext={ep}")
    only_ref = set(ref_circs) - set(ext_circs)
    only_ext = set(ext_circs) - set(ref_circs)
    if only_ref:
        log(f"  reference-only cell types: {sorted(only_ref)}")
    if only_ext:
        log(f"  layout-only cell types: {sorted(only_ext)}")
    if not (pin_diffs or only_ref or only_ext):
        log("  (no cell-level pin-set differences)")

    # Net-fingerprint diff at the top level: each net's fingerprint is
    # the sorted tuple of (subcircuit_cell_type, pin_name) it touches,
    # plus ("TOP", pin_name) for top-port connections. Fingerprints that
    # exist on only one side are the most direct evidence of mis-wiring.
    log("--- diagnostics: top-net fingerprint diff ---")
    if ext_top is None or ref_top is None:
        log("  (skipped: top-circuit handle lost across simplify)")
        return
    from collections import Counter

    def _fp(net):
        sigs = []
        for spp in net.each_subcircuit_pin():
            sigs.append((spp.subcircuit().circuit_ref().name,
                         spp.pin().name()))
        for pp in net.each_pin():
            sigs.append(("TOP", pp.pin().name()))
        return tuple(sorted(sigs))

    ext_fps = Counter(_fp(n) for n in ext_top.each_net())
    ref_fps = Counter(_fp(n) for n in ref_top.each_net())
    diffs = 0
    for fp in sorted(set(ext_fps) | set(ref_fps), key=lambda f: (-len(f), f)):
        e, r = ext_fps[fp], ref_fps[fp]
        if e != r:
            diffs += 1
            if diffs <= 30:
                sample = list(fp)[:4]
                log(f"  ext={e} ref={r} touches {len(fp)} pins, e.g. {sample}")
    if diffs == 0:
        log("  (no net fingerprint differences — topology matches but "
            "comparator still disagrees; check for global-net "
            "VDD/VSS-style anomalies)")
    elif diffs > 30:
        log(f"  ... ({diffs - 30} more fingerprint differences hidden)")


# -- Main flow ----------------------------------------------------------------


class _Args:
    pass


def _args_from_globals():
    """Pull script parameters from `klayout -rd k=v` globals.

    KLayout's batch mode doesn't pass argv to the script, so the host
    must hand inputs via -rd name=value pairs. Required keys: gds,
    verilog, top, report. Optional: netlist_out.
    """
    g = globals()
    args = _Args()
    for key in ("gds", "verilog", "top", "report"):
        if key not in g:
            sys.stderr.write(
                f"missing -rd {key}=... — see tech/asap7/orfs/lvs.sh\n")
            sys.exit(2)
        setattr(args, key, g[key])
    args.netlist_out = g.get("netlist_out")
    return args


def main(argv=None):
    args = _args_from_globals()

    t_start = time.time()
    report_lines = []
    def log(msg):
        print(msg, flush=True)
        report_lines.append(msg)

    log(f"== asap7 LVS ({args.top}) ==")
    log(f"  layout:  {args.gds}")
    log(f"  netlist: {args.verilog}")

    # 1) Extract from GDS.
    log("[1/3] Reading layout and extracting netlist from GDS...")
    ly = pya.Layout()
    ly.read(args.gds)
    top_cell = ly.cell(args.top)
    if top_cell is None:
        # Fall back to whatever the GDS reports as top.
        top_cell = ly.top_cell()
        log(f"  warn: '{args.top}' not found as a cell; using GDS top "
            f"'{top_cell.name}'")
    l2n = setup_l2n(ly, top_cell)
    l2n.extract_netlist()
    ext_nl = l2n.netlist()
    # Promote any net carrying a top-level label into a top-circuit pin.
    # Without this, top IO pins are anonymous and the comparator can't
    # align them with the Verilog port list.
    ext_nl.make_top_level_pins()
    ext_top = ext_nl.circuit_by_name(top_cell.name) or ext_nl.top_circuit()

    # Physical-only cells get streamed into the GDS by the fill/tapcell
    # stages but never appear in the post-route Verilog. Remove their
    # instances + circuits from the extracted side before comparison so
    # the comparator doesn't flag them as schema mismatches.
    n_purged = _purge_physical_only(ext_nl, log)

    # Hardened macros (mac_tmem_cell, skew_lane, cmd_unit, ...) are
    # already streamed in as full subcells of the parent GDS. The Verilog
    # reference only declares macro INSTANCES, not internals, so flatten
    # the extracted macros down to their pins before comparison.
    n_macros = _blackbox_macros(ext_nl, top_cell.name, log)
    log(f"  extracted: {sum(1 for _ in ext_nl.each_circuit())} circuits, "
        f"top has {ext_top.pin_count()} pins, "
        f"{sum(1 for _ in ext_top.each_subcircuit())} subcircuit instances "
        f"(purged {n_purged} physical-only)")

    if args.netlist_out:
        writer = pya.NetlistSpiceWriter()
        ext_nl.write(args.netlist_out, writer)
        log(f"  layout netlist written: {args.netlist_out}")

    # 2) Parse the Verilog reference.
    log("[2/3] Parsing reference Verilog netlist...")
    ref_nl = parse_verilog(args.verilog, args.top)
    # The parser may have fallen back to a differently-named top module
    # (single-module netlist case); pick whichever circuit isn't a cell.
    ref_top = ref_nl.circuit_by_name(args.top) or ref_nl.top_circuit()
    log(f"  parsed: {sum(1 for _ in ref_nl.each_circuit())} circuits, "
        f"top has {ref_top.pin_count()} pins, "
        f"{sum(1 for _ in ref_top.each_subcircuit())} subcircuit instances")

    # CTS-inserted clkload cells have their Y output left dangling
    # (intentional capacitive load) — the Verilog netlist omits the
    # dangling assignment, but the GDS extraction still finds the
    # output pin's metal as a net with one connection. Reconcile by
    # synthesising unique dangling nets on the reference side for every
    # subcircuit-pin that the Verilog parser left disconnected.
    n_dangling = _add_dangling_nets(ref_nl)
    if n_dangling:
        log(f"  added {n_dangling} dangling-net stubs to reference")

    # Save top-circuit names before simplify (simplify may purge or
    # rewrite Circuit objects, invalidating our Python wrapper handles).
    ext_top_name = ext_top.name
    ref_top_name = ref_top.name

    # Macro/stdcell power-pin connectivity check.
    # Walk every subcircuit instance in the extracted top and check that
    # its VDD/VSS pins land on a multi-fanout net (the global power
    # rail). A pin on a 1-fanout net is a PDN bug — the PSM-0069 failure
    # mode at compute_array. The structural compare below CANNOT catch
    # this because simplify() purges single-fanout nets before compare.
    pdn_violations = _check_macro_power(ext_top, log)

    # Both netlists need simplification before compare — without it,
    # NetlistComparer fails to find the isomorphism even on identical
    # netlists, presumably because it can't canonicalise anonymous
    # internal-net IDs across the two sides.
    #
    # Side-effect: simplify() purges single-fanout nets (e.g., dangling
    # macro power pins from a broken PDN — the PSM-0069 failure mode at
    # compute_array). That class of bug is therefore NOT caught here;
    # use scripts/verify_macro_power.tcl alongside this LVS for sign-off
    # on macro power connectivity.
    ext_nl.simplify()
    ref_nl.simplify()
    # Re-resolve top-circuit handles after simplify.
    ext_top = ext_nl.circuit_by_name(ext_top_name)
    ref_top = ref_nl.circuit_by_name(ref_top_name)

    # 3) Compare.
    log("[3/3] Comparing...")
    # KLayout 0.30 crashes intermittently when the comparator drives a
    # Python-subclassed GenericNetlistCompareLogger over hierarchical
    # designs (segfault deep in compare_impl). Until that's fixed
    # upstream, run the comparator with NO logger and post-mortem the
    # netlist objects via unmatched_circuits_{a,b} on failure.
    comparer = pya.NetlistComparer()
    structural_ok = comparer.compare(ext_nl, ref_nl)
    elapsed = time.time() - t_start
    log("")
    # LVS passes only if BOTH the structural compare and the PDN check
    # come back clean. Either one alone could miss real silicon bugs.
    ok = structural_ok and not pdn_violations
    if ok:
        log(f"LVS CLEAN: {args.top} ({elapsed:.1f}s)")
    else:
        reasons = []
        if not structural_ok:
            reasons.append("structural mismatch")
        if pdn_violations:
            reasons.append(f"{len(pdn_violations)} PDN violation(s)")
        log(f"LVS FAIL: {args.top} - {', '.join(reasons)} ({elapsed:.1f}s)")
        if not structural_ok:
            _dump_failure_diagnostics(ext_nl, ref_nl, ext_top, ref_top,
                                      comparer, log)

    # Write report.
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w") as fh:
        fh.write("\n".join(report_lines) + "\n")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
