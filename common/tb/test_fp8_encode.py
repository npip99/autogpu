"""Cocotb unit tests for common/fp8_encode.sv.

Drive random fp32 values plus edge cases, compare bit-exact against
golden.fp8.encode_e4m3.

Tie semantics: golden uses np.argmin which picks the lower index on
ties (= smaller magnitude = round-toward-zero). fp8_encode.sv
intentionally matches this; see header in fp8_encode.sv.
"""

import struct

import cocotb
import numpy as np
from cocotb.triggers import Timer

from golden.fp8 import encode_e4m3


def fp32_bits(v):
    return int(np.float32(v).view(np.uint32))


# Edge cases that must encode correctly.
EDGE_CASES = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    2.0,
    -2.0,
    448.0,             # max representable e4m3 normal
    -448.0,
    447.99,            # just below max -> still 0x7E via argmin
    449.0,             # just above max -> saturate
    1e10,              # overflow -> saturate
    -1e10,
    2.0 ** -9,         # smallest e4m3 subnormal (code 0x01)
    -(2.0 ** -9),
    2.0 ** -10,        # half-LSB; golden -> 0 (ties toward zero)
    2.0 ** -11,        # below half -> 0
    2.0 ** -6,         # smallest e4m3 normal (code 0x08)
    1.5,
    1.75,
    3.0,
    7.0,
    14.0,              # exactly an e4m3 normal
    -14.0,
    256.0,             # e=15, m=0 (just inside max-exp)
    480.0,             # e=15 with m=7 in raw -> NaN code -> saturate
    256.0 * 1.5,       # = 384 -> e=15, m=4
    float("nan"),
    -float("nan"),
    float("inf"),      # saturate
    -float("inf"),
    # fp32 subnormal -> rounds to 0
    struct.unpack("<f", struct.pack("<I", 0x00000001))[0],
]


@cocotb.test()
async def test_encode_edge_cases(dut):
    """Edge-case fp32 -> e4m3 bit-exact vs golden."""
    for v in EDGE_CASES:
        bits = fp32_bits(v)
        dut.fp32.value = bits
        await Timer(1, units="ns")

        sv_byte = int(dut.fp8.value) & 0xFF
        py_byte = int(encode_e4m3(np.array([np.float32(v)], dtype=np.float32))[0])
        assert sv_byte == py_byte, (
            f"v={v} fp32_bits=0x{bits:08x}: SV=0x{sv_byte:02x} py=0x{py_byte:02x}"
        )


@cocotb.test()
async def test_encode_random(dut):
    """Random fp32 values (in / near / above the e4m3 range) vs golden."""
    rng = np.random.default_rng(0xCAFE)

    samples = []
    # In-range uniform.
    samples.append(rng.uniform(-448.0, 448.0, size=500).astype(np.float32))
    # Tiny values (subnormal-range).
    samples.append((rng.uniform(-2.0 ** -8, 2.0 ** -8, size=200) ).astype(np.float32))
    # Wide range exponent.
    samples.append((rng.standard_normal(200).astype(np.float32) * 100.0))
    # Overflow.
    samples.append((rng.uniform(-1e6, 1e6, size=100)).astype(np.float32))
    all_vals = np.concatenate(samples)

    for v in all_vals:
        bits = int(np.float32(v).view(np.uint32))
        dut.fp32.value = bits
        await Timer(1, units="ns")
        sv_byte = int(dut.fp8.value) & 0xFF
        py_byte = int(encode_e4m3(np.array([v], dtype=np.float32))[0])
        if sv_byte != py_byte:
            raise AssertionError(
                f"v={float(v)} fp32_bits=0x{bits:08x}: "
                f"SV=0x{sv_byte:02x} py=0x{py_byte:02x}"
            )


@cocotb.test()
async def test_encode_all_representable_roundtrip(dut):
    """Decoding every valid e4m3 code (except NaN) and re-encoding round-trips."""
    codes = np.arange(256, dtype=np.uint8)
    from golden.fp8 import decode_e4m3
    decoded = decode_e4m3(codes)
    valid = ~np.isnan(decoded)
    for code in codes[valid]:
        v = decoded[code]
        bits = int(np.float32(v).view(np.uint32))
        dut.fp32.value = bits
        await Timer(1, units="ns")
        sv_byte = int(dut.fp8.value) & 0xFF
        # The round-trip property holds for representable values.
        assert sv_byte == int(code), (
            f"roundtrip fail: code=0x{int(code):02x} -> v={float(v)} -> "
            f"SV=0x{sv_byte:02x}"
        )
