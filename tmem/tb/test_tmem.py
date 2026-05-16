"""
cocotb testbench for tmem.sv.

Drives tmem.sv and pymodel.tmem.TMEM in lockstep and asserts equality of
every registered output (mma_rd_tile, mma_rd_valid, store_rd_tile,
store_rd_valid) every cycle.

Tile packing convention (must match tmem.sv):
    The packed tile vector has width MMA_M*MMA_N*32 bits. Element [i][j]
    (row i, col j) lives in bits [((i*MMA_N + j) * 32) +: 32], i.e. row-major
    with element [0][0] in the low 32 bits. Within each 32-bit word the fp32
    IEEE 754 bit pattern is stored verbatim (LSB at the low bit). In Python
    this corresponds to `tile.astype('<f4').tobytes()` followed by
    `int.from_bytes(buf, "little")`.

Tests:
  1. test_directed_write_then_read — write a tile to slot 0, read it back
     via STORE_RD next cycle, verify exact match.
  2. test_random_vs_pymodel — ~300 random cycles, compare every output to
     pymodel cycle-by-cycle via common.tb_utils.step_and_compare.
"""

import random

import cocotb
import numpy as np
from cocotb.triggers import RisingEdge, ReadOnly

from common.tb_utils import start_clock, reset, step_and_compare
from config import MMA_M, MMA_N, TMEM_SLOTS
from pymodel.tmem import MMAOp, TMEM


TILE_BYTES = MMA_M * MMA_N * 4
TILE_BITS = TILE_BYTES * 8


def tile_to_int(tile: np.ndarray) -> int:
    """Convert (MMA_M, MMA_N) fp32 array -> packed int matching SV layout.

    Row-major, element [0][0] in low bits, fp32 bit pattern as IEEE 754
    little-endian within each 32-bit word.
    """
    assert tile.shape == (MMA_M, MMA_N)
    assert tile.dtype == np.float32
    # numpy stores ndarray in C (row-major) order by default; '<f4' forces
    # little-endian fp32. tobytes() emits bytes in iteration order: [0][0]
    # first, so element [0][0] lands at byte offset 0 -> low bits of the int.
    buf = np.ascontiguousarray(tile, dtype="<f4").tobytes()
    return int.from_bytes(buf, "little")


def int_to_tile(packed: int) -> np.ndarray:
    """Inverse of tile_to_int."""
    buf = int(packed).to_bytes(TILE_BYTES, "little")
    return np.frombuffer(buf, dtype="<f4").reshape(MMA_M, MMA_N).astype(np.float32)


class TMEMAdapter:
    """Wraps pymodel.TMEM so step_and_compare sees ints on both sides.

    - mma_write_tile arrives from inputs as an int (matching SV); we convert
      to a numpy fp32 array before forwarding to pymodel.tick.
    - mma_rd_tile / store_rd_tile are exposed as ints matching the SV packing.
    """

    def __init__(self):
        self._t = TMEM()

    def tick(self, **kwargs):
        # Drop 'reset' if present (pymodel.tick has no reset kwarg; the cocotb
        # harness drives reset separately and we only call tick when reset=0).
        kwargs.pop("reset", None)
        if "mma_write_tile" in kwargs:
            v = kwargs["mma_write_tile"]
            if not isinstance(v, np.ndarray):
                kwargs["mma_write_tile"] = int_to_tile(v)
        else:
            # pymodel's tick allows mma_write_tile=None, but only checks it
            # when op == WRITE. We always pass an int from the testbench, so
            # this branch is just defensive.
            pass
        self._t.tick(**kwargs)

    @property
    def mma_rd_tile(self) -> int:
        return tile_to_int(self._t.mma_rd_tile)

    @property
    def mma_rd_valid(self) -> int:
        return int(self._t.mma_rd_valid)

    @property
    def store_rd_tile(self) -> int:
        return tile_to_int(self._t.store_rd_tile)

    @property
    def store_rd_valid(self) -> int:
        return int(self._t.store_rd_valid)


