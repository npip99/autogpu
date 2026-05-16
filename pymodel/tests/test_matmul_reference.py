"""Tests for golden.matmul_reference. Validates shapes, determinism, self-consistency."""

# from golden.matmul_reference import generate


def test_shapes_correct():
    """generate(32,32,32) returns A=1024B, B=1024B, C shape=(32,32) fp32."""
    raise NotImplementedError


def test_determinism():
    """Two calls with same seed return identical bytes and identical C."""
    raise NotImplementedError


def test_self_consistency():
    """Decode A_bytes (column-major) and B_bytes (row-major) back to fp32,
    compute A_fp32 @ B_fp32, compare to returned C_fp32_expected.
    Must match exactly (since reference computes via same decode→fp32 path)."""
    raise NotImplementedError


def test_asymmetric_shape():
    """generate(16, 32, 64) shapes correct, self-consistent."""
    raise NotImplementedError
