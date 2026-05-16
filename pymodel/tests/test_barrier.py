"""Tests for pymodel.barrier."""

# from pymodel.barrier import Barrier


def test_init_sets_state():
    """INIT(bar=0, count=2) → pending=2, expected=2, tx_pending=0, phase=0."""
    raise NotImplementedError


def test_arrive_only_flip():
    """INIT(count=2); 2 arrives → phase flips, pending reloads to 2."""
    raise NotImplementedError


def test_tx_only_flip():
    """INIT(count=0); add_tx 1024 → flip blocked; sub_tx 1024 → flips."""
    raise NotImplementedError


def test_combined_load_pattern():
    """INIT(count=2); add_tx 1024 twice; arrive twice + sub_tx twice → phase flips when both counters reach 0."""
    raise NotImplementedError


def test_dual_arrive_same_cycle():
    """Two arrives on same bar in one tick decrement pending by 2."""
    raise NotImplementedError


def test_wait_query_combinational():
    """WAIT(bar=0, expected=0) returns 0 while phase=0; immediately returns 1 the cycle after flip."""
    raise NotImplementedError


def test_priority_order_init_beats_arrive():
    """INIT + arrive in same cycle → fresh init state (count fully reloaded), arrive ignored on this cycle."""
    raise NotImplementedError