def _rand_tile(rng: random.Random) -> np.ndarray:
    """Random fp32 tile drawn from a finite-value RandomState."""
    seed = rng.randint(0, 2**31 - 1)
    return np.random.RandomState(seed).randn(MMA_M, MMA_N).astype(np.float32)


@cocotb.test()
async def test_directed_write_then_read(dut):
    """Write a known tile to slot 0, read it back via STORE_RD next cycle."""
    await start_clock(dut)
    # Drive safe defaults before reset so X's don't propagate.
    dut.mma_op.value = 0
    dut.mma_slot.value = 0
    dut.mma_write_tile.value = 0
    dut.store_rd_en.value = 0
    dut.store_rd_slot.value = 0
    await reset(dut)

    # Build a deterministic tile: tile[i][j] = (i * MMA_N + j) cast to fp32.
    tile = np.arange(MMA_M * MMA_N, dtype=np.float32).reshape(MMA_M, MMA_N)
    tile_int = tile_to_int(tile)

    # Cycle T0: MMA_PORT WRITE tile -> slot 0.
    dut.mma_op.value = int(MMAOp.WRITE)
    dut.mma_slot.value = 0
    dut.mma_write_tile.value = tile_int
    dut.store_rd_en.value = 0
    dut.store_rd_slot.value = 0
    await RisingEdge(dut.clk)

    # Cycle T1: issue STORE_RD on slot 0; clear write.
    dut.mma_op.value = 0
    dut.mma_slot.value = 0
    dut.mma_write_tile.value = 0
    dut.store_rd_en.value = 1
    dut.store_rd_slot.value = 0
    await RisingEdge(dut.clk)

    # Cycle T2: drain. store_rd_valid should be 1 and store_rd_tile == tile.
    dut.store_rd_en.value = 0
    dut.store_rd_slot.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()

    sv_valid = int(dut.store_rd_valid.value)
    sv_tile = int(dut.store_rd_tile.value)
    assert sv_valid == 1, f"expected store_rd_valid=1, got {sv_valid}"
    assert sv_tile == tile_int, (
        "store_rd_tile mismatch:\n"
        f"  sv      first 8 bits = 0x{sv_tile & ((1<<256)-1):064x}\n"
        f"  expected first 8 bits = 0x{tile_int & ((1<<256)-1):064x}"
    )


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """Drive ~300 random cycles; compare every registered output to pymodel."""
    await start_clock(dut)
    dut.mma_op.value = 0
    dut.mma_slot.value = 0
    dut.mma_write_tile.value = 0
    dut.store_rd_en.value = 0
    dut.store_rd_slot.value = 0
    await reset(dut)

    py = TMEMAdapter()
    rng = random.Random(0xDECAF)

    outputs = ["mma_rd_tile", "mma_rd_valid", "store_rd_tile", "store_rd_valid"]

    for cycle in range(300):
        # Pick MMA op: weight toward NONE/WRITE early (so reads find data),
        # then mix freely.
        mma_op = rng.choices(
            [int(MMAOp.NONE), int(MMAOp.READ), int(MMAOp.WRITE)],
            weights=[2, 2, 2],
        )[0]
        mma_slot = rng.randrange(TMEM_SLOTS) if mma_op != int(MMAOp.NONE) else 0

        if mma_op == int(MMAOp.WRITE):
            tile = _rand_tile(rng)
            mma_write_tile = tile_to_int(tile)
        else:
            mma_write_tile = 0

        store_rd_en = rng.randint(0, 1)
        store_rd_slot = rng.randrange(TMEM_SLOTS) if store_rd_en else 0

        # Avoid the spec-illegal same-cycle MMA WRITE + STORE_RD on same slot.
        if (mma_op == int(MMAOp.WRITE)
                and store_rd_en
                and mma_slot == store_rd_slot):
            store_rd_en = 0
            store_rd_slot = 0

        inputs = {
            "reset": 0,
            "mma_op": mma_op,
            "mma_slot": mma_slot,
            "mma_write_tile": mma_write_tile,
            "store_rd_en": store_rd_en,
            "store_rd_slot": store_rd_slot,
        }

        await step_and_compare(dut, py, inputs, outputs)
