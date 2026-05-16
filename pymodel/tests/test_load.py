"""Tests for pymodel.load."""

import pytest

from config import BEAT_BYTES, SMEM_TILE_BASE
from pymodel.gmem import GMEM
from pymodel.load import Load
from pymodel.smem import SMEM


def _pat(base: int, length: int) -> bytes:
    return bytes((base + i) & 0xFF for i in range(length))


def _run_until_idle(load: Load, max_cycles: int = 1000) -> int:
    for cyc in range(max_cycles):
        load.tick()
        if not load.busy:
            return cyc + 1
    raise AssertionError("LOAD did not become idle")


def test_single_load():
    gmem = GMEM()
    smem = SMEM()
    load = Load(gmem, smem)
    pat = _pat(0, 1024)
    gmem.load(0, pat)

    load.tick(issue_en=1, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE,
              bytes_n=1024, bar_id=0)
    _run_until_idle(load)
    assert smem.dump(SMEM_TILE_BASE, 1024) == pat


def test_barrier_accounting():
    """Verify add_tx on accept, sub_tx + arrive on completion."""
    gmem = GMEM()
    smem = SMEM()
    load = Load(gmem, smem)
    gmem.load(0, _pat(0, 1024))

    load.tick(issue_en=1, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE,
              bytes_n=1024, bar_id=2)
    # On issue cycle: add_tx pulses with full byte count.
    assert load.accept == 1
    assert load.add_tx_en == 1
    assert load.add_tx_bytes == 1024
    assert load.add_tx_bar_id == 2

    saw_sub = False
    saw_arrive = False
    for _ in range(1024 // BEAT_BYTES + 5):
        load.tick()
        if load.sub_tx_en:
            saw_sub = True
            assert load.sub_tx_bar_id == 2
            assert load.sub_tx_bytes == 1024
        if load.arrive_en:
            saw_arrive = True
            assert load.arrive_bar_id == 2
        if not load.busy:
            break
    assert saw_sub and saw_arrive


def test_two_loads_queued():
    """Push two LOADs back-to-back; both complete with correct smem contents."""
    gmem = GMEM()
    smem = SMEM()
    load = Load(gmem, smem)
    pat_a = _pat(0, 256)
    pat_b = _pat(100, 256)
    gmem.load(0, pat_a)
    gmem.load(256, pat_b)

    load.tick(issue_en=1, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE,
              bytes_n=256, bar_id=0)
    assert load.accept == 1
    load.tick(issue_en=1, gmem_ptr=256, smem_ptr=SMEM_TILE_BASE + 256,
              bytes_n=256, bar_id=0)
    assert load.accept == 1

    _run_until_idle(load)
    assert smem.dump(SMEM_TILE_BASE, 256) == pat_a
    assert smem.dump(SMEM_TILE_BASE + 256, 256) == pat_b


def test_multi_beat_correctness():
    gmem = GMEM()
    smem = SMEM()
    load = Load(gmem, smem)
    nbeats = 8
    nbytes = nbeats * BEAT_BYTES
    pat = _pat(0, nbytes)
    gmem.load(0, pat)

    load.tick(issue_en=1, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE,
              bytes_n=nbytes, bar_id=0)
    _run_until_idle(load)
    assert smem.dump(SMEM_TILE_BASE, nbytes) == pat


def test_unaligned_bytes_asserts():
    gmem = GMEM()
    smem = SMEM()
    load = Load(gmem, smem)
    with pytest.raises(AssertionError):
        load.tick(issue_en=1, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE,
                  bytes_n=15, bar_id=0)


def test_unaligned_addrs_assert():
    gmem = GMEM()
    smem = SMEM()
    load = Load(gmem, smem)
    with pytest.raises(AssertionError):
        load.tick(issue_en=1, gmem_ptr=1, smem_ptr=SMEM_TILE_BASE,
                  bytes_n=BEAT_BYTES, bar_id=0)
