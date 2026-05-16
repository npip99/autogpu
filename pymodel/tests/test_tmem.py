"""Tests for pymodel.tmem."""

import numpy as np
import pytest

from config import MMA_M, MMA_N, TMEM_SLOTS
from pymodel.tmem import MMAOp, TMEM


def _tile(seed: int) -> np.ndarray:
    return np.random.RandomState(seed).randn(MMA_M, MMA_N).astype(np.float32)


def test_write_then_read_same_slot():
    t = TMEM()
    tile = _tile(0)
    t.tick(mma_op=MMAOp.WRITE, mma_slot=0, mma_write_tile=tile)
    t.tick(store_rd_en=1, store_rd_slot=0)
    t.tick()
    assert t.store_rd_valid == 1
    np.testing.assert_array_equal(t.store_rd_tile, tile)


def test_parallel_reads_different_slots():
    t = TMEM()
    tile0 = _tile(0)
    tile1 = _tile(1)
    t.set_slot(0, tile0)
    t.set_slot(1, tile1)
    t.tick(mma_op=MMAOp.READ, mma_slot=0, store_rd_en=1, store_rd_slot=1)
    t.tick()
    np.testing.assert_array_equal(t.mma_rd_tile, tile0)
    np.testing.assert_array_equal(t.store_rd_tile, tile1)


def test_write_persists():
    t = TMEM()
    tile = _tile(2)
    t.tick(mma_op=MMAOp.WRITE, mma_slot=2, mma_write_tile=tile)
    for _ in range(5):
        t.tick()
    t.tick(store_rd_en=1, store_rd_slot=2)
    t.tick()
    np.testing.assert_array_equal(t.store_rd_tile, tile)


def test_backdoor_roundtrip():
    t = TMEM()
    tile = _tile(3)
    t.set_slot(0, tile)
    np.testing.assert_array_equal(t.get_slot(0), tile)


def test_slot_out_of_range_asserts():
    t = TMEM()
    with pytest.raises(AssertionError):
        t.tick(mma_op=MMAOp.READ, mma_slot=TMEM_SLOTS)


def test_wrong_shape_or_dtype_asserts():
    t = TMEM()
    with pytest.raises(AssertionError):
        t.tick(
            mma_op=MMAOp.WRITE,
            mma_slot=0,
            mma_write_tile=np.zeros((1, 1), dtype=np.float32),
        )
    with pytest.raises(AssertionError):
        t.tick(
            mma_op=MMAOp.WRITE,
            mma_slot=0,
            mma_write_tile=np.zeros((MMA_M, MMA_N), dtype=np.float64),
        )
