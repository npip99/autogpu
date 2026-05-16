"""Tests for golden.fp8. Validates encode/decode against spec."""

# from golden.fp8 import encode_e4m3, decode_e4m3


def test_roundtrip_representable_values():
    """For every representable e4m3 code (ex. NaN duplicates), decode→encode is identity."""
    raise NotImplementedError


def test_saturation_positive():
    """encode(1e10) returns the max_normal positive code."""
    raise NotImplementedError


def test_saturation_negative():
    """encode(-1e10) returns the max_normal negative code."""
    raise NotImplementedError


def test_nan_encode():
    """encode(np.nan) returns the NaN code (0x7F or 0xFF)."""
    raise NotImplementedError


def test_nan_decode():
    """decode of the NaN code returns np.nan."""
    raise NotImplementedError


def test_zero_encodings():
    """encode(0.0)→0x00; encode(-0.0)→0x80."""
    raise NotImplementedError


def test_smallest_subnormal_roundtrips():
    """Smallest positive subnormal value is preserved by decode(encode(x))."""
    raise NotImplementedError


def test_shape_preservation():
    """encode/decode preserve 2D and 3D ndarray shapes."""
    raise NotImplementedError
