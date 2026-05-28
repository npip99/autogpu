"""Tests for pymodel.smem."""

import pytest

from config import BEAT_BYTES, MMA_M, MMA_N, SMEM_BYTES, SMEM_TILE_BASE
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
    """Post-B1: rd_a from region 0 (A's region), rd_b from region 1 (B's)."""
    s = SMEM()
    pat_a = _pat(0, MMA_M)
    pat_b = _pat(100, MMA_N)
    a_addr = SMEM_TILE_BASE                 # region 0
    b_addr = SMEM_BYTES // 4                # region 1 (4096 = SMEM_BYTES/4)
    s.load(a_addr, pat_a)
    s.load(b_addr, pat_b)
    s.tick(
        rd_a_en=1, rd_a_addr=a_addr,
        rd_b_en=1, rd_b_addr=b_addr,
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


# ---------------------------------------------------------------------------
# Bank-conflict tests. The 32-bank scratchpad's stall protocol prioritizes
# LOAD_WR > MMA_RD_A > MMA_RD_B (highest first). With our alignment
# guarantees, two ports' bank ranges overlap iff their 8-bank-group indices
# `addr[6:5]` match.
# ---------------------------------------------------------------------------


def test_no_conflict_concurrent_3ports():
    """LOAD_WR + MMA_RD_A + MMA_RD_B targeting disjoint regions → no stalls.

    Post-B1: SMEM is partitioned into 4 regions of SMEM_BYTES/4 bytes each
    (one region per 8-bank set, picked by addr[13:12]). Two ports targeting
    DIFFERENT regions never conflict.
    """
    s = SMEM()
    # wr_addr in region 2; rd_a in region 0; rd_b in region 1 — all different.
    wr_addr   = 2 * (SMEM_BYTES // 4)        # region 2 (8192)
    rd_a_addr = SMEM_TILE_BASE               # region 0
    rd_b_addr = SMEM_BYTES // 4              # region 1 (4096)

    pat_w = _pat(0x10, BEAT_BYTES)
    pat_a = _pat(0x40, MMA_M)
    pat_b = _pat(0x80, MMA_N)
    # Pre-load the read targets via back-door (LOAD_WR will only put pat_w
    # into 16 bytes of group 0, which doesn't overlap our read addrs).
    s.load(rd_a_addr, pat_a)
    s.load(rd_b_addr, pat_b)

    s.tick(
        wr_en=1, wr_addr=wr_addr, wr_data=pat_w,
        rd_a_en=1, rd_a_addr=rd_a_addr,
        rd_b_en=1, rd_b_addr=rd_b_addr,
    )
    # No stalls — all three groups disjoint.
    assert s.load_wr_stall_out == 0
    assert s.mma_rd_a_stall_out == 0
    assert s.mma_rd_b_stall_out == 0

    # Drain cycle: reads land.
    s.tick()
    assert s.rd_a_valid == 1
    assert s.rd_a_data == pat_a
    assert s.rd_b_valid == 1
    assert s.rd_b_data == pat_b
    # Write committed too.
    assert s.dump(wr_addr, BEAT_BYTES) == pat_w


def test_rd_a_rd_b_conflict():
    """RD_A and RD_B target overlapping bank groups → RD_B stalls one cycle, then completes."""
    s = SMEM()
    # Both addresses in the SAME 8-bank group (group 0).
    rd_a_addr = SMEM_TILE_BASE                  # group 0
    rd_b_addr = SMEM_TILE_BASE + 128            # group 0 (next 128-byte block, same group)

    pat_a = _pat(0xA0, MMA_M)
    pat_b = _pat(0xB0, MMA_N)
    s.load(rd_a_addr, pat_a)
    s.load(rd_b_addr, pat_b)

    # Cycle T: drive both. RD_A wins, RD_B stalls.
    s.tick(
        rd_a_en=1, rd_a_addr=rd_a_addr,
        rd_b_en=1, rd_b_addr=rd_b_addr,
    )
    assert s.load_wr_stall_out == 0
    assert s.mma_rd_a_stall_out == 0
    assert s.mma_rd_b_stall_out == 1

    # Cycle T+1: RD_A drains (pat_a). Consumer re-issues RD_B alone.
    s.tick(rd_b_en=1, rd_b_addr=rd_b_addr)
    assert s.rd_a_valid == 1
    assert s.rd_a_data == pat_a
    assert s.mma_rd_b_stall_out == 0  # no conflict now (no other port active)

    # Cycle T+2: RD_B drains.
    s.tick()
    assert s.rd_b_valid == 1
    assert s.rd_b_data == pat_b


def test_load_vs_rd_conflict():
    """LOAD_WR and MMA_RD_A target overlapping bank groups → RD_A stalls, LOAD wins."""
    s = SMEM()
    # LOAD_WR at group 0; RD_A at group 0 with a NON-overlapping byte range
    # (so we don't trip the byte-overlap assertion). LOAD writes 16 bytes
    # starting at 0; RD_A reads 32 bytes starting at SMEM_TILE_BASE
    # (=128). bytes [0,16) and [128,160) are disjoint, but group_of(0)=0
    # and group_of(128)=0 — bank conflict.
    wr_addr = 0
    rd_a_addr = SMEM_TILE_BASE  # 128

    def _grp(a: int) -> int:
        return (a >> 5) & 0x3

    assert _grp(wr_addr) == _grp(rd_a_addr) == 0

    pat_w = _pat(0x11, BEAT_BYTES)
    pat_a = _pat(0xAA, MMA_M)
    s.load(rd_a_addr, pat_a)

    # Cycle T: both drive. RD_A stalls (LOAD wins).
    s.tick(
        wr_en=1, wr_addr=wr_addr, wr_data=pat_w,
        rd_a_en=1, rd_a_addr=rd_a_addr,
    )
    assert s.load_wr_stall_out == 0
    assert s.mma_rd_a_stall_out == 1

    # LOAD committed (RD_A's stall didn't block the write).
    assert s.dump(wr_addr, BEAT_BYTES) == pat_w

    # Cycle T+1: RD_A re-issued alone.
    s.tick(rd_a_en=1, rd_a_addr=rd_a_addr)
    assert s.mma_rd_a_stall_out == 0

    # Cycle T+2: RD_A drains.
    s.tick()
    assert s.rd_a_valid == 1
    assert s.rd_a_data == pat_a
