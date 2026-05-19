"""Tests for pymodel.mac_tmem_cell."""

import numpy as np
import pytest

from config import TMEM_SLOTS
from golden.fp8 import decode_e4m3, encode_e4m3
from pymodel.mac_tmem_cell import MacTmemCell


def _fp32_to_bits(x: float) -> int:
    return int(np.array([np.float32(x)], dtype=np.float32).view(np.uint32)[0])


def _bits_to_fp32(b: int) -> float:
    return float(np.array([b & 0xFFFFFFFF], dtype=np.uint32).view(np.float32)[0])


def _decode_one(byte: int) -> float:
    return float(decode_e4m3(np.array([byte & 0xFF], dtype=np.uint8))[0])


def _encode_one(x: float) -> int:
    return int(encode_e4m3(np.array([np.float32(x)], dtype=np.float32))[0])


def test_compute_no_accum():
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    a_byte = _encode_one(2.0)
    b_byte = _encode_one(3.0)
    cell.tick(compute_in=1, a_in=a_byte, b_in=b_byte, slot_in=1, accum_in=0)
    # Storage[1] should be a*b + 0.
    expected = np.float32(_decode_one(a_byte) * _decode_one(b_byte))
    assert cell.storage[1] == expected
    # Other slots untouched.
    assert cell.storage[0] == np.float32(0.0)


def test_compute_with_accum():
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    # Seed slot 2 with a known value via init.
    seed = np.float32(7.5)
    cell.tick(init_en=1, init_slot=2, init_data=_fp32_to_bits(seed))
    assert cell.storage[2] == seed

    a_byte = _encode_one(1.5)
    b_byte = _encode_one(2.0)
    cell.tick(compute_in=1, a_in=a_byte, b_in=b_byte, slot_in=2, accum_in=1)
    expected = np.float32(_decode_one(a_byte) * _decode_one(b_byte) + seed)
    assert cell.storage[2] == expected


def test_drain_latency_1():
    """drain_en at cycle T -> drain_data appears one tick later."""
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    seed = np.float32(42.0)
    cell.tick(init_en=1, init_slot=0, init_data=_fp32_to_bits(seed))

    # Cycle T: issue drain.
    cell.tick(drain_en=1, drain_slot=0)
    # drain_data should NOT yet reflect the read (it reflects no prior request).
    assert cell.drain_data == 0

    # Cycle T+1: drain commits.
    cell.tick()
    assert _bits_to_fp32(cell.drain_data) == float(seed)


def test_drain_write_forwarding():
    """Same-cycle commit-then-drain.

    drain captured at T-1 on slot S; at cycle T, scrub/init/compute commits
    to slot S. drain_data at cycle T must reflect the NEW value.
    """
    # Case A: init forwards.
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    cell.tick(init_en=1, init_slot=0, init_data=_fp32_to_bits(np.float32(1.0)))
    cell.tick(drain_en=1, drain_slot=0)  # capture at T
    new_bits = _fp32_to_bits(np.float32(99.0))
    cell.tick(init_en=1, init_slot=0, init_data=new_bits)  # commit at T+1
    assert cell.drain_data == new_bits

    # Case B: scrub forwards.
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    cell.tick(init_en=1, init_slot=1, init_data=_fp32_to_bits(np.float32(8.0)))
    cell.tick(drain_en=1, drain_slot=1)
    cell.tick(scrub_en=1)
    assert cell.drain_data == 0  # 0.0 == 0 bits

    # Case C: compute forwards.
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    cell.tick(drain_en=1, drain_slot=3)
    a_byte = _encode_one(4.0)
    b_byte = _encode_one(2.0)
    expected = np.float32(_decode_one(a_byte) * _decode_one(b_byte))
    cell.tick(compute_in=1, a_in=a_byte, b_in=b_byte, slot_in=3, accum_in=0)
    assert _bits_to_fp32(cell.drain_data) == float(expected)


def test_init_writes_slot():
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    bits = _fp32_to_bits(np.float32(-3.25))
    cell.tick(init_en=1, init_slot=2, init_data=bits)
    assert cell.storage[2] == np.float32(-3.25)


def test_scrub_clears_all_slots():
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    # Pre-init all slots.
    for s in range(TMEM_SLOTS):
        cell.tick(init_en=1, init_slot=s, init_data=_fp32_to_bits(np.float32(s + 1)))
    for s in range(TMEM_SLOTS):
        assert cell.storage[s] != np.float32(0.0)

    cell.tick(scrub_en=1)
    for s in range(TMEM_SLOTS):
        assert cell.storage[s] == np.float32(0.0)


def test_slot_isolation():
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    # Init slot 0 to one value, slot 1 to another.
    cell.tick(init_en=1, init_slot=0, init_data=_fp32_to_bits(np.float32(11.0)))
    cell.tick(init_en=1, init_slot=1, init_data=_fp32_to_bits(np.float32(22.0)))
    # Compute on slot 2 must not disturb 0 or 1.
    a_byte = _encode_one(2.0)
    b_byte = _encode_one(3.0)
    cell.tick(compute_in=1, a_in=a_byte, b_in=b_byte, slot_in=2, accum_in=0)
    assert cell.storage[0] == np.float32(11.0)
    assert cell.storage[1] == np.float32(22.0)


def test_mutex_compute_scrub_asserts():
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    with pytest.raises(AssertionError):
        cell.tick(compute_in=1, a_in=0x40, b_in=0x40, slot_in=0, scrub_en=1)


def test_mutex_init_compute_asserts():
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    with pytest.raises(AssertionError):
        cell.tick(compute_in=1, a_in=0x40, b_in=0x40, slot_in=0, init_en=1, init_slot=0)


def test_mutex_scrub_init_asserts():
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    with pytest.raises(AssertionError):
        cell.tick(scrub_en=1, init_en=1, init_slot=0)


def test_drain_with_no_pending_returns_zero():
    """drain_data is 0 when no prior drain_en was issued."""
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    cell.tick(init_en=1, init_slot=0, init_data=_fp32_to_bits(np.float32(5.0)))
    cell.tick()
    assert cell.drain_data == 0


def test_systolic_pipe_passthrough():
    """*_out latches the prior tick's *_in (1-cycle hop delay)."""
    cell = MacTmemCell(n_slots=TMEM_SLOTS)
    assert cell.a_out == 0 and cell.b_out == 0
    assert cell.compute_out == 0 and cell.slot_out == 0 and cell.accum_out == 0

    # Cycle 0: feed a packet (compute_in=0 so we don't write storage).
    cell.tick(compute_in=0, a_in=0xAB, b_in=0xCD, slot_in=2, accum_in=1)
    assert cell.a_out == 0xAB
    assert cell.b_out == 0xCD
    assert cell.slot_out == 2
    assert cell.accum_out == 1
    assert cell.compute_out == 0

    # Cycle 1: different packet — _out should now reflect cycle 1's _in.
    a1 = _encode_one(2.0)
    b1 = _encode_one(3.0)
    cell.tick(compute_in=1, a_in=a1, b_in=b1, slot_in=0, accum_in=0)
    assert cell.a_out == a1
    assert cell.b_out == b1
    assert cell.slot_out == 0
    assert cell.accum_out == 0
    assert cell.compute_out == 1
