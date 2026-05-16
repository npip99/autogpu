"""Tests for pymodel.gmem."""

import pytest

from config import BEAT_BYTES, GMEM_BYTES
from pymodel.gmem import GMEM


def _pat(base: int, length: int = BEAT_BYTES) -> bytes:
    return bytes((base + i) & 0xFF for i in range(length))


def test_write_then_read_roundtrip():
    g = GMEM()
    pat = _pat(0)
    g.tick(wr_en=1, wr_addr=0, wr_data=pat)
    g.tick(rd_en=1, rd_addr=0)
    g.tick()  # drain
    assert g.rd_valid == 1
    assert g.rd_data == pat


def test_read_latency_is_one():
    """rd_en at T → valid==0 at T, valid==1 at T+1, valid==0 at T+2."""
    g = GMEM()
    g.load(0, _pat(0))
    g.tick(rd_en=1, rd_addr=0)
    assert g.rd_valid == 0
    g.tick()
    assert g.rd_valid == 1
    g.tick()
    assert g.rd_valid == 0


def test_concurrent_rw_different_addresses():
    g = GMEM()
    g.load(BEAT_BYTES, _pat(BEAT_BYTES))
    pat_w = _pat(0)
    g.tick(rd_en=1, rd_addr=BEAT_BYTES, wr_en=1, wr_addr=0, wr_data=pat_w)
    g.tick()
    assert g.rd_valid == 1
    assert g.rd_data == _pat(BEAT_BYTES)
    assert g.dump(0, BEAT_BYTES) == pat_w


def test_wr_persists():
    g = GMEM()
    pat = _pat(0)
    g.tick(wr_en=1, wr_addr=0, wr_data=pat)
    for _ in range(10):
        g.tick()
    g.tick(rd_en=1, rd_addr=0)
    g.tick()
    assert g.rd_data == pat


def test_reset_clears_pending_read():
    g = GMEM()
    g.load(0, _pat(0))
    g.tick(rd_en=1, rd_addr=0)
    g.tick(reset=1)
    assert g.rd_valid == 0
    assert g.rd_data == b"\x00" * BEAT_BYTES


def test_backdoor_load_dump_roundtrip():
    g = GMEM()
    blob = bytes(range(50))
    g.load(0, blob)
    assert g.dump(0, len(blob)) == blob


def test_backdoor_visible_to_port():
    g = GMEM()
    pat = _pat(0)
    g.load(0, pat)
    g.tick(rd_en=1, rd_addr=0)
    g.tick()
    assert g.rd_data == pat


def test_assert_unaligned_addr():
    g = GMEM()
    with pytest.raises(AssertionError):
        g.tick(rd_en=1, rd_addr=1)


def test_assert_overlap_rw_same_cycle():
    g = GMEM()
    with pytest.raises(AssertionError):
        g.tick(rd_en=1, rd_addr=0, wr_en=1, wr_addr=0, wr_data=_pat(0))
