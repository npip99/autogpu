"""
cocotb testbench for barrier.sv.

Drives barrier.sv and pymodel.barrier.Barrier in lockstep and asserts equality
of every per-bar state field (pending, expected, tx_pending, phase) every
cycle. Also exercises the combinational wait_done output.

Tests:
  1. test_directed_priority_cases — directed scenarios for each priority
     channel (INIT / ADD_TX / SUB_TX / ARRIVE / FLIP), plus INIT+ARRIVE on the
     same bar (INIT wins) and dual-arrive on the same bar (pending -= 2).
  2. test_random_vs_pymodel — ~500 random cycles, comparing per-bar state
     against the pymodel. Per-bar state is exposed by the SV as packed signals
     (bars_pending / bars_expected / bars_tx_pending / bars_phase); we slice
     them and compare per-bar. wait_query is also checked combinationally each
     cycle.

Note: we don't use step_and_compare here because per-bar state is structured
(NUM_BARRIERS fields per signal). The compare-loop below slices each packed
signal manually.
"""

import random

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, reset
from config import NUM_BARRIERS
from pymodel.barrier import Barrier


# All input-port defaults — set before reset so X's don't propagate.
INPUT_DEFAULTS = {
    "init_en": 0,
    "init_bar_id": 0,
    "init_count": 0,
    "arrive_en_a": 0,
    "arrive_bar_id_a": 0,
    "arrive_en_b": 0,
    "arrive_bar_id_b": 0,
    "add_tx_en": 0,
    "add_tx_bar_id": 0,
    "add_tx_bytes": 0,
    "sub_tx_en": 0,
    "sub_tx_bar_id": 0,
    "sub_tx_bytes": 0,
    "query_bar_id": 0,
    "query_expected_phase": 0,
}


def _drive_defaults(dut) -> None:
    for name, val in INPUT_DEFAULTS.items():
        getattr(dut, name).value = val


def _drive(dut, inputs: dict) -> None:
    """Drive inputs from a partial dict; missing fields get defaults."""
    merged = dict(INPUT_DEFAULTS)
    merged.update(inputs)
    for name, val in merged.items():
        getattr(dut, name).value = val


def _slice(packed: int, idx: int, width: int) -> int:
    """Extract field `idx` of width `width` bits from a packed int."""
    return (packed >> (idx * width)) & ((1 << width) - 1)


def _sv_bar(dut, idx: int) -> tuple[int, int, int, int]:
    """Return (pending, expected, tx_pending, phase) for bar `idx` from SV."""
    bp = int(dut.bars_pending.value)
    be = int(dut.bars_expected.value)
    bt = int(dut.bars_tx_pending.value)
    ph = int(dut.bars_phase.value)
    return (
        _slice(bp, idx, 16),
        _slice(be, idx, 16),
        _slice(bt, idx, 32),
        (ph >> idx) & 1,
    )


def _compare_all_bars(dut, py: Barrier, cycle: int, inputs: dict) -> None:
    """Assert every bar's (pending, expected, tx_pending, phase) matches pymodel."""
    for i in range(NUM_BARRIERS):
        sv = _sv_bar(dut, i)
        b = py.bars[i]
        expected = (b.pending, b.expected, b.tx_pending, b.phase)
        assert sv == expected, (
            f"cycle {cycle}: bar {i} mismatch\n"
            f"  sv (pending, expected, tx_pending, phase) = {sv}\n"
            f"  py                                         = {expected}\n"
            f"  inputs = {inputs}"
        )


# ----------------------------------------------------------------------------
# Test 1: directed priority cases
# ----------------------------------------------------------------------------

