"""Tests for pymodel.smem."""

# from pymodel.smem import SMEM


def test_load_then_read_a():
    """Backdoor-load a tile; MMA_RD_A reads first column; next cycle data matches."""
    raise NotImplementedError


def test_parallel_reads_different_ports():
    """MMA_RD_A reads addr X and MMA_RD_B reads addr Y in same cycle; both return correct data at T+1."""
    raise NotImplementedError


def test_wr_then_rd_next_cycle():
    """LOAD_WR a beat at T; MMA_RD_A reads same addr at T+1; returns the written beat at T+2."""
    raise NotImplementedError


def test_wr_rd_overlap_same_cycle_asserts():
    """LOAD_WR + MMA_RD_A to overlapping addresses in the same cycle asserts."""
    raise NotImplementedError


def test_unaligned_addr_asserts():
    """Per-port alignment violations assert."""
    raise NotImplementedError


def test_backdoor_roundtrip():
    """load() / dump() are inverses of each other and consistent with port access."""
    raise NotImplementedError


def test_read_latency_exact_one():
    """rd_en at T → rd_valid==0 at T, ==1 at T+1, ==0 at T+2 (unless re-issued)."""
    raise NotImplementedError
