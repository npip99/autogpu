"""
cocotb testbench for smem.sv.

Drives smem.sv and pymodel.smem.SMEM in lockstep and asserts equality of
every registered output (rd_a_data, rd_a_valid, rd_b_data, rd_b_valid)
every cycle.

Byte packing convention (must match smem.sv):
    wr_data is BEAT_BYTES bytes packed little-endian (byte 0 in low 8 bits).
    rd_a_data is MMA_M bytes packed the same way; rd_b_data is MMA_N bytes.
    In Python: int.from_bytes(buf, "little") and int.to_bytes(n, "little").

Tests:
  1. test_directed_load_then_read_a — backdoor-load a tile, drive MMA_RD_A
     to read the first MMA_M bytes, verify exact match next cycle.
  2. test_random_vs_pymodel — ~500 random cycles, compare every output to
     pymodel cycle-by-cycle via common.tb_utils.step_and_compare.
"""

import random

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly

from common.tb_utils import start_clock, reset, step_and_compare
from config import BEAT_BYTES, MMA_M, MMA_N, SMEM_BYTES, SMEM_TILE_BASE
from pymodel.smem import SMEM


def _bytes_to_int(b: bytes) -> int:
    """Little-endian within a beat: byte 0 -> low 8 bits."""
    return int.from_bytes(b, "little")


def _int_to_bytes(n: int, nbytes: int) -> bytes:
    return int(n).to_bytes(nbytes, "little")


class SMEMAdapter:
    """Wraps pymodel.SMEM so step_and_compare sees ints on both sides.

    - wr_data arrives from inputs as an int (matching SV); we convert to
      bytes (BEAT_BYTES long) before forwarding to pymodel.tick.
    - rd_a_data / rd_b_data are exposed as ints matching the SV packing.
    - 'reset' kwarg (used by the harness) is dropped — pymodel has no reset.
    """

    def __init__(self):
        self._s = SMEM()

    def tick(self, **kwargs):
        kwargs.pop("reset", None)
        if "wr_data" in kwargs and not isinstance(kwargs["wr_data"], (bytes, bytearray)):
            kwargs["wr_data"] = _int_to_bytes(kwargs["wr_data"], BEAT_BYTES)
        self._s.tick(**kwargs)

    # Back-door for the directed test.
    def load(self, addr: int, data: bytes) -> None:
        self._s.load(addr, data)

    @property
    def rd_a_data(self) -> int:
        return _bytes_to_int(self._s.rd_a_data)

    @property
    def rd_a_valid(self) -> int:
        return int(self._s.rd_a_valid)

    @property
    def rd_b_data(self) -> int:
        return _bytes_to_int(self._s.rd_b_data)

    @property
    def rd_b_valid(self) -> int:
        return int(self._s.rd_b_valid)


def _rand_wr_addr(rng: random.Random) -> int:
    """Random BEAT_BYTES-aligned address inside the tile region."""
    lo = SMEM_TILE_BASE // BEAT_BYTES
    hi = (SMEM_BYTES // BEAT_BYTES) - 1
    return rng.randint(lo, hi) * BEAT_BYTES


def _rand_rd_a_addr(rng: random.Random) -> int:
    """Random MMA_M-aligned address inside the tile region."""
    lo = SMEM_TILE_BASE // MMA_M
    hi = (SMEM_BYTES // MMA_M) - 1
    return rng.randint(lo, hi) * MMA_M


def _rand_rd_b_addr(rng: random.Random) -> int:
    """Random MMA_N-aligned address inside the tile region."""
    lo = SMEM_TILE_BASE // MMA_N
    hi = (SMEM_BYTES // MMA_N) - 1
    return rng.randint(lo, hi) * MMA_N


def _overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return not (a1 <= b0 or b1 <= a0)


def _backdoor_load_dut(dut, addr: int, data: bytes) -> None:
    """Write `data` bytes into dut.mem[addr:] one byte at a time."""
    for i, byte in enumerate(data):
        dut.mem[addr + i].value = byte


@cocotb.test()
async def test_directed_load_then_read_a(dut):
    """Backdoor-load a tile; drive MMA_RD_A; verify exact match."""
    await start_clock(dut)
    # Drive safe defaults before reset so X's don't propagate.
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_a_en.value = 0
    dut.rd_a_addr.value = 0
    dut.rd_b_en.value = 0
    dut.rd_b_addr.value = 0
    await reset(dut)

    # Build a deterministic tile of A_TILE_BYTES bytes at SMEM_TILE_BASE.
    # We'll read the first MMA_M bytes (one "column" worth in the spec's terms).
    tile_bytes = bytes((i + 1) & 0xFF for i in range(MMA_M))
    expected_int = _bytes_to_int(tile_bytes)
    addr = SMEM_TILE_BASE
    _backdoor_load_dut(dut, addr, tile_bytes)

    # Cycle T0: issue MMA_RD_A at addr.
    dut.rd_a_en.value = 1
    dut.rd_a_addr.value = addr
    await RisingEdge(dut.clk)

    # Cycle T1: drain. rd_a_valid should be 1 and rd_a_data should match.
    dut.rd_a_en.value = 0
    dut.rd_a_addr.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()

    sv_valid = int(dut.rd_a_valid.value)
    sv_data = int(dut.rd_a_data.value)
    assert sv_valid == 1, f"expected rd_a_valid=1, got {sv_valid}"
    assert sv_data == expected_int, (
        f"rd_a_data mismatch: sv=0x{sv_data:0{MMA_M*2}x} "
        f"expected=0x{expected_int:0{MMA_M*2}x}"
    )


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """Drive ~500 random cycles; compare every registered output to pymodel."""
    await start_clock(dut)
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_a_en.value = 0
    dut.rd_a_addr.value = 0
    dut.rd_b_en.value = 0
    dut.rd_b_addr.value = 0
    await reset(dut)

    py = SMEMAdapter()
    rng = random.Random(0xBEEF)

    outputs = ["rd_a_data", "rd_a_valid", "rd_b_data", "rd_b_valid"]

    for cycle in range(500):
        wr_en = rng.randint(0, 1)
        rd_a_en = rng.randint(0, 1)
        rd_b_en = rng.randint(0, 1)

        wr_addr = _rand_wr_addr(rng) if wr_en else 0
        rd_a_addr = _rand_rd_a_addr(rng) if rd_a_en else 0
        rd_b_addr = _rand_rd_b_addr(rng) if rd_b_en else 0

        # Avoid the spec-illegal same-cycle wr_en + rd_*_en with OVERLAPPING
        # byte ranges (pymodel asserts). Drop the write if it overlaps either
        # active read this cycle.
        if wr_en and rd_a_en and _overlap(
            wr_addr, wr_addr + BEAT_BYTES, rd_a_addr, rd_a_addr + MMA_M
        ):
            wr_en = 0
            wr_addr = 0
        if wr_en and rd_b_en and _overlap(
            wr_addr, wr_addr + BEAT_BYTES, rd_b_addr, rd_b_addr + MMA_N
        ):
            wr_en = 0
            wr_addr = 0

        wr_data = rng.getrandbits(BEAT_BYTES * 8) if wr_en else 0

        inputs = {
            "reset": 0,
            "wr_en": wr_en,
            "wr_addr": wr_addr,
            "wr_data": wr_data,
            "rd_a_en": rd_a_en,
            "rd_a_addr": rd_a_addr,
            "rd_b_en": rd_b_en,
            "rd_b_addr": rd_b_addr,
        }

        await step_and_compare(dut, py, inputs, outputs)
