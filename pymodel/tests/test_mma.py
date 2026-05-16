"""Tests for pymodel.mma."""

import numpy as np
import pytest

from config import MMA_K, MMA_M, MMA_N, SMEM_TILE_BASE
from golden.fp8 import decode_e4m3, encode_e4m3
from golden.matmul_reference import generate
from pymodel.mma import MMA
from pymodel.smem import SMEM
from pymodel.tmem import TMEM


def _run_to_done(mma: MMA, max_cycles: int = MMA_K + 10) -> int:
    """Run mma.tick() until done pulses. Returns cycles elapsed (incl. start tick)."""
    for cyc in range(1, max_cycles + 1):
        mma.tick()
        if mma.done:
            return cyc + 1  # +1 for the start tick caller already did
    raise AssertionError("MMA did not signal done within max_cycles")


def _load_tiles(smem: SMEM, a_off: int, b_off: int, A_fp8: np.ndarray, B_fp8: np.ndarray):
    """A column-major, B row-major, both at given SMEM offsets."""
    A_bytes = bytes(np.ascontiguousarray(A_fp8.T).reshape(-1))
    B_bytes = bytes(np.ascontiguousarray(B_fp8).reshape(-1))
    smem.load(a_off, A_bytes)
    smem.load(b_off, B_bytes)


def test_accum0_known_inputs():
    smem = SMEM()
    tmem = TMEM()
    mma = MMA(smem, tmem)
    rng = np.random.RandomState(0)
    A_fp32 = (rng.randn(MMA_M, MMA_K) * 0.5).astype(np.float32)
    B_fp32 = (rng.randn(MMA_K, MMA_N) * 0.5).astype(np.float32)
    A_fp8 = encode_e4m3(A_fp32)
    B_fp8 = encode_e4m3(B_fp32)
    a_off = SMEM_TILE_BASE
    b_off = SMEM_TILE_BASE + MMA_M * MMA_K
    _load_tiles(smem, a_off, b_off, A_fp8, B_fp8)

    mma.tick(start=1, a_smem_offset=a_off, b_smem_offset=b_off,
             d_tmem_slot=0, accum=0, bar_id=0)
    _run_to_done(mma)

    expected = (decode_e4m3(A_fp8) @ decode_e4m3(B_fp8)).astype(np.float32)
    np.testing.assert_allclose(tmem.get_slot(0), expected, rtol=0, atol=1e-5)


def test_accum1_adds_into_existing():
    smem = SMEM()
    tmem = TMEM()
    mma = MMA(smem, tmem)
    D0 = np.random.RandomState(7).randn(MMA_M, MMA_N).astype(np.float32)
    tmem.set_slot(0, D0)

    rng = np.random.RandomState(1)
    A_fp32 = (rng.randn(MMA_M, MMA_K) * 0.3).astype(np.float32)
    B_fp32 = (rng.randn(MMA_K, MMA_N) * 0.3).astype(np.float32)
    A_fp8 = encode_e4m3(A_fp32)
    B_fp8 = encode_e4m3(B_fp32)
    a_off = SMEM_TILE_BASE
    b_off = SMEM_TILE_BASE + MMA_M * MMA_K
    _load_tiles(smem, a_off, b_off, A_fp8, B_fp8)

    mma.tick(start=1, a_smem_offset=a_off, b_smem_offset=b_off,
             d_tmem_slot=0, accum=1, bar_id=0)
    _run_to_done(mma)

    expected = D0 + (decode_e4m3(A_fp8) @ decode_e4m3(B_fp8)).astype(np.float32)
    np.testing.assert_allclose(tmem.get_slot(0), expected, rtol=0, atol=1e-5)


def test_barrier_arrival_on_done():
    smem = SMEM()
    tmem = TMEM()
    mma = MMA(smem, tmem)
    mma.tick(start=1, a_smem_offset=SMEM_TILE_BASE,
             b_smem_offset=SMEM_TILE_BASE + MMA_M * MMA_K,
             d_tmem_slot=0, accum=0, bar_id=3)
    # Run until done; check arrive_en pulsed with the right bar_id.
    saw_arrive = False
    saw_bar_id = None
    for _ in range(MMA_K + 5):
        mma.tick()
        if mma.arrive_en:
            saw_arrive = True
            saw_bar_id = mma.arrive_bar_id
            break
    assert saw_arrive
    assert saw_bar_id == 3


def test_random_via_golden():
    smem = SMEM()
    tmem = TMEM()
    mma = MMA(smem, tmem)
    A_bytes, B_bytes, C_expected = generate(MMA_M, MMA_N, MMA_K, seed=123)
    a_off = SMEM_TILE_BASE
    b_off = SMEM_TILE_BASE + MMA_M * MMA_K
    smem.load(a_off, A_bytes)
    smem.load(b_off, B_bytes)

    mma.tick(start=1, a_smem_offset=a_off, b_smem_offset=b_off,
             d_tmem_slot=0, accum=0, bar_id=0)
    _run_to_done(mma)
    np.testing.assert_allclose(tmem.get_slot(0), C_expected, rtol=0, atol=1e-5)


def test_busy_blocks_start():
    smem = SMEM()
    tmem = TMEM()
    mma = MMA(smem, tmem)
    mma.tick(start=1, a_smem_offset=SMEM_TILE_BASE,
             b_smem_offset=SMEM_TILE_BASE + MMA_M * MMA_K,
             d_tmem_slot=0, accum=0, bar_id=0)
    # mma now busy
    with pytest.raises(AssertionError):
        mma.tick(start=1, a_smem_offset=SMEM_TILE_BASE,
                 b_smem_offset=SMEM_TILE_BASE + MMA_M * MMA_K,
                 d_tmem_slot=1, accum=0, bar_id=0)


def test_latency_exact():
    """done pulses exactly MMA_K + 1 cycles after the start tick (impl convention)."""
    smem = SMEM()
    tmem = TMEM()
    mma = MMA(smem, tmem)
    mma.tick(start=1, a_smem_offset=SMEM_TILE_BASE,
             b_smem_offset=SMEM_TILE_BASE + MMA_M * MMA_K,
             d_tmem_slot=0, accum=0, bar_id=0)
    for i in range(1, MMA_K + 5):
        mma.tick()
        if mma.done:
            assert i == MMA_K + 1, f"expected done at cycle MMA_K+1={MMA_K + 1}, saw at {i}"
            return
    raise AssertionError("MMA never signaled done")
