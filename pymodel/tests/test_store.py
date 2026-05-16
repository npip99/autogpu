"""Tests for pymodel.store."""

# from pymodel.store import Store


def test_store_fp32():
    """Backdoor-set tmem slot; STORE dtype=0; gmem contains the flattened fp32 tile."""
    raise NotImplementedError


def test_store_fp8():
    """STORE dtype=1; gmem contains fp8.encode_e4m3 of the tile."""
    raise NotImplementedError


def test_roundtrip_through_fp8():
    """Set slot to known tile; STORE fp8; decode_e4m3(gmem) approximates original tile
    within fp8 quantization tolerance."""
    raise NotImplementedError


def test_multi_beat_correctness():
    """Tile large enough to require multiple BEAT_BYTES writes; all beats correct."""
    raise NotImplementedError


def test_busy_during_drain():
    """busy=1 from cycle after issue until done pulses."""
    raise NotImplementedError
