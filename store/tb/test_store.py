"""
cocotb testbench for store.sv.

Drives store_tb_top (store + compute_array + gmem) and verifies that the
STORE engine produces the same gmem bytes as pymodel.store.Store on the
same input tile.

Phase 7h-3: store now consumes compute_array's drain-stream interface
(one row per cycle), not a 32k-bit one-shot tile from tmem. We seed the
tile into compute_array via cocotb back-door into the per-cell storage,
following the pattern in compute_array/tb/test_compute_array.py.

Conventions (see DEVELOPMENT.md §Testing):
  - SV port names match pymodel kwargs / attrs exactly.
  - gmem bytes cross SV<->Python as an int (byte 0 in low 8 bits, little-endian).
"""

import random

import cocotb
import numpy as np
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, reset
from config import BEAT_BYTES, MMA_M, MMA_N
from golden.fp8 import decode_e4m3, encode_e4m3
from pymodel.compute_array import ComputeArray
from pymodel.gmem import GMEM
from pymodel.store import Store


TILE_BYTES = MMA_M * MMA_N * 4
TILE_BITS = TILE_BYTES * 8


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _fp32_bits(x: float) -> int:
    """fp32 → 32-bit unsigned bit pattern."""
    return int(np.array([np.float32(x)], dtype=np.float32).view(np.uint32)[0])


def _bytes_to_int(b: bytes) -> int:
    """Little-endian within a beat: byte 0 -> low 8 bits."""
    return int.from_bytes(b, "little")


def _int_to_bytes(n: int, nbytes: int = BEAT_BYTES) -> bytes:
    return int(n).to_bytes(nbytes, "little")


async def _drive_defaults(dut) -> None:
    """Drive safe defaults on every input so X's don't propagate."""
    dut.issue_en.value = 0
    dut.tmem_slot.value = 0
    dut.gmem_ptr.value = 0
    dut.dtype.value = 0
    dut.gmem_rd_en.value = 0
    dut.gmem_rd_addr.value = 0


async def _seed_tile(dut, slot: int, tile: np.ndarray) -> None:
    """Seed a tile into compute_array slot via per-cell storage back-door.

    Phase 7h-3: matches compute_array/tb/test_compute_array.py's pattern.
    """
    assert tile.shape == (MMA_M, MMA_N)
    for i in range(MMA_M):
        for j in range(MMA_N):
            dut.u_compute_array.gen_row[i].gen_col[j].u_cell.storage[slot].value = (
                _fp32_bits(tile[i, j])
            )


async def _issue_and_wait(
    dut, tmem_slot: int, gmem_ptr: int, dtype: int, max_cycles: int = 10000
) -> int:
    """Issue a STORE and hold issue_en until done pulses. Returns done cycle."""
    dut.issue_en.value = 1
    dut.tmem_slot.value = tmem_slot
    dut.gmem_ptr.value = gmem_ptr
    dut.dtype.value = dtype

    for c in range(max_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        d = int(dut.done.value)
        await NextTimeStep()
        if d:
            # Drop issue_en the cycle after done.
            dut.issue_en.value = 0
            dut.tmem_slot.value = 0
            dut.gmem_ptr.value = 0
            dut.dtype.value = 0
            return c
    raise AssertionError(f"STORE did not assert done within {max_cycles} cycles")


async def _read_gmem(dut, addr: int, nbytes: int) -> bytes:
    """Read nbytes from gmem starting at addr via the gmem rd port.

    Issues one read per BEAT_BYTES; uses the 1-cycle read latency. Caller
    guarantees addr and nbytes are BEAT_BYTES-aligned.

    Timing per beat i:
      - Drive rd_en=1, rd_addr at the START of cycle T (before RisingEdge).
      - The clock edge captures the request.
      - After ANOTHER RisingEdge (cycle T+1), the gmem has registered rd_valid
        high and rd_data with the beat. We sample then.

    Successive reads pipeline: drive request for beat i, advance one cycle,
    sample beat i's rd_data while simultaneously driving the request for beat
    i+1. After the last beat we drain one extra cycle.
    """
    assert addr % BEAT_BYTES == 0, "addr must be BEAT_BYTES-aligned"
    assert nbytes % BEAT_BYTES == 0, "nbytes must be BEAT_BYTES-multiple"
    nbeats = nbytes // BEAT_BYTES

    out = bytearray(nbytes)

    # Issue beat 0 request.
    dut.gmem_rd_en.value = 1
    dut.gmem_rd_addr.value = addr
    await RisingEdge(dut.clk)
    # Now in cycle T+1: gmem has captured beat 0's request; rd_valid for beat 0
    # will be high after the NEXT rising edge. Issue beat 1 request now (if any).
    for i in range(nbeats):
        if i + 1 < nbeats:
            dut.gmem_rd_en.value = 1
            dut.gmem_rd_addr.value = addr + (i + 1) * BEAT_BYTES
        else:
            dut.gmem_rd_en.value = 0
            dut.gmem_rd_addr.value = 0
        await RisingEdge(dut.clk)
        # After this edge, rd_valid for beat i is high, rd_data holds beat i.
        await ReadOnly()
        rv = int(dut.gmem_rd_valid.value)
        beat_int = int(dut.gmem_rd_data.value)
        await NextTimeStep()
        assert rv == 1, f"gmem rd_valid not high for beat {i}"
        chunk = _int_to_bytes(beat_int, BEAT_BYTES)
        out[i * BEAT_BYTES : (i + 1) * BEAT_BYTES] = chunk

    # Make sure rd_en is off for next caller.
    dut.gmem_rd_en.value = 0
    dut.gmem_rd_addr.value = 0
    return bytes(out)


def _det_tile(seed: int, scale: float = 0.3) -> np.ndarray:
    """Deterministic random fp32 tile."""
    return np.random.RandomState(seed).randn(MMA_M, MMA_N).astype(np.float32) * scale


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
@cocotb.test()
async def test_store_fp32(dut):
    """dtype=0: gmem bytes match tile.astype('<f4').tobytes() exactly."""
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)

    tile = _det_tile(0)
    await _seed_tile(dut, slot=0, tile=tile)

    await _issue_and_wait(dut, tmem_slot=0, gmem_ptr=0, dtype=0)

    nbytes = TILE_BYTES
    got = await _read_gmem(dut, addr=0, nbytes=nbytes)
    expected = bytes(np.ascontiguousarray(tile.astype("<f4")).tobytes())
    assert got == expected, (
        f"fp32 gmem bytes mismatch. "
        f"first diff at byte {next(i for i, (a, b) in enumerate(zip(got, expected)) if a != b)}"
    )


