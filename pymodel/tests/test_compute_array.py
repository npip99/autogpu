"""Tests for pymodel.compute_array."""

import numpy as np

from config import MMA_K, MMA_M, MMA_N, TMEM_SLOTS
from golden.fp8 import decode_e4m3, encode_e4m3
from pymodel.compute_array import ComputeArray


def _fp32_bits(x: float) -> int:
    return int(np.array([np.float32(x)], dtype=np.float32).view(np.uint32)[0])


def _bits_to_fp32(b: int) -> float:
    return float(np.array([b & 0xFFFFFFFF], dtype=np.uint32).view(np.float32)[0])


def _run_matmul(
    arr: ComputeArray,
    A_fp8: np.ndarray,  # (MMA_M, MMA_K) bytes
    B_fp8: np.ndarray,  # (MMA_K, MMA_N) bytes
    slot: int,
    accum: int,
    bar_id: int = 0,
    max_cycles: int = MMA_K + MMA_M + MMA_N + 50,
) -> int:
    """Drive a matmul with a synthetic SMEM model. A is column-major in SMEM;
    B is row-major. We deliver one column of A and one row of B per K cycle,
    with the standard 1-cycle read latency: rd_a_en at T → rd_a_valid at T+1.

    Returns the cycle (0-indexed) on which mma_done was observed.
    """
    # A col k = A[:, k] (MMA_M bytes); B row k = B[k, :] (MMA_N bytes).
    a_off = 0
    b_off = MMA_M * MMA_K  # arbitrary fixed offsets in the faux SMEM
    a_stride = MMA_M
    b_stride = MMA_N

    # Pending SMEM responses for next tick.
    pending_a: tuple[int, bytes] | None = None  # (addr, data)
    pending_b: tuple[int, bytes] | None = None

    cycle = 0
    # Issue tick.
    arr.tick(
        mma_issue=1,
        mma_slot=slot,
        mma_accum=accum,
        mma_bar_id=bar_id,
        issue_a_off=a_off,
        issue_b_off=b_off,
        issue_a_stride=a_stride,
        issue_b_stride=b_stride,
    )
    # After this tick, arr.rd_a_en / rd_a_addr reflect column-0 issue.
    if arr.rd_a_en:
        a_idx = (arr.rd_a_addr - a_off) // a_stride
        pending_a = (arr.rd_a_addr, bytes(A_fp8[:, a_idx]))
    if arr.rd_b_en:
        b_idx = (arr.rd_b_addr - b_off) // b_stride
        pending_b = (arr.rd_b_addr, bytes(B_fp8[b_idx, :]))

    cycle = 1
    while cycle < max_cycles:
        # Deliver pending responses this tick (1-cycle SMEM latency).
        rd_a_data = pending_a[1] if pending_a else bytes(MMA_M)
        rd_a_valid = 1 if pending_a else 0
        rd_b_data = pending_b[1] if pending_b else bytes(MMA_N)
        rd_b_valid = 1 if pending_b else 0

        # Capture this cycle's NEW issue (will be served next tick).
        prev_rd_a_en = arr.rd_a_en
        prev_rd_a_addr = arr.rd_a_addr
        prev_rd_b_en = arr.rd_b_en
        prev_rd_b_addr = arr.rd_b_addr

        arr.tick(
            rd_a_data=rd_a_data,
            rd_a_valid=rd_a_valid,
            rd_b_data=rd_b_data,
            rd_b_valid=rd_b_valid,
        )

        # New pending = whatever the array drove THIS cycle (registered).
        # Note: arr.rd_a_en after tick reflects the *new* registered drive
        # for next cycle. The "served this cycle" reads were from the
        # PRIOR registered drive (prev_rd_a_en).
        if prev_rd_a_en:
            # We already served this cycle; clear pending.
            pending_a = None
        if prev_rd_b_en:
            pending_b = None
        if arr.rd_a_en:
            a_idx = (arr.rd_a_addr - a_off) // a_stride
            pending_a = (arr.rd_a_addr, bytes(A_fp8[:, a_idx]))
        if arr.rd_b_en:
            b_idx = (arr.rd_b_addr - b_off) // b_stride
            pending_b = (arr.rd_b_addr, bytes(B_fp8[b_idx, :]))

        if arr.mma_done:
            return cycle
        cycle += 1
    raise AssertionError(f"mma_done never pulsed within {max_cycles} cycles")


