"""
matmul_reference — random fp8 A, B and reference fp32 C = A @ B.

PURPOSE
    Generate problem instances for tests. Single source of truth for
    "what answer should the kernel produce?" Used by pymodel/tests/test_e2e.py
    and the Phase 5 RTL e2e test.

LAYOUT CONVENTION (SMEM-native, so LOAD copies bytes verbatim into SMEM)
    A is M*K bytes, COLUMN-major: A_bytes[k*M + m] encodes A_fp32[m, k]
    B is K*N bytes, ROW-major:    B_bytes[k*N + n] encodes B_fp32[k, n]
    C is fp32 (M, N) numpy array, row-major: C[m,n] = sum_k A[m,k] * B[k,n]

    These layouts match what pymodel.mma reads from SMEM with simple
    contiguous strides (A column k = A_base+k*M..; B row k = B_base+k*N..).

PUBLIC API (to be implemented in this file)
    generate(M: int, N: int, K: int, seed: int = 0) -> tuple[bytes, bytes, np.ndarray]
        Returns (A_bytes, B_bytes, C_fp32_expected).

        - A_bytes: bytes of length M*K, fp8 e4m3, column-major
        - B_bytes: bytes of length K*N, fp8 e4m3, row-major
        - C_fp32_expected: np.ndarray shape (M, N), dtype float32, row-major

        Random values drawn from a fp32 distribution narrow enough that
        e4m3 saturation is rare (suggested: N(0, 1) or U(-2, 2)).
        Computation is performed by decoding A_bytes and B_bytes back to fp32,
        then doing fp32 matmul, so C_fp32_expected exactly matches what an
        ideal pymodel.mma would produce given the same fp8 inputs.

INVARIANTS
    - Deterministic in `seed`: identical seed → identical A_bytes, B_bytes, C.
    - len(A_bytes) == M*K; len(B_bytes) == K*N; C_fp32_expected.shape == (M, N).

TEST CASES (pymodel/tests/test_matmul_reference.py)
    1. shapes_correct for (M=32, N=32, K=32).
    2. determinism: two calls with same seed return identical outputs.
    3. self_consistency: decode A_bytes (column-major) and B_bytes (row-major)
       back to fp32, compute A_fp32 @ B_fp32, compare to returned C_fp32_expected.
    4. asymmetric: (M=16, N=32, K=64) shapes correct, self-consistent.
"""

import numpy as np

from golden.fp8 import decode_e4m3, encode_e4m3


def generate(M: int, N: int, K: int, seed: int = 0):
    """See module docstring."""
    rng = np.random.RandomState(seed)
    # Narrow distribution so fp8 saturation is rare.
    A_fp32 = (rng.randn(M, K) * 0.5).astype(np.float32)
    B_fp32 = (rng.randn(K, N) * 0.5).astype(np.float32)

    A_fp8 = encode_e4m3(A_fp32)  # shape (M, K)
    B_fp8 = encode_e4m3(B_fp32)  # shape (K, N)

    # SMEM-native byte layouts.
    # A column-major: A_bytes[k*M + m] = A_fp8[m, k]. That's A_fp8.T (shape (K,M)) row-major.
    A_bytes = bytes(np.ascontiguousarray(A_fp8.T).reshape(-1))
    # B row-major: B_bytes[k*N + n] = B_fp8[k, n]. That's B_fp8 row-major.
    B_bytes = bytes(np.ascontiguousarray(B_fp8).reshape(-1))

    # Reference matmul: decode → fp32 matmul. Matches what an ideal MMA produces.
    A_eff = decode_e4m3(A_fp8)
    B_eff = decode_e4m3(B_fp8)
    C_fp32 = (A_eff @ B_eff).astype(np.float32)

    return A_bytes, B_bytes, C_fp32