@cocotb.test()
async def test_store_fp8(dut):
    """dtype=1: gmem bytes match encode_e4m3(tile) exactly; decoded approximates tile."""
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)

    tile = _det_tile(1)
    await _seed_tile(dut, slot=0, tile=tile)

    await _issue_and_wait(dut, tmem_slot=0, gmem_ptr=0, dtype=1)

    nbytes = MMA_M * MMA_N
    got = await _read_gmem(dut, addr=0, nbytes=nbytes)
    expected_codes = encode_e4m3(tile).reshape(-1)
    expected = bytes(np.ascontiguousarray(expected_codes))
    assert got == expected, (
        f"fp8 gmem byte codes mismatch. "
        f"got[:8]={list(got[:8])} expected[:8]={list(expected[:8])}"
    )

    # Decode and verify quantization tolerance.
    decoded = decode_e4m3(
        np.frombuffer(got, dtype=np.uint8)
    ).reshape(MMA_M, MMA_N)
    np.testing.assert_allclose(decoded, tile, rtol=0.15, atol=0.1)


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """5 random tiles x 2 dtypes; compare gmem byte-by-byte vs pymodel.Store."""
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)

    rng = random.Random(0xBEEF)

    # We seed slots 0..N independently. Use distinct gmem_ptrs (BEAT-aligned)
    # so each run writes to a fresh region we can verify.
    region_bytes = max(TILE_BYTES, MMA_M * MMA_N)  # both fit
    # Round up to BEAT_BYTES alignment.
    assert region_bytes % BEAT_BYTES == 0
    cur_ptr = 0

    for trial in range(5):
        for dtype in (0, 1):
            seed = rng.randint(0, 2**31 - 1)
            scale = rng.choice([0.1, 0.5, 1.0, 5.0])
            tile = np.random.RandomState(seed).randn(MMA_M, MMA_N).astype(np.float32) * scale

            # Mirror pymodel side: tick Store on a fresh GMEM/ComputeArray
            # to get expected bytes.
            py_ca = ComputeArray()
            py_gmem = GMEM()
            py_store = Store(py_ca, py_gmem)
            py_ca.set_tile(0, tile)
            py_store.tick(issue_en=1, tmem_slot=0, gmem_ptr=cur_ptr, dtype=dtype)
            while py_store.busy:
                py_store.tick()
            expected_nbytes = TILE_BYTES if dtype == 0 else MMA_M * MMA_N
            expected = py_gmem.dump(cur_ptr, expected_nbytes)

            # SV side: seed slot 0 with the same tile, then run STORE.
            await _seed_tile(dut, slot=0, tile=tile)
            await _issue_and_wait(dut, tmem_slot=0, gmem_ptr=cur_ptr, dtype=dtype)

            got = await _read_gmem(dut, addr=cur_ptr, nbytes=expected_nbytes)
            assert got == expected, (
                f"trial={trial} dtype={dtype} scale={scale}: SV vs pymodel byte mismatch. "
                f"first diff at byte "
                f"{next((i for i, (a, b) in enumerate(zip(got, expected)) if a != b), 'tail')}. "
                f"len got={len(got)} expected={len(expected)}"
            )

            cur_ptr += region_bytes
