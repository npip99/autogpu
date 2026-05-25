"""Tests for ir_drop_postprocess.

Run directly:
    pytest tech/asap7/orfs/scripts/tests/test_ir_drop_postprocess.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

from ir_drop_postprocess import (  # noqa: E402
    NetAnalysis,
    analyze_net,
    detect_psm_status,
    format_report,
    main,
    overall_status,
    read_voltage_csv,
)


VDD = 0.70
BUDGET_FRAC = 0.10              # 10 % of VDD
BUDGET_V = VDD * BUDGET_FRAC    # 70 mV


def _csv(tmp: Path, name: str, rows: list[str]) -> Path:
    p = tmp / name
    header = "Instance,Terminal,Layer,X location,Y location,Voltage\n"
    p.write_text(header + "\n".join(rows) + "\n")
    return p


# --- read_voltage_csv ------------------------------------------------------

def test_read_voltage_csv_parses_valid_rows(tmp_path):
    p = _csv(tmp_path, "v.csv", [
        "u_inst/A,VDD,M1,1.0,2.0,0.695",
        "u_other/B,VDD,M2,3.5,4.5,0.690",
    ])
    rows = list(read_voltage_csv(p))
    assert len(rows) == 2
    assert rows[0].instance == "u_inst/A"
    assert rows[0].layer == "M1"
    assert rows[0].x == 1.0
    assert rows[0].voltage == 0.695


def test_read_voltage_csv_skips_malformed(tmp_path):
    p = _csv(tmp_path, "v.csv", [
        "u_inst/A,VDD,M1,1.0,2.0,0.695",
        "too,short,row",
        "u_other/B,VDD,M2,not_a_float,4.5,0.690",
        "u_third/C,VDD,M3,7.0,8.0,0.680",
    ])
    rows = list(read_voltage_csv(p))
    assert len(rows) == 2
    assert {r.instance for r in rows} == {"u_inst/A", "u_third/C"}


# --- analyze_net -----------------------------------------------------------

def test_analyze_net_vdd_pass(tmp_path):
    # Worst node sees 0.695 V → drop = 5 mV, well under 70 mV.
    csv = _csv(tmp_path, "VDD_voltage.csv", [
        "u_a/A,VDD,M1,0,0,0.698",
        "u_b/B,VDD,M1,0,0,0.695",
    ])
    a = analyze_net("VDD", csv, VDD, BUDGET_V, "PSM-0040: grid connected")
    assert a.worst_drop_v == pytest.approx(0.005)
    assert a.worst_pct == pytest.approx(5/700*100)
    assert a.failures == []
    assert a.grid_blocked is False


def test_analyze_net_vdd_fail_lists_instances(tmp_path):
    # u_bad drops 100 mV (over 70 mV budget), u_ok drops only 10 mV.
    csv = _csv(tmp_path, "VDD_voltage.csv", [
        "u_bad/A,VDD,M1,10.0,20.0,0.600",
        "u_ok/B,VDD,M2,30.0,40.0,0.690",
    ])
    a = analyze_net("VDD", csv, VDD, BUDGET_V, "PSM-0040: grid connected")
    assert a.worst_drop_v == pytest.approx(0.100)
    assert len(a.failures) == 1
    drop, name, layer, x, y = a.failures[0]
    assert drop == pytest.approx(0.100)
    assert name == "u_bad/A"
    assert layer == "M1"
    assert (x, y) == (10.0, 20.0)


def test_analyze_net_vdd_aggregates_worst_node_per_instance(tmp_path):
    # u_inst has two nodes; the worst (0.620 V → 80 mV drop) wins.
    csv = _csv(tmp_path, "VDD_voltage.csv", [
        "u_inst/A,VDD,M1,1.0,1.0,0.680",
        "u_inst/A,VDD,M2,1.0,2.0,0.620",
    ])
    a = analyze_net("VDD", csv, VDD, BUDGET_V, "PSM-0040: grid connected")
    assert len(a.failures) == 1
    assert a.failures[0][0] == pytest.approx(0.080)
    assert a.failures[0][2] == "M2"  # worst-node layer


def test_analyze_net_vss_ground_bounce(tmp_path):
    # VSS: drop = V_node (ground rose above 0).  100 mV bounce → fail.
    csv = _csv(tmp_path, "VSS_voltage.csv", [
        "u_a/A,VSS,M1,0,0,0.020",     # 20 mV bounce — pass
        "u_bouncy/G,VSS,M1,5,5,0.100", # 100 mV bounce — fail
    ])
    a = analyze_net("VSS", csv, VDD, BUDGET_V, "PSM-0040: grid connected")
    assert a.worst_drop_v == pytest.approx(0.100)
    assert len(a.failures) == 1
    assert a.failures[0][1] == "u_bouncy/G"


def test_analyze_net_missing_csv_returns_zero(tmp_path):
    a = analyze_net("VDD", tmp_path / "nope.csv", VDD, BUDGET_V, "unknown")
    assert a.worst_drop_v == 0.0
    assert a.failures == []
    assert a.grid_blocked is False


def test_analyze_net_grid_blocked_flag(tmp_path):
    csv = _csv(tmp_path, "VDD_voltage.csv", ["u/A,VDD,M1,0,0,0.695"])
    a = analyze_net("VDD", csv, VDD, BUDGET_V, "153 PSM violation(s)")
    assert a.grid_blocked is True


# --- detect_psm_status -----------------------------------------------------

def test_detect_psm_status_violations(tmp_path):
    err = tmp_path / "VDD_error.rpt"
    err.write_text(
        "violation type: open\n"
        "  instance/foo\n"
        "violation type: open\n"
        "  instance/bar\n"
    )
    s = detect_psm_status(tmp_path, "irrelevant log", "VDD")
    assert s == "2 PSM violation(s)"


def test_detect_psm_status_clean_grid(tmp_path):
    s = detect_psm_status(
        tmp_path, "[INFO PSM-0040] All shapes on net VDD are connected", "VDD"
    )
    assert s == "PSM-0040: grid connected"


def test_detect_psm_status_unknown(tmp_path):
    assert detect_psm_status(tmp_path, "nothing here", "VDD") == "unknown"


def test_detect_psm_status_empty_error_file_treated_as_clean_if_psm0040(tmp_path):
    (tmp_path / "VDD_error.rpt").write_text("")
    s = detect_psm_status(
        tmp_path, "[INFO PSM-0040] All shapes on net VDD are connected", "VDD"
    )
    assert s == "PSM-0040: grid connected"


# --- overall_status --------------------------------------------------------

def _na(net="VDD", drop=0.0, status="PSM-0040: grid connected", blocked=False):
    return NetAnalysis(net, drop, drop / VDD * 100, status, blocked, [])


def test_overall_status_pass():
    a = [_na("VDD", 0.005), _na("VSS", 0.010)]
    assert overall_status(a, BUDGET_V) == "PASS"


def test_overall_status_fail():
    a = [_na("VDD", 0.100), _na("VSS", 0.010)]
    assert overall_status(a, BUDGET_V) == "FAIL"


def test_overall_status_blocked_wins_over_fail():
    a = [_na("VDD", 0.100, "12 PSM violation(s)", blocked=True),
         _na("VSS", 0.010)]
    assert overall_status(a, BUDGET_V) == "BLOCKED"


def test_overall_status_blocked_on_missing_data():
    # VDD has data, VSS is missing (drop=0 AND psm status not PSM-0040).
    a = [_na("VDD", 0.005, "PSM-0040: grid connected"),
         _na("VSS", 0.0, "unknown")]
    assert overall_status(a, BUDGET_V) == "BLOCKED"


# --- format_report end-to-end ---------------------------------------------

def test_format_report_pass():
    a = [_na("VDD", 0.005), _na("VSS", 0.010)]
    out = format_report("foo", VDD, BUDGET_FRAC, "0.10", a, "PASS")
    assert "OVERALL      : PASS" in out
    assert "SUMMARY: module=foo" in out
    assert "status=PASS" in out
    # No "Failing instances" or BLOCKED note on a pass.
    assert "Failing instances" not in out
    assert "BLOCKED — grid" not in out


def test_format_report_fail_lists_failures_by_net(tmp_path):
    # Build NetAnalyses with non-empty failures lists, exercising the
    # merged-and-sorted list logic across both nets.
    vdd_fails = [(0.100, "u_vdd_bad", "M1", 1.0, 1.0)]
    vss_fails = [(0.090, "u_vss_bad", "M2", 2.0, 2.0)]
    a = [
        NetAnalysis("VDD", 0.100, 14.29, "PSM-0040: grid connected", False, vdd_fails),
        NetAnalysis("VSS", 0.090, 12.86, "PSM-0040: grid connected", False, vss_fails),
    ]
    out = format_report("foo", VDD, BUDGET_FRAC, "0.10", a, "FAIL")
    assert "Failing instances (2)" in out
    # VDD listed first (100 mV > 90 mV).
    vdd_idx = out.index("[VDD]")
    vss_idx = out.index("[VSS]")
    assert vdd_idx < vss_idx
    assert "u_vdd_bad" in out
    assert "u_vss_bad" in out


def test_format_report_blocked_emits_a1_hint():
    a = [_na("VDD", 0.0, "153 PSM violation(s)", blocked=True),
         _na("VSS", 0.0, "161 PSM violation(s)", blocked=True)]
    out = format_report("foo", VDD, BUDGET_FRAC, "0.10", a, "BLOCKED")
    assert "BLOCKED — grid connectivity errors" in out
    assert "see A1" in out


# --- main() ----------------------------------------------------------------

def _setup_main_inputs(tmp_path, vdd_rows, vss_rows, log_text=""):
    rep_dir = tmp_path / "rep"
    rep_dir.mkdir()
    _csv(rep_dir, "VDD_voltage.csv", vdd_rows)
    _csv(rep_dir, "VSS_voltage.csv", vss_rows)
    log = tmp_path / "openroad.log"
    log.write_text(log_text)
    out_log = tmp_path / "ir_drop.log"
    argv = [
        "ir_drop_postprocess.py",
        str(log), str(rep_dir), "foo",
        str(VDD), str(BUDGET_FRAC), "0.10",
        str(out_log),
    ]
    return argv, out_log


def test_main_pass(tmp_path):
    argv, out_log = _setup_main_inputs(
        tmp_path,
        vdd_rows=["u_a/A,VDD,M1,0,0,0.695"],
        vss_rows=["u_a/A,VSS,M1,0,0,0.005"],
        log_text=(
            "[INFO PSM-0040] All shapes on net VDD are connected\n"
            "[INFO PSM-0040] All shapes on net VSS are connected\n"
        ),
    )
    rc = main(argv)
    assert rc == 0
    assert "status=PASS" in out_log.read_text()


def test_main_fail(tmp_path):
    argv, out_log = _setup_main_inputs(
        tmp_path,
        vdd_rows=["u_bad/A,VDD,M1,1.0,1.0,0.600"],
        vss_rows=["u_a/A,VSS,M1,0,0,0.005"],
        log_text=(
            "[INFO PSM-0040] All shapes on net VDD are connected\n"
            "[INFO PSM-0040] All shapes on net VSS are connected\n"
        ),
    )
    rc = main(argv)
    assert rc == 1
    body = out_log.read_text()
    assert "status=FAIL" in body
    assert "u_bad/A" in body


def test_main_blocked_on_violations(tmp_path):
    rep_dir = tmp_path / "rep"
    rep_dir.mkdir()
    _csv(rep_dir, "VDD_voltage.csv", ["u_a/A,VDD,M1,0,0,0.695"])
    _csv(rep_dir, "VSS_voltage.csv", ["u_a/A,VSS,M1,0,0,0.005"])
    # Inject PSM violations on VDD via the error file.
    (rep_dir / "VDD_error.rpt").write_text(
        "violation type: open\n  u_a/A\n"
    )
    log = tmp_path / "openroad.log"
    log.write_text("")
    out_log = tmp_path / "ir_drop.log"
    argv = [
        "ir_drop_postprocess.py",
        str(log), str(rep_dir), "foo",
        str(VDD), str(BUDGET_FRAC), "0.10", str(out_log),
    ]
    rc = main(argv)
    assert rc == 2
    assert "status=BLOCKED" in out_log.read_text()
