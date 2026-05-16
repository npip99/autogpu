"""Tests for golden.fp8."""

import numpy as np
import pytest

from golden.fp8 import decode_e4m3, encode_e4m3


def test_roundtrip_representable_values():
    """For every representable e4m3 code (ex. NaN codes), decode→encode is identity."""
    codes = np.arange(256, dtype=np.uint8)
    decoded = decode_e4m3(codes)
    valid = ~np.isnan(decoded)
    re_encoded = encode_e4m3(decoded[valid])
    np.testing.assert_array_equal(re_encoded, codes[valid])


def test_saturation_positive():
    """encode(1e10) returns max positive normal (0x7E)."""
    code = encode_e4m3(np.array([1e10], dtype=np.float32))[0]
    assert code == 0x7E, f"got {hex(int(code))}"


def test_saturation_negative():
    """encode(-1e10) returns max negative normal (0xFE)."""
    code = encode_e4m3(np.array([-1e10], dtype=np.float32))[0]
    assert code == 0xFE, f"got {hex(int(code))}"


def test_nan_encode():
    """encode(np.nan) → NaN code (0x7F or 0xFF)."""
    code = encode_e4m3(np.array([np.nan], dtype=np.float32))[0]
    assert int(code) in (0x7F, 0xFF), f"got {hex(int(code))}"


def test_nan_decode():
    """decode of NaN codes → np.nan."""
    assert np.isnan(decode_e4m3(np.array([0x7F], dtype=np.uint8))[0])
    assert np.isnan(decode_e4m3(np.array([0xFF], dtype=np.uint8))[0])


def test_zero_encodings():
    """encode(0.0)→0x00; encode(-0.0)→0x80."""
    assert encode_e4m3(np.array([0.0], dtype=np.float32))[0] == 0x00
    assert encode_e4m3(np.array([-0.0], dtype=np.float32))[0] == 0x80


def test_smallest_subnormal_roundtrips():
    """Smallest positive subnormal (2^-9) round-trips via code 0x01."""
    smallest = np.float32(2.0 ** -9)
    enc = encode_e4m3(np.array([smallest], dtype=np.float32))[0]
    assert enc == 0x01, f"got {hex(int(enc))}"
    dec = decode_e4m3(np.array([enc], dtype=np.uint8))[0]
    assert dec == smallest


def test_shape_preservation():
    """encode/decode preserve 2D and 3D ndarray shapes."""
    x_2d = np.random.RandomState(0).randn(5, 7).astype(np.float32) * 0.5
    enc_2d = encode_e4m3(x_2d)
    assert enc_2d.shape == (5, 7)
    assert decode_e4m3(enc_2d).shape == (5, 7)
    x_3d = np.zeros((2, 3, 4), dtype=np.float32)
    assert encode_e4m3(x_3d).shape == (2, 3, 4)
