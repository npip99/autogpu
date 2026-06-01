"""
cocotb testbench for mac_tmem_cell.sv (Phase 7i systolic leaf).

Drives mac_tmem_cell.sv and pymodel.mac_tmem_cell.MacTmemCell in lockstep.
Asserts equality on:
  - drain_data (registered output)
  - storage[*] (back-door observation each cycle)
  - {compute,a,b,slot,accum}_out (pipeline pass-through registers)
"""

import random

import cocotb
import numpy as np
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, reset
from config import TMEM_SLOTS
from golden.fp8 import encode_e4m3
from pymodel.mac_tmem_cell import MacTmemCell


def _fp32_bits(x: float) -> int:
    return int(np.array([np.float32(x)], dtype=np.float32).view(np.uint32)[0])


def _encode_one(x: float) -> int:
    return int(encode_e4m3(np.array([np.float32(x)], dtype=np.float32))[0])


async def _drive_defaults(dut) -> None:
    dut.compute_in.value = 0
    dut.a_in.value = 0
    dut.b_in.value = 0
    dut.slot_in.value = 0
    dut.accum_in.value = 0
    dut.drain_en_w.value = 0
    dut.drain_slot_w.value = 0
    dut.drain_in.value = 0
    dut.init_en.value = 0
    dut.init_slot.value = 0
    dut.init_data.value = 0
    dut.scrub_en_w.value = 0


def _read_storage(dut, n_slots: int) -> list[int]:
    return [int(dut.storage[s].value) & 0xFFFFFFFF for s in range(n_slots)]


def _py_storage_bits(py: MacTmemCell) -> list[int]:
    arr = py.storage.astype(np.float32).view(np.uint32)
    return [int(x) for x in arr]


def _check_pipe_out(dut, py: MacTmemCell, cyc: int, inputs: dict) -> None:
    sv = (
        int(dut.compute_out.value),
        int(dut.a_out.value),
        int(dut.b_out.value),
        int(dut.slot_out.value),
        int(dut.accum_out.value),
    )
    p = (py.compute_out, py.a_out, py.b_out, py.slot_out, py.accum_out)
    assert sv == p, f"cycle {cyc}: pipe-out mismatch sv={sv} py={p} inputs={inputs}"


@cocotb.test()
async def test_directed(dut):
    """Canned operations with known values, exact match to pymodel."""
    await start_clock(dut, signal_name="clk_w")
    await _drive_defaults(dut)
    await reset(dut, signal_name="reset_w", clock_name="clk_w")

    py = MacTmemCell(n_slots=TMEM_SLOTS)

    # --- Cycle 1: init slot 0 to 7.5 ---
    seed_bits = _fp32_bits(7.5)
    dut.init_en.value = 1
    dut.init_slot.value = 0
    dut.init_data.value = seed_bits
    await RisingEdge(dut.clk_w)
    py.tick(init_en=1, init_slot=0, init_data=seed_bits)
    await ReadOnly()
    assert _read_storage(dut, TMEM_SLOTS) == _py_storage_bits(py), (
        "storage mismatch after init"
    )
    await NextTimeStep()

    # --- Cycle 2: compute_in accum_in=1 on slot 0 ---
    a = _encode_one(2.0)
    b = _encode_one(3.0)
    dut.init_en.value = 0
    dut.init_slot.value = 0
    dut.init_data.value = 0
    dut.compute_in.value = 1
    dut.a_in.value = a
    dut.b_in.value = b
    dut.slot_in.value = 0
    dut.accum_in.value = 1
    await RisingEdge(dut.clk_w)
    py.tick(compute_in=1, a_in=a, b_in=b, slot_in=0, accum_in=1)
    await ReadOnly()
    assert _read_storage(dut, TMEM_SLOTS) == _py_storage_bits(py), (
        "storage mismatch after compute"
    )
    # Pipe regs: a_out, b_out, etc. should reflect this cycle's _in.
    assert int(dut.a_out.value) == a
    assert int(dut.b_out.value) == b
    assert int(dut.slot_out.value) == 0
    assert int(dut.accum_out.value) == 1
    assert int(dut.compute_out.value) == 1
    await NextTimeStep()

    # --- Cycle 3: drain slot 0 (drain_en pulses; drain_out becomes storage[0]) ---
    dut.compute_in.value = 0
    dut.a_in.value = 0
    dut.b_in.value = 0
    dut.slot_in.value = 0
    dut.accum_in.value = 0
    dut.drain_en_w.value = 1
    dut.drain_slot_w.value = 0
    await RisingEdge(dut.clk_w)
    py.tick(drain_en=1, drain_slot=0)
    await ReadOnly()
    assert int(dut.drain_out.value) == int(py.drain_out), (
        f"drain_out mismatch on inject cycle: sv={int(dut.drain_out.value)} "
        f"py={int(py.drain_out)}"
    )
    assert int(dut.compute_out.value) == 0
    assert int(dut.a_out.value) == 0
    await NextTimeStep()

    # --- Cycle 4: idle with drain_in=0; drain_out should register 0 ---
    dut.drain_en_w.value = 0
    dut.drain_slot_w.value = 0
    dut.drain_in.value = 0
    await RisingEdge(dut.clk_w)
    py.tick()
    await ReadOnly()
    sv = int(dut.drain_out.value)
    pyval = int(py.drain_out)
    assert sv == pyval, f"drain_out mismatch on idle cycle: sv={sv:#010x} py={pyval:#010x}"
    await NextTimeStep()

    # --- Cycle 5: scrub everything ---
    dut.scrub_en_w.value = 1
    await RisingEdge(dut.clk_w)
    py.tick(scrub_en=1)
    await ReadOnly()
    assert _read_storage(dut, TMEM_SLOTS) == _py_storage_bits(py), (
        "storage mismatch after scrub"
    )
    await NextTimeStep()

    dut.scrub_en_w.value = 0


