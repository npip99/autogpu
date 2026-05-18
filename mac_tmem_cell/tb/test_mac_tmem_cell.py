"""
cocotb testbench for mac_tmem_cell.sv.

Drives mac_tmem_cell.sv and pymodel.mac_tmem_cell.MacTmemCell in lockstep.
Asserts equality on:
  - drain_data (registered output)
  - storage[*] (back-door observation each cycle)
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
    dut.compute.value = 0
    dut.a.value = 0
    dut.b.value = 0
    dut.slot.value = 0
    dut.accum.value = 0
    dut.drain_en.value = 0
    dut.drain_slot.value = 0
    dut.init_en.value = 0
    dut.init_slot.value = 0
    dut.init_data.value = 0
    dut.scrub_en.value = 0


def _read_storage(dut, n_slots: int) -> list[int]:
    return [int(dut.storage[s].value) & 0xFFFFFFFF for s in range(n_slots)]


def _py_storage_bits(py: MacTmemCell) -> list[int]:
    arr = py.storage.astype(np.float32).view(np.uint32)
    return [int(x) for x in arr]


@cocotb.test()
async def test_directed(dut):
    """Canned operations with known values, exact match to pymodel."""
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)

    py = MacTmemCell(n_slots=TMEM_SLOTS)

    # --- Cycle 1: init slot 0 to 7.5 ---
    seed_bits = _fp32_bits(7.5)
    dut.init_en.value = 1
    dut.init_slot.value = 0
    dut.init_data.value = seed_bits
    await RisingEdge(dut.clk)
    py.tick(init_en=1, init_slot=0, init_data=seed_bits)
    await ReadOnly()
    assert _read_storage(dut, TMEM_SLOTS) == _py_storage_bits(py), (
        "storage mismatch after init"
    )
    await NextTimeStep()

    # --- Cycle 2: compute accum=1 on slot 0 ---
    a = _encode_one(2.0)
    b = _encode_one(3.0)
    dut.init_en.value = 0
    dut.init_slot.value = 0
    dut.init_data.value = 0
    dut.compute.value = 1
    dut.a.value = a
    dut.b.value = b
    dut.slot.value = 0
    dut.accum.value = 1
    await RisingEdge(dut.clk)
    py.tick(compute=1, a=a, b=b, slot=0, accum=1)
    await ReadOnly()
    assert _read_storage(dut, TMEM_SLOTS) == _py_storage_bits(py), (
        "storage mismatch after compute"
    )
    await NextTimeStep()

    # --- Cycle 3: drain slot 0 (capture) ---
    dut.compute.value = 0
    dut.a.value = 0
    dut.b.value = 0
    dut.slot.value = 0
    dut.accum.value = 0
    dut.drain_en.value = 1
    dut.drain_slot.value = 0
    await RisingEdge(dut.clk)
    py.tick(drain_en=1, drain_slot=0)
    await ReadOnly()
    assert int(dut.drain_data.value) == int(py.drain_data), (
        f"drain_data mismatch on capture cycle: sv={int(dut.drain_data.value)} "
        f"py={int(py.drain_data)}"
    )
    await NextTimeStep()

    # --- Cycle 4: idle; drain_data should now reflect slot 0 ---
    dut.drain_en.value = 0
    dut.drain_slot.value = 0
    await RisingEdge(dut.clk)
    py.tick()
    await ReadOnly()
    sv = int(dut.drain_data.value)
    pyval = int(py.drain_data)
    assert sv == pyval, f"drain_data mismatch on drain cycle: sv={sv:#010x} py={pyval:#010x}"
    await NextTimeStep()

    # --- Cycle 5: scrub everything ---
    dut.scrub_en.value = 1
    await RisingEdge(dut.clk)
    py.tick(scrub_en=1)
    await ReadOnly()
    assert _read_storage(dut, TMEM_SLOTS) == _py_storage_bits(py), (
        "storage mismatch after scrub"
    )
    await NextTimeStep()

    dut.scrub_en.value = 0


def _rand_inputs(rng: random.Random) -> dict:
    """Random inputs respecting the mutex invariants (scrub > init > compute)."""
    # Pick exactly one of (scrub, init, compute, none) via a roulette wheel.
    op = rng.choices(["scrub", "init", "compute", "none"], weights=[1, 3, 5, 2])[0]
    scrub_en = 1 if op == "scrub" else 0
    init_en = 1 if op == "init" else 0
    compute = 1 if op == "compute" else 0

    a = rng.randint(0, 255)
    b = rng.randint(0, 255)
    slot = rng.randrange(TMEM_SLOTS)
    accum = rng.randint(0, 1)

    init_slot = rng.randrange(TMEM_SLOTS)
    init_data = rng.randint(0, 0xFFFFFFFF)

    drain_en = rng.randint(0, 1)
    drain_slot = rng.randrange(TMEM_SLOTS)

    return {
        "compute": compute,
        "a": a,
        "b": b,
        "slot": slot,
        "accum": accum,
        "drain_en": drain_en,
        "drain_slot": drain_slot,
        "init_en": init_en,
        "init_slot": init_slot,
        "init_data": init_data,
        "scrub_en": scrub_en,
    }


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """500 random cycles of mixed ops; lockstep with pymodel; check every output."""
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)

    py = MacTmemCell(n_slots=TMEM_SLOTS)
    rng = random.Random(0xC0FFEE)

    N = 500
    for cyc in range(N):
        inputs = _rand_inputs(rng)

        # Drive DUT.
        for name, val in inputs.items():
            getattr(dut, name).value = val

        await RisingEdge(dut.clk)
        py.tick(**inputs)

        await ReadOnly()
        sv_drain = int(dut.drain_data.value)
        py_drain = int(py.drain_data)
        assert sv_drain == py_drain, (
            f"cycle {cyc}: drain_data mismatch sv=0x{sv_drain:08x} "
            f"py=0x{py_drain:08x} inputs={inputs}"
        )
        sv_storage = _read_storage(dut, TMEM_SLOTS)
        py_storage = _py_storage_bits(py)
        assert sv_storage == py_storage, (
            f"cycle {cyc}: storage mismatch\n  sv={[f'0x{x:08x}' for x in sv_storage]}\n"
            f"  py={[f'0x{x:08x}' for x in py_storage]}\n  inputs={inputs}"
        )
        await NextTimeStep()
