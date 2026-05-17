"""
cocotb testbench for mma.sv (via mma_tb_top, which wires mma + smem + tmem +
barrier together).

Strategy:
  1. Seed SMEM with the A/B tile bytes via a hierarchical back-door write
     (dut.u_smem.mem[idx] = byte).
  2. Initialize a barrier with count=1 (so the MMA's arrive flips it once).
  3. Pulse start=1 with the operand bundle.
  4. Wait for done to pulse.
  5. Read TMEM slot 0 via the STORE_RD port (1-cycle latency).
  6. Compare to the golden expected tile (fp8 decode -> fp32 matmul).

We only test accum=0 here. accum=1 has additional pipeline shape that the
pymodel hides via back-door TMEM access; the SV exercises a real TMEM READ,
and verifying that path adds wiring that is out of scope for the result-
correctness check this TB is responsible for.

Tile/byte packing conventions (matches tmem.sv / smem.sv):
  - SMEM bytes: little-endian within a beat (mem[k] is the k-th byte).
  - TMEM tile : packed [MMA_M*MMA_N*32-1:0] vector; element [i][j] at bit
                ((i*MMA_N + j)*32) +: 32, fp32 IEEE 754 LSB-first per word.
"""

import random

import cocotb
import numpy as np
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, reset
from config import MMA_K, MMA_M, MMA_N, SMEM_TILE_BASE, BEAT_BYTES
from golden.fp8 import decode_e4m3, encode_e4m3
from golden.matmul_reference import generate


TILE_BYTES = MMA_M * MMA_N * 4


def tile_to_int(tile: np.ndarray) -> int:
    """Convert (MMA_M, MMA_N) fp32 array -> packed int matching SV layout."""
    assert tile.shape == (MMA_M, MMA_N)
    assert tile.dtype == np.float32
    buf = np.ascontiguousarray(tile, dtype="<f4").tobytes()
    return int.from_bytes(buf, "little")


def int_to_tile(packed: int) -> np.ndarray:
    """Inverse of tile_to_int."""
    buf = int(packed).to_bytes(TILE_BYTES, "little")
    return np.frombuffer(buf, dtype="<f4").reshape(MMA_M, MMA_N).astype(np.float32)


async def _drive_defaults(dut) -> None:
    """Drive safe defaults so X's don't propagate into wires."""
    dut.start.value = 0
    dut.a_smem_offset.value = 0
    dut.b_smem_offset.value = 0
    dut.d_tmem_slot.value = 0
    dut.accum.value = 0
    dut.bar_id.value = 0

    dut.init_en.value = 0
    dut.init_bar_id.value = 0
    dut.init_count.value = 0

    dut.smem_wr_en.value = 0
    dut.smem_wr_addr.value = 0
    dut.smem_wr_data.value = 0

    dut.tmem_store_rd_en.value = 0
    dut.tmem_store_rd_slot.value = 0


def _seed_smem(dut, offset: int, data: bytes) -> None:
    """Back-door write `data` into the SMEM banked storage starting at `offset`.

    The banked smem (32 banks × NUM_WORDS_PER_BANK × 4 bytes) exposes its
    byte array `mem[]` as a read-only combinational alias of the banks,
    so we have to write directly into `bank_mem[bank][word]`. cocotb
    hierarchical writes are scheduled (NBA-like) — successive
    read-modify-write of the same word would race — so we gather all
    bytes per (bank, word) into a dict first, then issue one write per
    word.
    """
    NUM_BANKS = 32
    bank_mem = dut.u_smem.bank_mem
    # 1. Pre-read existing words for any (bank, word) we'll touch.
    word_cache: dict[tuple[int, int], int] = {}
    for i in range(len(data)):
        byte_addr = offset + i
        bank = (byte_addr >> 2) & (NUM_BANKS - 1)
        word = byte_addr >> (2 + 5)
        key = (bank, word)
        if key not in word_cache:
            word_cache[key] = int(bank_mem[bank][word].value)
    # 2. Apply byte updates.
    for i, b in enumerate(data):
        byte_addr = offset + i
        bank = (byte_addr >> 2) & (NUM_BANKS - 1)
        word = byte_addr >> (2 + 5)
        byte_in_dw = byte_addr & 3
        v = word_cache[(bank, word)]
        v &= ~(0xFF << (byte_in_dw * 8))
        v |= (int(b) & 0xFF) << (byte_in_dw * 8)
        word_cache[(bank, word)] = v
    # 3. Write one word at a time.
    for (bank, word), v in word_cache.items():
        bank_mem[bank][word].value = v & 0xFFFFFFFF


async def _init_barrier(dut, bar_id: int, count: int) -> None:
    """Pulse init_en for one cycle to set bar.expected = bar.pending = count."""
    dut.init_en.value = 1
    dut.init_bar_id.value = bar_id
    dut.init_count.value = count
    await RisingEdge(dut.clk)
    dut.init_en.value = 0
    dut.init_bar_id.value = 0
    dut.init_count.value = 0


async def _issue_mma(
    dut,
    a_off: int,
    b_off: int,
    d_slot: int,
    accum: int,
    bar_id: int,
) -> None:
    """Pulse start=1 for one cycle with the operand bundle."""
    dut.start.value = 1
    dut.a_smem_offset.value = a_off
    dut.b_smem_offset.value = b_off
    dut.d_tmem_slot.value = d_slot
    dut.accum.value = accum
    dut.bar_id.value = bar_id
    await RisingEdge(dut.clk)
    dut.start.value = 0
    dut.a_smem_offset.value = 0
    dut.b_smem_offset.value = 0
    dut.d_tmem_slot.value = 0
    dut.accum.value = 0
    dut.bar_id.value = 0