@cocotb.test()
async def test_directed_priority_cases(dut):
    """Exercise each priority channel and the cross-channel rules."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)

    py = Barrier()

    # All bars start zeroed (after reset).
    await ReadOnly()
    _compare_all_bars(dut, py, cycle=-1, inputs={})
    await NextTimeStep()

    # Helper: drive `inputs` for one cycle, tick pymodel, compare.
    async def step(inputs: dict, cycle: int) -> None:
        _drive(dut, inputs)
        await RisingEdge(dut.clk)
        py.tick(**{k: v for k, v in inputs.items() if k in (
            "init_en", "init_bar_id", "init_count",
            "arrive_en_a", "arrive_bar_id_a",
            "arrive_en_b", "arrive_bar_id_b",
            "add_tx_en", "add_tx_bar_id", "add_tx_bytes",
            "sub_tx_en", "sub_tx_bar_id", "sub_tx_bytes",
        )})
        await ReadOnly()
        _compare_all_bars(dut, py, cycle, inputs)
        await NextTimeStep()

    # 1) INIT on bar 0 with count=2.
    await step({"init_en": 1, "init_bar_id": 0, "init_count": 2}, cycle=0)
    # Confirm pymodel state for sanity (mirrors test_barrier.py).
    assert py.bars[0].pending == 2 and py.bars[0].expected == 2
    assert py.bars[0].phase == 0

    # 2) ARRIVE on bar 0 — pending 2 -> 1, no flip yet.
    await step({"arrive_en_a": 1, "arrive_bar_id_a": 0}, cycle=1)
    assert py.bars[0].pending == 1 and py.bars[0].phase == 0

    # 3) ARRIVE on bar 0 again — pending 1 -> 0, FLIP, reload to 2.
    await step({"arrive_en_a": 1, "arrive_bar_id_a": 0}, cycle=2)
    assert py.bars[0].phase == 1 and py.bars[0].pending == 2

    # 4) ADD_TX on bar 1 (no init yet; default pending=0).
    #    Pending stays 0; tx_pending grows; no flip (no decrement event).
    await step({"add_tx_en": 1, "add_tx_bar_id": 1, "add_tx_bytes": 1024}, cycle=3)
    assert py.bars[1].tx_pending == 1024 and py.bars[1].phase == 0

    # 5) SUB_TX on bar 1 — tx_pending 1024 -> 0, pending already 0 -> FLIP.
    await step({"sub_tx_en": 1, "sub_tx_bar_id": 1, "sub_tx_bytes": 1024}, cycle=4)
    assert py.bars[1].tx_pending == 0 and py.bars[1].phase == 1

    # 6) Dual ARRIVE on same bar (bar 2). First INIT(2).
    await step({"init_en": 1, "init_bar_id": 2, "init_count": 2}, cycle=5)
    await step({
        "arrive_en_a": 1, "arrive_bar_id_a": 2,
        "arrive_en_b": 1, "arrive_bar_id_b": 2,
    }, cycle=6)
    # pending 2 -> 0 in one cycle, flip, reload to 2.
    assert py.bars[2].phase == 1 and py.bars[2].pending == 2

    # 7) INIT+ARRIVE same cycle on bar 3. INIT must dominate (arrive dropped).
    #    Result: pending=3, expected=3, phase=0 (no flip from arrive).
    await step({"init_en": 1, "init_bar_id": 3, "init_count": 5}, cycle=7)
    await step({
        "init_en": 1, "init_bar_id": 3, "init_count": 3,
        "arrive_en_a": 1, "arrive_bar_id_a": 3,
    }, cycle=8)
    assert py.bars[3].pending == 3 and py.bars[3].expected == 3
    assert py.bars[3].phase == 0

    # 8) wait_query combinational check: bar 0 phase=1; expected=0 → wait_done=1.
    dut.query_bar_id.value = 0
    dut.query_expected_phase.value = 0
    await NextTimeStep()
    await ReadOnly()
    sv_wait = int(dut.wait_done.value)
    py_wait = py.wait_query(0, expected_phase=0)
    assert sv_wait == py_wait == 1, f"wait_done: sv={sv_wait} py={py_wait}"
    await NextTimeStep()

    # And with expected_phase=1, wait_done should be 0.
    dut.query_expected_phase.value = 1
    await NextTimeStep()
    await ReadOnly()
    sv_wait = int(dut.wait_done.value)
    py_wait = py.wait_query(0, expected_phase=1)
    assert sv_wait == py_wait == 0, f"wait_done: sv={sv_wait} py={py_wait}"


# ----------------------------------------------------------------------------
# Test 2: random vs pymodel
# ----------------------------------------------------------------------------

@cocotb.test()
async def test_random_vs_pymodel(dut):
    """Random stimulus for ~500 cycles; compare every bar's state vs pymodel."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)

    py = Barrier()
    rng = random.Random(0xBA77E5)

    NUM_CYCLES = 500

    for cycle in range(NUM_CYCLES):
        # Build a random set of inputs.
        init_en = 1 if rng.random() < 0.10 else 0
        init_bar_id = rng.randrange(NUM_BARRIERS) if init_en else 0
        # Keep counts modest so we exercise flip transitions often.
        init_count = rng.randrange(0, 4) if init_en else 0

        # ARRIVE channels: independent, may target any bar (incl. the same one).
        arrive_en_a = 1 if rng.random() < 0.30 else 0
        arrive_bar_id_a = rng.randrange(NUM_BARRIERS) if arrive_en_a else 0
        arrive_en_b = 1 if rng.random() < 0.30 else 0
        arrive_bar_id_b = rng.randrange(NUM_BARRIERS) if arrive_en_b else 0

        # ADD_TX / SUB_TX. Must not underflow tx_pending on the pymodel side
        # (pymodel asserts). We use the *current* pymodel state to choose a
        # legal sub_tx_bytes — which is the same state the SV sees this cycle.
        add_tx_en = 1 if rng.random() < 0.20 else 0
        add_tx_bar_id = rng.randrange(NUM_BARRIERS) if add_tx_en else 0
        add_tx_bytes = rng.randrange(1, 65) if add_tx_en else 0

        # For SUB_TX, only choose bars with non-zero tx_pending AND ensure we
        # don't subtract more than what's there. If INIT targets that bar, the
        # SUB_TX is dropped by both pymodel and SV — so no underflow concern.
        candidate_sub_bars = [
            b for b in range(NUM_BARRIERS) if py.bars[b].tx_pending > 0
        ]
        sub_tx_en = 1 if (candidate_sub_bars and rng.random() < 0.30) else 0
        if sub_tx_en:
            sub_tx_bar_id = rng.choice(candidate_sub_bars)
            sub_tx_bytes = rng.randrange(1, py.bars[sub_tx_bar_id].tx_pending + 1)
        else:
            sub_tx_bar_id = 0
            sub_tx_bytes = 0

        # ARRIVE underflow guard: pending must not go below 0. We need to
        # ensure that for each bar (a) not targeted by INIT this cycle, the
        # number of arrives on it this cycle does not exceed its current
        # pending. (INIT-targeted bars: arrive is dropped → no underflow.)
        arrives_per_bar: dict[int, int] = {}
        if arrive_en_a and not (init_en and init_bar_id == arrive_bar_id_a):
            arrives_per_bar[arrive_bar_id_a] = (
                arrives_per_bar.get(arrive_bar_id_a, 0) + 1
            )
        if arrive_en_b and not (init_en and init_bar_id == arrive_bar_id_b):
            arrives_per_bar[arrive_bar_id_b] = (
                arrives_per_bar.get(arrive_bar_id_b, 0) + 1
            )
        # If any bar would underflow, suppress the offending channels.
        for bar_id, n in list(arrives_per_bar.items()):
            if py.bars[bar_id].pending < n:
                # Drop channel a if it targets this bar.
                if arrive_en_a and arrive_bar_id_a == bar_id and not (
                        init_en and init_bar_id == bar_id):
                    arrive_en_a = 0
                    arrive_bar_id_a = 0
                # Drop channel b if it targets this bar.
                if arrive_en_b and arrive_bar_id_b == bar_id and not (
                        init_en and init_bar_id == bar_id):
                    arrive_en_b = 0
                    arrive_bar_id_b = 0

        inputs = {
            "init_en": init_en,
            "init_bar_id": init_bar_id,
            "init_count": init_count,
            "arrive_en_a": arrive_en_a,
            "arrive_bar_id_a": arrive_bar_id_a,
            "arrive_en_b": arrive_en_b,
            "arrive_bar_id_b": arrive_bar_id_b,
            "add_tx_en": add_tx_en,
            "add_tx_bar_id": add_tx_bar_id,
            "add_tx_bytes": add_tx_bytes,
            "sub_tx_en": sub_tx_en,
            "sub_tx_bar_id": sub_tx_bar_id,
            "sub_tx_bytes": sub_tx_bytes,
        }

        # Also drive a random wait_query each cycle (combinational).
        query_bar_id = rng.randrange(NUM_BARRIERS)
        query_expected_phase = rng.randint(0, 1)

        # Drive all inputs.
        _drive(dut, inputs)
        dut.query_bar_id.value = query_bar_id
        dut.query_expected_phase.value = query_expected_phase

        await RisingEdge(dut.clk)
        py.tick(**inputs)

        await ReadOnly()
        _compare_all_bars(dut, py, cycle, inputs)

        sv_wait = int(dut.wait_done.value)
        py_wait = py.wait_query(query_bar_id, expected_phase=query_expected_phase)
        assert sv_wait == py_wait, (
            f"cycle {cycle}: wait_done mismatch sv={sv_wait} py={py_wait} "
            f"query_bar={query_bar_id} expected_phase={query_expected_phase}"
        )

        await NextTimeStep()
