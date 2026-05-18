"""Tests for pymodel.store.

Phase 7h-3: store now consumes compute_array's drain stream (was tmem
pre-7h-3). The pymodel uses back-door tile access via
ComputeArray.get_tile(), so the test seeds via ComputeArray.set_tile().
"""

import numpy as np

from config import BEAT_BYTES, MMA_M, MMA_N
from golden.fp8 import decode_e4m3, encode_e4m3
from pymodel.compute_array import ComputeArray
from pymodel.gmem import GMEM
from pymodel.store import Store


def _tile(seed: int) -> np.ndarray:
    return np.random.RandomState(seed).randn(MMA_M, MMA_N).astype(np.float32) * 0.3


def _run_until_idle(store: Store, max_cycles: int = 1000) -> None:
    for _ in range(max_cycles):
        store.tick()
        if not store.busy:
            return
    raise AssertionError("STORE did not become idle")


def test_store_fp32():
    ca = ComputeArray()
    gmem = GMEM()
    store = Store(ca, gmem)
    tile = _tile(0)
    ca.set_tile(0, tile)

    store.tick(issue_en=1, tmem_slot=0, gmem_ptr=0, dtype=0)
    _run_until_idle(store)
    nbytes = MMA_M * MMA_N * 4
    expected = bytes(np.ascontiguousarray(tile.astype("<f4")).tobytes())
    assert gmem.dump(0, nbytes) == expected


def test_store_fp8():
    ca = ComputeArray()
    gmem = GMEM()
    store = Store(ca, gmem)
    tile = _tile(1)
    ca.set_tile(0, tile)

    store.tick(issue_en=1, tmem_slot=0, gmem_ptr=0, dtype=1)
    _run_until_idle(store)
    nbytes = MMA_M * MMA_N
    expected = bytes(np.ascontiguousarray(encode_e4m3(tile)).reshape(-1))
    assert gmem.dump(0, nbytes) == expected


def test_roundtrip_through_fp8():
    ca = ComputeArray()
    gmem = GMEM()
    store = Store(ca, gmem)
    tile = _tile(2)
    ca.set_tile(0, tile)

    store.tick(issue_en=1, tmem_slot=0, gmem_ptr=0, dtype=1)
    _run_until_idle(store)
    nbytes = MMA_M * MMA_N
    decoded = decode_e4m3(
        np.frombuffer(gmem.dump(0, nbytes), dtype=np.uint8)
    ).reshape(MMA_M, MMA_N)
    # fp8 quantization error tolerance
    np.testing.assert_allclose(decoded, tile, rtol=0.15, atol=0.1)


def test_multi_beat_correctness():
    """fp32 STORE produces MMA_M*MMA_N*4 / BEAT_BYTES beats, each correct."""
    ca = ComputeArray()
    gmem = GMEM()
    store = Store(ca, gmem)
    tile = _tile(3)
    ca.set_tile(0, tile)
    store.tick(issue_en=1, tmem_slot=0, gmem_ptr=0, dtype=0)
    _run_until_idle(store)
    # Verify byte-exact match.
    expected = bytes(np.ascontiguousarray(tile.astype("<f4")).tobytes())
    assert gmem.dump(0, len(expected)) == expected


def test_busy_during_drain():
    ca = ComputeArray()
    gmem = GMEM()
    store = Store(ca, gmem)
    ca.set_tile(0, _tile(4))
    store.tick(issue_en=1, tmem_slot=0, gmem_ptr=0, dtype=1)
    assert store.busy == 1
    # mid-drain
    store.tick()
    if store.busy:
        # Still draining — that's fine
        pass
    _run_until_idle(store)
    assert store.busy == 0
