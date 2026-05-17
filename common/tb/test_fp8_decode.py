"""Cocotb unit tests for common/fp8_decode.sv.

For every fp8 e4m3 byte (256 codes), drive the input and compare the
32-bit fp32 output against golden.fp8.decode_e4m3. NaN codes (0x7F and
0xFF) only require that the result IS a NaN (sign bit and payload may
differ).
"""

import cocotb
import numpy as np
from cocotb.triggers import Timer

from golden.fp8 import decode_e4m3


@cocotb.test()
async def test_decode_all_codes(dut):
    """Drive all 256 e4m3 codes, compare bit-exact against golden (NaN: any NaN)."""

    for code in range(256):
        dut.fp8.value = code
        # combinational — let signals settle.
        await Timer(1, units="ns")

        sv_bits = int(dut.fp32.value) & 0xFFFFFFFF
        py_fp32 = decode_e4m3(np.array([code], dtype=np.uint8))[0]
        py_bits = int(py_fp32.view(np.uint32))

        if np.isnan(py_fp32):
            # SV result must also be a NaN (exp=0xFF, mant != 0).
            sv_exp = (sv_bits >> 23) & 0xFF
            sv_mant = sv_bits & 0x7FFFFF
            assert sv_exp == 0xFF and sv_mant != 0, (
                f"code=0x{code:02x}: expected NaN, got SV bits=0x{sv_bits:08x}"
            )
        else:
            assert sv_bits == py_bits, (
                f"code=0x{code:02x}: SV=0x{sv_bits:08x} py=0x{py_bits:08x} "
                f"(SV={np.uint32(sv_bits).view(np.float32)}, py={py_fp32})"
            )