def _rand_inputs(rng: random.Random) -> dict:
    """Random inputs respecting the mutex invariants (scrub > init > compute)."""
    op = rng.choices(["scrub", "init", "compute", "none"], weights=[1, 3, 5, 2])[0]
    scrub_en = 1 if op == "scrub" else 0
    init_en = 1 if op == "init" else 0
    compute_in = 1 if op == "compute" else 0

    a_in = rng.randint(0, 255)
    b_in = rng.randint(0, 255)
    slot_in = rng.randrange(TMEM_SLOTS)
    accum_in = rng.randint(0, 1)

    init_slot = rng.randrange(TMEM_SLOTS)
    init_data = rng.randint(0, 0xFFFFFFFF)

    drain_en = rng.randint(0, 1)
    drain_slot = rng.randrange(TMEM_SLOTS)
    drain_in = rng.randint(0, 0xFFFFFFFF)

    return {
        "compute_in": compute_in,
        "a_in": a_in,
        "b_in": b_in,
        "slot_in": slot_in,
        "accum_in": accum_in,
        "drain_en": drain_en,
        "drain_slot": drain_slot,
        "drain_in": drain_in,
        "init_en": init_en,
        "init_slot": init_slot,
        "init_data": init_data,
        "scrub_en": scrub_en,
    }


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """500 random cycles of mixed ops; lockstep with pymodel; check every output."""
    await start_clock(dut, signal_name="clk_w")
    await _drive_defaults(dut)
    await reset(dut, signal_name="reset_w", clock_name="clk_w")

    py = MacTmemCell(n_slots=TMEM_SLOTS)
    rng = random.Random(0xC0FFEE)

    # pymodel keeps the pre-rename signal names (drain_en, drain_slot,
    # scrub_en); the DUT has those renamed to *_w as part of the abutment
    # feedthrough rework (issue #32). Map py→dut on the poke side only.
    PY_TO_DUT = {"drain_en": "drain_en_w",
                 "drain_slot": "drain_slot_w",
                 "scrub_en": "scrub_en_w"}

    N = 500
    for cyc in range(N):
        inputs = _rand_inputs(rng)

        # Drive DUT.
        for name, val in inputs.items():
            getattr(dut, PY_TO_DUT.get(name, name)).value = val

        await RisingEdge(dut.clk_w)
        py.tick(**inputs)

        await ReadOnly()
        sv_drain = int(dut.drain_out.value)
        py_drain = int(py.drain_out)
        assert sv_drain == py_drain, (
            f"cycle {cyc}: drain_out mismatch sv=0x{sv_drain:08x} "
            f"py=0x{py_drain:08x} inputs={inputs}"
        )
        sv_storage = _read_storage(dut, TMEM_SLOTS)
        py_storage = _py_storage_bits(py)
        assert sv_storage == py_storage, (
            f"cycle {cyc}: storage mismatch\n  sv={[f'0x{x:08x}' for x in sv_storage]}\n"
            f"  py={[f'0x{x:08x}' for x in py_storage]}\n  inputs={inputs}"
        )
        _check_pipe_out(dut, py, cyc, inputs)
        await NextTimeStep()
