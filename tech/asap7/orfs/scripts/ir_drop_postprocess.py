"""Post-process OpenROAD psm IR-drop output into a sign-off report.

Inputs (positional args):
    1. openroad stdout log (path)
    2. reports directory (contains {VDD,VSS}_voltage.csv and _error.rpt)
    3. module name
    4. VDD nominal voltage (V)
    5. budget fraction of VDD (e.g. 0.10 = 10%)
    6. activity factor (used in summary line)
    7. output log path

Exit codes:
    0 = PASS
    1 = FAIL (worst Vdrop above budget)
    2 = BLOCKED (grid connectivity errors, e.g. PSM-0069)

Worst-case Vdrop is computed from the per-node voltage CSVs that psm writes
(authoritative), not by regex-parsing psm's stdout (version-fragile). The
stdout log is used only to detect PSM-0040 ("grid connected") status when
no error file exists.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NodeVoltage:
    instance: str
    layer: str
    x: float
    y: float
    voltage: float


@dataclass(frozen=True)
class NetAnalysis:
    net: str
    worst_drop_v: float
    worst_pct: float
    psm_status: str            # human-readable connectivity status
    grid_blocked: bool         # True iff psm reported violations on this net
    failures: list             # list[(drop_v, instance, layer, x, y)] above budget


EXPECTED_CSV_HEADER = "Instance,Terminal,Layer,X location,Y location,Voltage"


def read_voltage_csv(path: Path):
    """Yield NodeVoltage records from a psm voltage CSV. Skip malformed lines.

    Header: Instance,Terminal,Layer,X location,Y location,Voltage

    Raises ValueError if the header doesn't match — column order is hardcoded
    in NodeVoltage construction below, so a psm format change must trip here
    rather than silently produce nonsense voltages.
    """
    with open(path) as f:
        header = f.readline().strip()
        if header != EXPECTED_CSV_HEADER:
            raise ValueError(
                f"unexpected psm CSV header at {path}: {header!r} "
                f"(expected {EXPECTED_CSV_HEADER!r})"
            )
        for line in f:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            try:
                yield NodeVoltage(
                    instance=parts[0],
                    layer=parts[2],
                    x=float(parts[3]),
                    y=float(parts[4]),
                    voltage=float(parts[5]),
                )
            except ValueError:
                continue


def analyze_net(net: str, csv_path: Path, vdd: float, budget_v: float,
                psm_status: str) -> NetAnalysis:
    """Compute worst Vdrop + per-instance failures from a psm voltage CSV.

    For VDD: drop = VDD_nominal - V_node (rail sagged below nominal).
    For VSS: drop = V_node - 0 (ground bounced above nominal 0 V).
    Either way, drop is the magnitude of supply deviation at the node.
    """
    grid_blocked = "violation" in psm_status

    if not csv_path.exists():
        return NetAnalysis(net, 0.0, 0.0, psm_status, grid_blocked, [])

    def drop_of(v: float) -> float:
        return (vdd - v) if net == "VDD" else v

    worst_per_inst: dict[str, tuple[float, str, float, float]] = {}
    worst_global = 0.0
    for n in read_voltage_csv(csv_path):
        d = drop_of(n.voltage)
        if d > worst_global:
            worst_global = d
        prev = worst_per_inst.get(n.instance)
        if prev is None or d > prev[0]:
            worst_per_inst[n.instance] = (d, n.layer, n.x, n.y)

    failures = sorted(
        ((d, name, lyr, x, y)
         for name, (d, lyr, x, y) in worst_per_inst.items()
         if d > budget_v),
        reverse=True,
    )
    worst_pct = (worst_global / vdd * 100.0) if vdd > 0 else 0.0
    return NetAnalysis(net, worst_global, worst_pct, psm_status, grid_blocked, failures)


def detect_psm_status(rep_dir: Path, log_text: str, net: str) -> str:
    """Classify the per-net connectivity status from psm artifacts.

    psm writes <net>_error.rpt only when violations exist. A clean grid
    leaves the file unwritten but emits PSM-0040 in the log.
    """
    err = rep_dir / f"{net}_error.rpt"
    if err.exists() and err.stat().st_size > 0:
        body = err.read_text()
        n = sum(1 for ln in body.splitlines() if "violation type" in ln)
        return f"{n} PSM violation(s)"
    # Regex assumes psm's clean-grid message format:
    #   "[INFO PSM-0040] All shapes on net VDD are connected."
    # If a future OpenROAD changes the wording (e.g. quotes the net name
    # like `net "VDD"`), this won't match and overall_status will flag the
    # run BLOCKED. If you upgrade OpenROAD and every IR run goes BLOCKED,
    # check the actual PSM-0040 wording in ir_drop.openroad.log first.
    if re.search(rf"PSM-0040.*net {net}\b", log_text):
        return "PSM-0040: grid connected"
    return "unknown"


def overall_status(net_analyses: list[NetAnalysis], budget_v: float) -> str:
    """Reduce per-net analyses to a single PASS / FAIL / BLOCKED verdict.

    BLOCKED wins over FAIL wins over PASS. A net with no CSV data
    (worst_drop_v == 0.0 AND status != "PSM-0040: grid connected") is
    treated as BLOCKED — psm didn't produce a meaningful answer.
    """
    blocked = any(a.grid_blocked for a in net_analyses)
    if not blocked:
        for a in net_analyses:
            if a.worst_drop_v == 0.0 and "PSM-0040" not in a.psm_status:
                blocked = True
                break
    if blocked:
        return "BLOCKED"
    if any(a.worst_drop_v > budget_v for a in net_analyses):
        return "FAIL"
    return "PASS"


def format_report(module: str, vdd: float, budget_frac: float,
                  activity: str, net_analyses: list[NetAnalysis],
                  overall: str) -> str:
    budget_v = vdd * budget_frac
    worst_v = max((a.worst_drop_v for a in net_analyses), default=0.0)
    worst_pct = max((a.worst_pct for a in net_analyses), default=0.0)

    lines = [
        "=" * 70,
        "IR-drop sign-off report",
        "=" * 70,
        f"Module       : {module}",
        f"VDD nominal  : {vdd} V (asap7 typical corner)",
        f"Budget       : {budget_frac*100:.1f}% of VDD = {budget_v*1000:.2f} mV",
        f"Activity     : {activity} (global switching-activity factor, "
        f"set_power_activity -global -activity {activity})",
        "Tool         : OpenROAD psm (analyze_power_grid)",
        "",
    ]
    for a in net_analyses:
        if a.worst_drop_v == 0.0 and "PSM-0040" not in a.psm_status:
            lines.append(f"{a.net}: NO IR REPORT (psm: {a.psm_status})")
            continue
        status = "PASS" if a.worst_drop_v <= budget_v else "FAIL"
        lines.append(
            f"{a.net}: worst Vdrop = {a.worst_drop_v*1000:.3f} mV "
            f"({a.worst_pct:.2f}%)   psm: {a.psm_status}   {status}"
        )

    lines += [
        "",
        f"WORST Vdrop  : {worst_v*1000:.3f} mV  ({worst_pct:.2f}%)",
        f"OVERALL      : {overall}",
        "",
    ]

    if overall == "FAIL":
        merged = []
        for a in net_analyses:
            for drop, inst, lyr, x, y in a.failures:
                merged.append((drop, a.net, inst, lyr, x, y))
        merged.sort(reverse=True)
        lines.append(f"Failing instances ({len(merged)}); worst 10 by Vdrop:")
        for drop, net, inst, lyr, x, y in merged[:10]:
            lines.append(
                f"  [{net}] Vdrop={drop*1000:.2f}mV  {inst}  "
                f"({x:.2f}, {y:.2f}) on {lyr}"
            )
        lines.append("")
    elif overall == "BLOCKED":
        lines.append(
            "BLOCKED — grid connectivity errors (likely PSM-0069). "
            "Fix PDN (see A1) before IR-drop sign-off can complete."
        )
        lines.append("")

    # Emit both mV and % so the worst_*/budget_* pair stays in matched
    # units regardless of which the reader compares against.
    lines.append(
        f"SUMMARY: module={module} "
        f"worst_vdrop={worst_v*1000:.3f}mV worst_pct={worst_pct:.3f}% "
        f"budget_mV={budget_v*1000:.2f} budget_pct={budget_frac*100:.3f}% "
        f"activity={activity} status={overall}"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 8:
        print(__doc__, file=sys.stderr)
        return 2
    (_, log_path, rep_dir_s, module, vdd_s, budget_frac_s,
     activity, out_log) = argv
    rep_dir = Path(rep_dir_s)
    vdd = float(vdd_s)
    budget_frac = float(budget_frac_s)
    budget_v = vdd * budget_frac

    log_text = Path(log_path).read_text()
    analyses = []
    for net in ("VDD", "VSS"):
        psm = detect_psm_status(rep_dir, log_text, net)
        analyses.append(
            analyze_net(net, rep_dir / f"{net}_voltage.csv", vdd, budget_v, psm)
        )

    overall = overall_status(analyses, budget_v)
    report = format_report(module, vdd, budget_frac, activity, analyses, overall)
    Path(out_log).write_text(report)
    sys.stdout.write(report)
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[overall]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
