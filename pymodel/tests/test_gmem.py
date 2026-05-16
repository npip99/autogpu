"""
Tests for pymodel.gmem.

Each test follows the contract from pymodel/gmem.py. Implementation is TODO;
test bodies are TODO. Filled in by the implementer agent.
"""

# import pytest
# from pymodel.gmem import GMEM
# from config import GMEM_BYTES, BEAT_BYTES


def test_write_then_read_roundtrip():
    """Write a beat to addr 0, next cycle read addr 0, check pattern returns."""
    raise NotImplementedError


def test_read_latency_is_one():
    """rd_en at cycle T → rd_valid==0 at T, ==1 at T+1, ==0 at T+2."""
    raise NotImplementedError


def test_concurrent_rw_different_addresses():
    """Same-cycle rd_en(A) + wr_en(B), A != B. Both succeed."""
    raise NotImplementedError


def test_wr_persists():
    """Write at T=0, no further activity, read at T=10 returns the written data."""
    raise NotImplementedError


def test_reset_clears_pending_read():
    """rd_en at T → reset at T+1 → rd_valid at T+1 is 0 (no late return)."""
    raise NotImplementedError


def test_backdoor_load_dump_roundtrip():
    """gmem.load(0, blob); gmem.dump(0, len(blob)) returns blob exactly."""
    raise NotImplementedError


def test_backdoor_visible_to_port():
    """load() then read via rd_en port returns the same bytes."""
    raise NotImplementedError


def test_assert_unaligned_addr():
    """rd_en with addr=1 raises AssertionError (not BEAT_BYTES-aligned)."""
    raise NotImplementedError


def test_assert_overlap_rw_same_cycle():
    """Same-cycle rd_en(0) + wr_en(0) raises AssertionError."""
    raise NotImplementedError
