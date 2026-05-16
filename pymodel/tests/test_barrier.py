"""Tests for pymodel.barrier."""

from pymodel.barrier import Barrier


def test_init_sets_state():
    b = Barrier()
    b.tick(init_en=1, init_bar_id=0, init_count=2)
    bar = b.bars[0]
    assert bar.pending == 2
    assert bar.expected == 2
    assert bar.tx_pending == 0
    assert bar.phase == 0


def test_arrive_only_flip():
    b = Barrier()
    b.tick(init_en=1, init_bar_id=0, init_count=2)
    b.tick(arrive_en_a=1, arrive_bar_id_a=0)
    assert b.bars[0].pending == 1
    assert b.bars[0].phase == 0
    b.tick(arrive_en_a=1, arrive_bar_id_a=0)
    assert b.bars[0].phase == 1
    assert b.bars[0].pending == 2  # reloaded from expected


def test_tx_only_flip():
    b = Barrier()
    b.tick(init_en=1, init_bar_id=0, init_count=0)
    assert b.bars[0].phase == 0  # INIT(0) does NOT trigger flip
    b.tick(add_tx_en=1, add_tx_bar_id=0, add_tx_bytes=1024)
    assert b.bars[0].tx_pending == 1024
    assert b.bars[0].phase == 0
    b.tick(sub_tx_en=1, sub_tx_bar_id=0, sub_tx_bytes=1024)
    assert b.bars[0].tx_pending == 0
    assert b.bars[0].phase == 1


def test_combined_load_pattern():
    """INIT(2) + 2 add_tx then 2 (sub_tx + arrive) → flip when both counters hit 0."""
    b = Barrier()
    b.tick(init_en=1, init_bar_id=0, init_count=2)
    b.tick(add_tx_en=1, add_tx_bar_id=0, add_tx_bytes=1024)
    b.tick(add_tx_en=1, add_tx_bar_id=0, add_tx_bytes=1024)
    assert b.bars[0].tx_pending == 2048
    assert b.bars[0].pending == 2
    assert b.bars[0].phase == 0
    b.tick(
        sub_tx_en=1, sub_tx_bar_id=0, sub_tx_bytes=1024,
        arrive_en_a=1, arrive_bar_id_a=0,
    )
    assert b.bars[0].tx_pending == 1024
    assert b.bars[0].pending == 1
    assert b.bars[0].phase == 0
    b.tick(
        sub_tx_en=1, sub_tx_bar_id=0, sub_tx_bytes=1024,
        arrive_en_a=1, arrive_bar_id_a=0,
    )
    assert b.bars[0].tx_pending == 0
    assert b.bars[0].phase == 1


def test_dual_arrive_same_cycle():
    b = Barrier()
    b.tick(init_en=1, init_bar_id=0, init_count=2)
    b.tick(
        arrive_en_a=1, arrive_bar_id_a=0,
        arrive_en_b=1, arrive_bar_id_b=0,
    )
    # Both arrives decrement pending → 0; FLIP fires; pending reloads to expected.
    assert b.bars[0].phase == 1
    assert b.bars[0].pending == 2


def test_wait_query_combinational():
    b = Barrier()
    b.tick(init_en=1, init_bar_id=0, init_count=1)
    assert b.wait_query(0, expected_phase=0) == 0
    b.tick(arrive_en_a=1, arrive_bar_id_a=0)
    assert b.bars[0].phase == 1
    assert b.wait_query(0, expected_phase=0) == 1


def test_priority_order_init_beats_arrive():
    """INIT + arrive in same cycle on same bar → fresh init state, arrive dropped."""
    b = Barrier()
    b.tick(init_en=1, init_bar_id=0, init_count=5)
    b.tick(
        init_en=1, init_bar_id=0, init_count=3,
        arrive_en_a=1, arrive_bar_id_a=0,
    )
    # Should be fresh init state; arrive ignored.
    assert b.bars[0].pending == 3
    assert b.bars[0].expected == 3
    assert b.bars[0].phase == 0
