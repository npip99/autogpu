"""Tests for pymodel.mma."""

# from pymodel.mma import MMA


def test_accum0_known_inputs():
    """Backdoor-load known A, B into SMEM (column/row-major per spec); run MMA accum=0;
    after MMA_K+2 cycles, TMEM slot equals fp8.decode(A) @ fp8.decode(B)^T in fp32."""
    raise NotImplementedError


def test_accum1_adds_into_existing():
    """Pre-set TMEM slot to D0; run MMA accum=1; slot equals D0 + A@B^T."""
    raise NotImplementedError


def test_barrier_arrival_on_done():
    """After MMA completes, barrier.pending for bar_id decrements by 1."""
    raise NotImplementedError


def test_random_via_golden():
    """Use golden.matmul_reference.generate; load A,B into SMEM; run MMA; compare to C_expected."""
    raise NotImplementedError


def test_busy_blocks_start():
    """start=1 while busy=1 asserts (or is rejected)."""
    raise NotImplementedError


def test_latency_exact():
    """done pulses exactly MMA_K + 2 cycles after start."""
    raise NotImplementedError