def test_single_matmul_no_accum():
    arr = ComputeArray()
    rng = np.random.RandomState(0)
    A_fp32 = (rng.randn(MMA_M, MMA_K) * 0.5).astype(np.float32)
    B_fp32 = (rng.randn(MMA_K, MMA_N) * 0.5).astype(np.float32)
    A_fp8 = encode_e4m3(A_fp32)
    B_fp8 = encode_e4m3(B_fp32)

    _run_matmul(arr, A_fp8, B_fp8, slot=0, accum=0)

    expected = (decode_e4m3(A_fp8) @ decode_e4m3(B_fp8)).astype(np.float32)
    got = arr.get_tile(0)
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-5)


def test_matmul_accum():
    arr = ComputeArray()
    # Pre-seed slot 1 with a known tile.
    D0 = np.random.RandomState(7).randn(MMA_M, MMA_N).astype(np.float32)
    arr.set_tile(1, D0)
    assert np.array_equal(arr.get_tile(1), D0)

    rng = np.random.RandomState(1)
    A_fp32 = (rng.randn(MMA_M, MMA_K) * 0.3).astype(np.float32)
    B_fp32 = (rng.randn(MMA_K, MMA_N) * 0.3).astype(np.float32)
    A_fp8 = encode_e4m3(A_fp32)
    B_fp8 = encode_e4m3(B_fp32)

    _run_matmul(arr, A_fp8, B_fp8, slot=1, accum=1)

    expected = D0 + (decode_e4m3(A_fp8) @ decode_e4m3(B_fp8)).astype(np.float32)
    got = arr.get_tile(1)
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-5)


def test_drain_outputs_correct_rows():
    arr = ComputeArray()
    # Seed slot 0 with a known tile.
    tile = (
        np.arange(MMA_M * MMA_N, dtype=np.float32).reshape(MMA_M, MMA_N) * 0.25
        - 7.0
    )
    arr.set_tile(0, tile)

    # Issue drain.
    arr.tick(drain_issue=1, drain_slot=0)

    # Pump cycles, collecting drain_row_data per cycle when drain_row_valid.
    rows: dict[int, np.ndarray] = {}
    saw_done = False
    saw_last = False

    for _ in range(MMA_M + 5):
        arr.tick()
        if arr.drain_row_valid:
            idx = arr.drain_row_idx
            packed = arr.drain_row_data
            row = np.zeros((MMA_N,), dtype=np.float32)
            for j in range(MMA_N):
                word = (packed >> (j * 32)) & 0xFFFFFFFF
                row[j] = _bits_to_fp32(word)
            rows[idx] = row
            if arr.drain_last:
                saw_last = True
        if arr.drain_done:
            saw_done = True

    assert saw_last, "drain_last never pulsed"
    assert saw_done, "drain_done never pulsed"
    assert set(rows.keys()) == set(range(MMA_M))
    for i in range(MMA_M):
        np.testing.assert_array_equal(rows[i], tile[i, :])


def test_scrub_clears_all_slots_via_array():
    arr = ComputeArray()
    # Seed each slot with a distinct pattern.
    for s in range(TMEM_SLOTS):
        arr.set_tile(s, np.full((MMA_M, MMA_N), float(s + 1), dtype=np.float32))

    # Confirm pre-state nonzero.
    for s in range(TMEM_SLOTS):
        assert np.any(arr.get_tile(s) != 0)

    arr.tick(scrub_en=1)

    for s in range(TMEM_SLOTS):
        np.testing.assert_array_equal(
            arr.get_tile(s), np.zeros((MMA_M, MMA_N), dtype=np.float32)
        )


def test_back_to_back_matmuls():
    arr = ComputeArray()
    rng = np.random.RandomState(42)

    A0 = encode_e4m3((rng.randn(MMA_M, MMA_K) * 0.4).astype(np.float32))
    B0 = encode_e4m3((rng.randn(MMA_K, MMA_N) * 0.4).astype(np.float32))
    _run_matmul(arr, A0, B0, slot=0, accum=0)
    expect0 = (decode_e4m3(A0) @ decode_e4m3(B0)).astype(np.float32)

    A1 = encode_e4m3((rng.randn(MMA_M, MMA_K) * 0.4).astype(np.float32))
    B1 = encode_e4m3((rng.randn(MMA_K, MMA_N) * 0.4).astype(np.float32))
    _run_matmul(arr, A1, B1, slot=1, accum=0)
    expect1 = (decode_e4m3(A1) @ decode_e4m3(B1)).astype(np.float32)

    np.testing.assert_allclose(arr.get_tile(0), expect0, rtol=0, atol=1e-5)
    np.testing.assert_allclose(arr.get_tile(1), expect1, rtol=0, atol=1e-5)


def test_busy_blocks_issue():
    import pytest

    arr = ComputeArray()
    arr.tick(mma_issue=1, mma_slot=0)
    assert arr.mma_busy == 1
    # Now a second issue while busy must assert.
    with pytest.raises(AssertionError):
        arr.tick(mma_issue=1, mma_slot=1)
