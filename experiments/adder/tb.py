"""
cocotb testbench for adder.sv.

Two checks:
  1. test_basic — directed test, fixed inputs, fixed expected outputs.
  2. test_random_vs_pymodel — drive random inputs to both the SV module and
     the Python pymodel.Adder in lockstep; assert outputs match every cycle.

This is the prototype for how every GPU RTL module will be tested in Phase 4.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from pymodel import Adder


async def _reset_and_clock(dut):
    """Start the clock and drive inputs to safe defaults."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.en.value = 0
    dut.a.value = 0
    dut.b.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)


@cocotb.test()
async def test_basic(dut):
    await _reset_and_clock(dut)

    dut.en.value = 1
    dut.a.value = 5
    dut.b.value = 7
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.sum.value) == 12, f"expected sum=12, got {int(dut.sum.value)}"
    assert int(dut.valid.value) == 1


@cocotb.test()
async def test_random_vs_pymodel(dut):
    await _reset_and_clock(dut)

    py = Adder()
    rng = random.Random(0xC0FFEE)

    for _ in range(500):
        en = rng.randint(0, 1)
        a = rng.randint(0, 255)
        b = rng.randint(0, 255)

        # Drive inputs for the upcoming clock edge.
        dut.en.value = en
        dut.a.value = a
        dut.b.value = b

        await RisingEdge(dut.clk)
        # The SV registers latch on this edge. Step the pymodel to match.
        py.tick(en=en, a=a, b=b)

        # Wait for NBAs to settle, then compare.
        await ReadOnly()
        sv_sum = int(dut.sum.value)
        sv_valid = int(dut.valid.value)
        assert sv_sum == py.sum, (
            f"sum mismatch: en={en} a={a} b={b} sv={sv_sum} py={py.sum}"
        )
        assert sv_valid == py.valid, (
            f"valid mismatch: en={en} sv={sv_valid} py={py.valid}"
        )
        # Leave ReadOnly so next iteration can drive inputs.
        await NextTimeStep()
