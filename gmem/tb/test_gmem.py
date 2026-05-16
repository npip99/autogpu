"""
cocotb testbench for gmem.sv.

Drives gmem.sv and pymodel.gmem.GMEM in lockstep and asserts equality of
every registered output (rd_data, rd_valid) every cycle.

Conventions (see DEVELOPMENT.md §Testing):
  - SV port names match pymodel kwargs / attrs exactly so step_and_compare
    can use string-keyed access.
  - rd_data crosses the python<->SV boundary as an int: byte 0 lives in bits
    [7:0] of the packed SV vector, byte 1 in [15:8], etc. (little-endian
    within the beat). The adapter below converts pymodel `bytes` <-> int so
    step_and_compare sees ints on both sides.

Tests:
  1. test_directed_write_then_read — write a beat, read it back next cycle.
  2. test_random_vs_pymodel — ~500 random cycles, compare every output to
     pymodel cycle-by-cycle via common.tb_utils.step_and_compare.
"""

import random

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly

from common.tb_utils import start_clock, reset, step_and_compare
from config import BEAT_BYTES, GMEM_BYTES
from pymodel.gmem import GMEM


def _bytes_to_int(b: bytes) -> int:
    """Little-endian within a beat: byte 0 → low 8 bits."""
    return int.from_bytes(b, "little")


def _int_to_bytes(n: int) -> bytes:
    return int(n).to_bytes(BEAT_BYTES, "little")


class GMEMAdapter:
    """Wraps pymodel.GMEM so step_and_compare sees ints, not bytes.

    step_and_compare reads inputs as a flat dict (forwarded to tick) and reads
    `getattr(pymodel, name)` for each output. The pymodel stores rd_data as
    bytes; we expose it as an int matching the SV packed-vector encoding.
    """

    def __init__(self):
        self._g = GMEM()

    def tick(self, **kwargs):
        # Convert wr_data int -> bytes if present and wr_en is asserted.
        if "wr_data" in kwargs and not isinstance(kwargs["wr_data"], (bytes, bytearray)):
            kwargs["wr_data"] = _int_to_bytes(kwargs["wr_data"])
        self._g.tick(**kwargs)

    @property
    def rd_data(self) -> int:
        return _bytes_to_int(self._g.rd_data)

    @property
    def rd_valid(self) -> int:
        return self._g.rd_valid


def _aligned_addr(rng: random.Random) -> int:
    """Random BEAT_BYTES-aligned address inside the memory."""
    max_beat = (GMEM_BYTES // BEAT_BYTES) - 1
    return rng.randint(0, max_beat) * BEAT_BYTES


@cocotb.test()
async def test_directed_write_then_read(dut):
    """Write a known pattern at addr 0, read it back next cycle."""
    await start_clock(dut)
    # Drive safe defaults before reset so X's don't propagate into mem.
    dut.rd_en.value = 0
    dut.wr_en.value = 0
    dut.rd_addr.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    await reset(dut)

    pattern_bytes = bytes((i + 1) & 0xFF for i in range(BEAT_BYTES))
    pattern_int = _bytes_to_int(pattern_bytes)

    # Cycle 0: write the pattern at addr 0.
    dut.wr_en.value = 1
    dut.wr_addr.value = 0
    dut.wr_data.value = pattern_int
    dut.rd_en.value = 0
    dut.rd_addr.value = 0
    await RisingEdge(dut.clk)

    # Cycle 1: issue read; write inputs go low.
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_en.value = 1
    dut.rd_addr.value = 0
    await RisingEdge(dut.clk)

    # Cycle 2: drain. rd_valid should be 1 and rd_data should match.
    dut.rd_en.value = 0
    dut.rd_addr.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()

    sv_valid = int(dut.rd_valid.value)
    sv_data = int(dut.rd_data.value)
    assert sv_valid == 1, f"expected rd_valid=1, got {sv_valid}"
    assert sv_data == pattern_int, (
        f"rd_data mismatch: sv=0x{sv_data:0{BEAT_BYTES*2}x} "
        f"expected=0x{pattern_int:0{BEAT_BYTES*2}x}"
    )


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """Drive ~500 random cycles; compare rd_data/rd_valid to pymodel every cycle."""
    await start_clock(dut)
    dut.rd_en.value = 0
    dut.wr_en.value = 0
    dut.rd_addr.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    await reset(dut)

    py = GMEMAdapter()
    rng = random.Random(0xC0FFEE)

    # Mix of phases: warm up with a write-heavy phase so reads find non-zero data.
    for cycle in range(500):
        rd_en = rng.randint(0, 1)
        wr_en = rng.randint(0, 1)
        rd_addr = _aligned_addr(rng) if rd_en else 0
        wr_addr = _aligned_addr(rng) if wr_en else 0

        # Avoid the illegal same-cycle r/w overlap (spec asserts on it).
        if rd_en and wr_en and rd_addr == wr_addr:
            wr_en = 0
            wr_addr = 0

        wr_data = rng.getrandbits(BEAT_BYTES * 8) if wr_en else 0

        inputs = {
            "reset": 0,
            "rd_en": rd_en,
            "rd_addr": rd_addr,
            "wr_en": wr_en,
            "wr_addr": wr_addr,
            "wr_data": wr_data,
        }
        outputs = ["rd_data", "rd_valid"]
        await step_and_compare(dut, py, inputs, outputs)
