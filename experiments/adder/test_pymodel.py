"""pytest for pymodel.Adder. Verifies the Python class matches the spec."""

from pymodel import Adder


def test_basic_add():
    a = Adder()
    a.tick(en=1, a=5, b=7)
    assert a.sum == 12
    assert a.valid == 1


def test_en_low_zeroes():
    a = Adder()
    a.tick(en=0, a=5, b=7)
    assert a.sum == 0
    assert a.valid == 0


def test_consecutive_updates():
    a = Adder()
    a.tick(en=1, a=10, b=20)
    assert a.sum == 30
    assert a.valid == 1
    a.tick(en=1, a=100, b=50)
    assert a.sum == 150
    assert a.valid == 1


def test_valid_goes_low():
    a = Adder()
    a.tick(en=1, a=1, b=2)
    assert a.valid == 1
    a.tick(en=0, a=0, b=0)
    assert a.valid == 0
    assert a.sum == 0


def test_max_inputs():
    a = Adder()
    a.tick(en=1, a=255, b=255)
    assert a.sum == 510
    assert a.valid == 1
