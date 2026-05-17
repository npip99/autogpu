"""Cocotb unit tests for common/fp32_fma.sv (CVFPU FMA wrapper).

For (a, b, c) triples the SV result should match `numpy.fma(a, b, c)` if
that existed; numpy doesn't ship FMA, so we use the IEEE-754
round-to-nearest-even reference computed in fp64 with manual fp32
correct-rounding emulation.
"""

import struct

import cocotb
import numpy as np
from cocotb.triggers import Timer


def fp32_bits(v):
    return int(np.float32(v).view(np.uint32))


def bits_to_fp32(bits):
    return np.uint32(bits & 0xFFFFFFFF).view(np.float32)


def reference_fma(a, b, c):
    """Compute a*b + c with one rounding, using fp64 intermediates.

    fp64 has 53-bit precision; multiplying two 24-bit significands gives
    a 48-bit product, exactly representable in fp64. The add can lose
    bits, but only if it produces a result whose magnitude is < 2^-1074
    (extreme subnormals) or > 2^971, neither of which arises in our
    random tests. So this is a correct fp32-rounded FMA reference for
    the tested ranges.
    """
    ai = np.float64(a)
    bi = np.float64(b)
    ci = np.float64(c)
    exact = ai * bi + ci
    return np.float32(exact)


@cocotb.test()
async def test_fma_zero_addend(dut):
    """a*b + 0 = a*b (rounded)."""
    rng = np.random.default_rng(0xBEEF)
    for _ in range(200):
        a = np.float32(rng.uniform(-10, 10))
        b = np.float32(rng.uniform(-10, 10))
        c = np.float32(0.0)
        dut.a.value = fp32_bits(a)
        dut.b.value = fp32_bits(b)
        dut.c.value = fp32_bits(c)
        await Timer(1, units="ns")
        sv_bits = int(dut.result.value) & 0xFFFFFFFF
        # a*b is exactly representable (or correctly rounded) in fp32.
        expected = np.float32(np.float64(a) * np.float64(b))
        exp_bits = fp32_bits(expected)
        assert sv_bits == exp_bits, (
            f"a*b: a={a} b={b} sv=0x{sv_bits:08x} exp=0x{exp_bits:08x}"
        )


@cocotb.test()
async def test_fma_zero_multiplier(dut):
    """0*b + c = c."""
    for c_val in [1.0, -1.0, 1.5, -3.14, 1e6, -1e-6, 0.0]:
        c = np.float32(c_val)
        dut.a.value = fp32_bits(0.0)
        dut.b.value = fp32_bits(5.0)
        dut.c.value = fp32_bits(c)
        await Timer(1, units="ns")
        sv_bits = int(dut.result.value) & 0xFFFFFFFF
        # 0 * 5 = 0, plus c = c.
        # Note: with FMADD round-to-nearest-even, sign of 0*0 may follow
        # standard IEEE-754: +0*5 + c = c.
        assert sv_bits == fp32_bits(c) or sv_bits == fp32_bits(np.float32(0.0) + c), (
            f"0*5+{c}: sv=0x{sv_bits:08x}"
        )


@cocotb.test()
async def test_fma_random(dut):
    """Random (a, b, c) triples vs fp64 reference."""
    rng = np.random.default_rng(0x1234)
    n = 500
    a_vals = rng.standard_normal(n).astype(np.float32) * 10.0
    b_vals = rng.standard_normal(n).astype(np.float32) * 10.0
    c_vals = rng.standard_normal(n).astype(np.float32) * 50.0

    for a, b, c in zip(a_vals, b_vals, c_vals):
        dut.a.value = fp32_bits(a)
        dut.b.value = fp32_bits(b)
        dut.c.value = fp32_bits(c)
        await Timer(1, units="ns")
        sv_bits = int(dut.result.value) & 0xFFFFFFFF
        expected = reference_fma(a, b, c)
        exp_bits = fp32_bits(expected)
        if sv_bits != exp_bits:
            sv_v = float(bits_to_fp32(sv_bits))
            exp_v = float(expected)
            raise AssertionError(
                f"FMA mismatch: a={float(a)} b={float(b)} c={float(c)}\n"
                f"  sv=0x{sv_bits:08x} ({sv_v})\n"
                f"  ref=0x{exp_bits:08x} ({exp_v})"
            )
