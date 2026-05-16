"""Tests for golden.matmul_reference."""

import numpy as np

from golden.fp8 import decode_e4m3
from golden.matmul_reference import generate


def test_shapes_correct():
    A_bytes, B_bytes, C = generate(M=32, N=32, K=32, seed=0)
    assert len(A_bytes) == 32 * 32
    assert len(B_bytes) == 32 * 32
    assert C.shape == (32, 32)
    assert C.dtype == np.float32


def test_determinism():
    a1, b1, c1 = generate(32, 32, 32, seed=42)
    a2, b2, c2 = generate(32, 32, 32, seed=42)
    assert a1 == a2
    assert b1 == b2
    np.testing.assert_array_equal(c1, c2)


def test_self_consistency():
    """Decode the returned bytes, recompute matmul, must equal the returned C."""
    M, N, K = 32, 32, 32
    A_bytes, B_bytes, C = generate(M, N, K, seed=0)
    # A is column-major: reshape as (K, M) row-major then transpose to (M, K)
    A_fp8 = np.frombuffer(A_bytes, dtype=np.uint8).reshape(K, M).T
    # B is row-major (K, N)
    B_fp8 = np.frombuffer(B_bytes, dtype=np.uint8).reshape(K, N)
    C_recomputed = (decode_e4m3(A_fp8) @ decode_e4m3(B_fp8)).astype(np.float32)
    np.testing.assert_array_equal(C_recomputed, C)


def test_asymmetric_shape():
    A_bytes, B_bytes, C = generate(M=16, N=32, K=64, seed=1)
    assert len(A_bytes) == 16 * 64
    assert len(B_bytes) == 64 * 32
    assert C.shape == (16, 32)