async def _wait_for_done(dut, max_cycles: int = 1000) -> int:
    """Spin until dut.done pulses high. Returns cycles elapsed."""
    for c in range(max_cycles):
        await ReadOnly()
        d = int(dut.done.value)
        await NextTimeStep()
        if d:
            return c
        await RisingEdge(dut.clk)
    raise AssertionError(f"MMA did not assert done within {max_cycles} cycles")


async def _read_tmem_tile(dut, slot: int) -> np.ndarray:
    """Read TMEM slot via the STORE_RD port (1-cycle latency)."""
    # Cycle T: issue STORE_RD.
    dut.tmem_store_rd_en.value = 1
    dut.tmem_store_rd_slot.value = slot
    await RisingEdge(dut.clk)
    dut.tmem_store_rd_en.value = 0
    dut.tmem_store_rd_slot.value = 0
    # Cycle T+1: sample drained tile.
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.tmem_store_rd_valid.value) == 1, (
        "tmem_store_rd_valid not asserted one cycle after rd_en"
    )
    packed = int(dut.tmem_store_rd_tile.value)
    await NextTimeStep()
    return int_to_tile(packed)


def _expected_from_tiles(A_fp8: np.ndarray, B_fp8: np.ndarray) -> np.ndarray:
    """Reference: decode -> fp32 matmul. Matches what the MMA should produce."""
    A_fp32 = decode_e4m3(A_fp8)
    B_fp32 = decode_e4m3(B_fp8)
    return (A_fp32 @ B_fp32).astype(np.float32)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
@cocotb.test()
async def test_directed(dut):
    """Directed test: known A/B with accum=0, exact fp32 result match.

    Uses small integer-valued fp8 entries so the decode is unambiguous and
    the multiplication produces a predictable accumulator.
    """
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)

    # Use a tiny deterministic seed.
    A_fp32 = (np.random.RandomState(0).randn(MMA_M, MMA_K) * 0.5).astype(np.float32)
    B_fp32 = (np.random.RandomState(1).randn(MMA_K, MMA_N) * 0.5).astype(np.float32)
    A_fp8 = encode_e4m3(A_fp32)
    B_fp8 = encode_e4m3(B_fp32)

    # SMEM layout: A column-major (A_bytes[k*M + m] = A_fp8[m, k]),
    #              B row-major    (B_bytes[k*N + n] = B_fp8[k, n]).
    A_bytes = bytes(np.ascontiguousarray(A_fp8.T).reshape(-1))
    B_bytes = bytes(np.ascontiguousarray(B_fp8).reshape(-1))

    a_off = SMEM_TILE_BASE
    b_off = SMEM_TILE_BASE + MMA_M * MMA_K
    # SMEM port reads require MMA_M / MMA_N alignment; offsets above respect that.
    assert a_off % MMA_M == 0 and b_off % MMA_N == 0

    _seed_smem(dut, a_off, A_bytes)
    _seed_smem(dut, b_off, B_bytes)

    # Initialize barrier 0 with count=1; MMA's arrive should flip it.
    await _init_barrier(dut, bar_id=0, count=1)

    # Issue MMA.
    await _issue_mma(dut, a_off=a_off, b_off=b_off, d_slot=0, accum=0, bar_id=0)

    # Wait for done.
    await _wait_for_done(dut)

    # Read back the result tile.
    got = await _read_tmem_tile(dut, slot=0)

    # Compare to golden.
    expected = _expected_from_tiles(A_fp8, B_fp8)
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-5)

    # Check barrier flipped to phase=1 (count=1, one arrive => flip).
    await ReadOnly()
    phase_bits = int(dut.bars_phase.value)
    bar0_phase = (phase_bits >> 0) & 1
    await NextTimeStep()
    assert bar0_phase == 1, (
        f"barrier 0 should have flipped to phase=1; got {bar0_phase}"
    )


@cocotb.test()
async def test_random_vs_golden(dut):
    """Run several random seeds; each result tile must equal the fp32 matmul golden."""
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)

    a_off = SMEM_TILE_BASE
    b_off = SMEM_TILE_BASE + MMA_M * MMA_K

    seeds = [0, 1, 7, 42, 123]
    for trial, seed in enumerate(seeds):
        A_bytes, B_bytes, C_expected = generate(MMA_M, MMA_N, MMA_K, seed=seed)

        _seed_smem(dut, a_off, A_bytes)
        _seed_smem(dut, b_off, B_bytes)

        # Re-init the barrier (count=1) so the arrive will flip it again
        # (alternates phase 0 -> 1 -> 0 -> 1 ...; init resets to phase 0).
        await _init_barrier(dut, bar_id=trial % 8, count=1)

        await _issue_mma(
            dut,
            a_off=a_off,
            b_off=b_off,
            d_slot=trial % 4,
            accum=0,
            bar_id=trial % 8,
        )
        await _wait_for_done(dut)

        got = await _read_tmem_tile(dut, slot=trial % 4)
        np.testing.assert_allclose(
            got, C_expected, rtol=0, atol=1e-5,
            err_msg=f"seed={seed} trial={trial}: SV vs golden mismatch",
        )
