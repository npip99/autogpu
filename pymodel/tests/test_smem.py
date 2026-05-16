"""Tests for pymodel.smem."""

import pytest

from config import BEAT_BYTES, MMA_M, MMA_N, SMEM_TILE_BASE
from pymodel.smem import SMEM


def _pat(base: int, length: int) -> bytes:
    return bytes((base + i) & 0xFF for i in range(length))


def test_load_then_read_a():
    s = SMEM()
    pat = _pat(0, MMA_M)
    s.load(SMEM_TILE_BASE, pat)
    s.tick(rd_a_en=1, rd_a_addr=SMEM_TILE_BASE)
    s.tick()
    assert s.rd_a_valid == 1
    assert s.rd_a_data == pat


def test_parallel_reads_different_ports():
    s = SMEM()
    pat_a = _pat(0, MMA_M)
    pat_b = _pat(100, MMA_N)
    s.load(SMEM_TILE_BASE, pat_a)
    s.load(SMEM_TILE_BASE + MMA_M, pat_b)
    s.tick(
        rd_a_en=1, rd_a_addr=SMEM_TILE_BASE,
        rd_b_en=1, rd_b_addr=SMEM_TILE_BASE + MMA_M,
    )
    s.tick()
    assert s.rd_a_data == pat_a
    assert s.rd_b_data == pat_b


def test_wr_then_rd_next_cycle():
    s = SMEM()
    # Write a BEAT_BYTES beat to addr 0, then on next cycle do an MMA_M-wide read at addr 0.
    # rd_a covers 0..MMA_M; we only wrote 0..BEAT_BYTES. Initialize the rest of the
    # rd_a window via back-door so we have a known expected value.
    pat_w = _pat(0, BEAT_BYTES)
    tail = _pat(BEAT_BYTES, MMA_M - BEAT_BYTES)
    s.load(BEAT_BYTES, tail)
    s.tick(wr_en=1, wr_addr=0, wr_data=pat_w)
    s.tick(rd_a_en=1, rd_a_addr=0)
    s.tick()
    assert s.rd_a_data == pat_w + tail


def test_wr_rd_overlap_same_cycle_asserts():
    s = SMEM()
    with pytest.raises(AssertionError):
        s.tick(
            wr_en=1, wr_addr=0, wr_data=_pat(0, BEAT_BYTES),
            rd_a_en=1, rd_a_addr=0,
        )


def test_unaligned_addr_asserts():
    s = SMEM()
    with pytest.raises(AssertionError):
        s.tick(rd_a_en=1, rd_a_addr=1)
    s = SMEM()
    with pytest.raises(AssertionError):
        s.tick(rd_b_en=1, rd_b_addr=1)
    s = SMEM()
    with pytest.raises(AssertionError):
        s.tick(wr_en=1, wr_addr=1, wr_data=_pat(0, BEAT_BYTES))


def test_backdoor_roundtrip():
    s = SMEM()
    blob = bytes(range(64))
    s.load(SMEM_TILE_BASE, blob)
    assert s.dump(SMEM_TILE_BASE, len(blob)) == blob


def test_read_latency_exact_one():
    s = SMEM()
    s.tick(rd_a_en=1, rd_a_addr=0)
    assert s.rd_a_valid == 0
    s.tick()
    assert s.rd_a_valid == 1
    s.tick()
    assert s.rd_a_valid == 0
