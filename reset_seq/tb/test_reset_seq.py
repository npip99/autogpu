"""
cocotb testbench for reset_seq.sv.

Mirrors pymodel/tests/test_reset_seq.py against the SV implementation, plus
a random-vs-pymodel test that stochastically toggles reset_in.

We don't go through common.tb_utils.reset() here because the DUT IS the
reset sequencer — there's no external `reset` port to gate. We drive
`reset_in` directly per the FSM's API.
"""

import random

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, step_and_compare
from pymodel.reset_seq import NUM_WORDS_PER_BANK, ResetSeq


async def _drive_reset_in(dut, value: int) -> None:
    dut.reset_in.value = value


async def _snap(dut) -> dict:
    await ReadOnly()
    out = {
        "chip_in_reset": int(dut.chip_in_reset.value),
        "smem_scrub_en": int(dut.smem_scrub_en.value),
        "smem_scrub_addr": int(dut.smem_scrub_addr.value),
        "tmem_scrub_en": int(dut.tmem_scrub_en.value),
        "scrub_done": int(dut.scrub_done.value),
    }
    await NextTimeStep()
    return out


@cocotb.test()
async def test_holds_in_reset_during_scrub(dut):
    """chip_in_reset stays high for every cycle of the scrub window."""
    await start_clock(dut)
    dut.reset_in.value = 1
    # Hold reset_in=1 for 3 cycles.
    for _ in range(3):
        await RisingEdge(dut.clk)
        out = await _snap(dut)
        assert out["chip_in_reset"] == 1
        assert out["smem_scrub_en"] == 0
        assert out["scrub_done"] == 0

    # Release reset_in; scrub runs.
    dut.reset_in.value = 0
    for cycle in range(NUM_WORDS_PER_BANK):
        await RisingEdge(dut.clk)
        out = await _snap(dut)
        assert out["chip_in_reset"] == 1, f"cycle {cycle}: chip_in_reset dropped early"
        assert out["smem_scrub_en"] == 1, f"cycle {cycle}: smem_scrub_en should be high"
        assert out["smem_scrub_addr"] == cycle


@cocotb.test()
async def test_releases_after_scrub_complete(dut):
    """chip_in_reset goes low exactly after NUM_WORDS_PER_BANK scrub cycles."""
    await start_clock(dut)
    dut.reset_in.value = 1
    await RisingEdge(dut.clk)
    await _snap(dut)

    # Walk the scrub.
    dut.reset_in.value = 0
    for cycle in range(NUM_WORDS_PER_BANK):
        await RisingEdge(dut.clk)
        out = await _snap(dut)
        assert out["chip_in_reset"] == 1
        assert out["smem_scrub_en"] == 1
        assert out["smem_scrub_addr"] == cycle
        assert out["scrub_done"] == 0

    # One more cycle: transition to RUN.
    await RisingEdge(dut.clk)
    out = await _snap(dut)
    assert out["chip_in_reset"] == 0
    assert out["smem_scrub_en"] == 0
    assert out["scrub_done"] == 1


@cocotb.test()
async def test_reset_in_reasserts_during_scrub(dut):
    """Re-asserting reset_in mid-scrub restarts the FSM at addr=0."""
    await start_clock(dut)
    dut.reset_in.value = 1
    await RisingEdge(dut.clk)
    await _snap(dut)

    # Advance partway through the scrub.
    dut.reset_in.value = 0
    for _ in range(NUM_WORDS_PER_BANK // 2):
        await RisingEdge(dut.clk)
        await _snap(dut)

    # Re-assert reset_in.
    dut.reset_in.value = 1
    await RisingEdge(dut.clk)
    out = await _snap(dut)
    assert out["chip_in_reset"] == 1
    assert out["smem_scrub_en"] == 0
    assert out["smem_scrub_addr"] == 0

    # Release; scrub_addr should walk 0..depth-1 fresh.
    dut.reset_in.value = 0
    seen = []
    for _ in range(NUM_WORDS_PER_BANK):
        await RisingEdge(dut.clk)
        out = await _snap(dut)
        if out["smem_scrub_en"] == 1:
            seen.append(out["smem_scrub_addr"])
    assert seen == list(range(NUM_WORDS_PER_BANK)), (
        f"expected fresh walk 0..{NUM_WORDS_PER_BANK - 1}, got {seen}"
    )


@cocotb.test()
async def test_scrub_writes_each_smem_addr_exactly_once(dut):
    """smem_scrub_addr sequence covers 0..depth-1 in order."""
    await start_clock(dut)
    dut.reset_in.value = 1
    await RisingEdge(dut.clk)
    await _snap(dut)

    dut.reset_in.value = 0
    seen = []
    for _ in range(NUM_WORDS_PER_BANK + 5):
        await RisingEdge(dut.clk)
        out = await _snap(dut)
        if out["smem_scrub_en"] == 1:
            seen.append(out["smem_scrub_addr"])
    assert seen == list(range(NUM_WORDS_PER_BANK)), (
        f"smem_scrub_addr sequence wrong: {seen}"
    )


@cocotb.test()
async def test_tmem_scrub_one_cycle(dut):
    """tmem_scrub_en pulses for exactly one cycle per reset event."""
    await start_clock(dut)
    dut.reset_in.value = 1
    await RisingEdge(dut.clk)
    await _snap(dut)

    dut.reset_in.value = 0
    pulses = []
    for _ in range(NUM_WORDS_PER_BANK + 5):
        await RisingEdge(dut.clk)
        out = await _snap(dut)
        pulses.append(out["tmem_scrub_en"])
    assert sum(pulses) == 1, f"expected exactly one pulse, got {sum(pulses)}: {pulses}"
    assert pulses[0] == 1


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """Randomly toggle reset_in across ~600 cycles; compare every output cycle-by-cycle."""
    await start_clock(dut)
    # Cold start: drive reset_in=1 to align SV and pymodel initial states.
    dut.reset_in.value = 1
    await RisingEdge(dut.clk)
    # Skip the first cycle's compare; we just want both to be settled.
    await _snap(dut)

    py = ResetSeq()
    py.tick(reset_in=1)

    rng = random.Random(0xCAFE)
    outputs = ["chip_in_reset", "smem_scrub_en", "smem_scrub_addr",
               "tmem_scrub_en", "scrub_done"]

    # Mix reset_in patterns:
    #   - 5% chance of asserting reset_in each cycle.
    #   - Otherwise leave at 0 so most cycles let the scrub progress / RUN.
    for cycle in range(600):
        reset_in = 1 if rng.random() < 0.05 else 0
        inputs = {"reset_in": reset_in}
        await step_and_compare(dut, py, inputs, outputs)
