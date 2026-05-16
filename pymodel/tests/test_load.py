"""Tests for pymodel.load."""

# from pymodel.load import Load


def test_single_load():
    """Preload gmem with pattern; issue LOAD; after done, smem contains the pattern."""
    raise NotImplementedError


def test_barrier_accounting():
    """add_tx fires on accept; sub_tx + arrive fire on completion;
    tx_pending returns to 0 and pending decremented by 1."""
    raise NotImplementedError


def test_two_loads_queued():
    """Push two LOADs back-to-back; cmdproc not stalled; both complete with correct
    smem contents and barrier state (final tx_pending=0, pending decremented by 2)."""
    raise NotImplementedError


def test_multi_beat_correctness():
    """LOAD of N*BEAT_BYTES bytes copies exactly N beats, no gaps or overlaps."""
    raise NotImplementedError


def test_unaligned_bytes_asserts():
    """LOAD with bytes not multiple of BEAT_BYTES asserts."""
    raise NotImplementedError


def test_unaligned_addrs_assert():
    """gmem_ptr or smem_ptr not BEAT_BYTES-aligned asserts."""
    raise NotImplementedError
