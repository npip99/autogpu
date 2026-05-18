"""
cocotb testbench for compute_array.sv.

Drives the SV compute_array and pymodel.compute_array.ComputeArray in
lockstep. The pymodel is the canonical reference (DEVELOPMENT.md "build
philosophy"). We simulate a faux SMEM in the testbench: deliver one A
column + one B row per requested cycle, with 1-cycle read latency.
"""

import random

import cocotb
import numpy as np
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, reset
from config import MMA_K, MMA_M, MMA_N, TMEM_SLOTS
from golden.fp8 import decode_e4m3, encode_e4m3
from pymodel.compute_array import ComputeArray


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _bytes_to_int(b: bytes) -> int:
    return int.from_bytes(b, "little")


def _fp32_bits(x: float) -> int:
    return int(np.array([np.float32(x)], dtype=np.float32).view(np.uint32)[0])


def _bits_to_fp32(b: int) -> float:
    return float(np.array([b & 0xFFFFFFFF], dtype=np.uint32).view(np.float32)[0])


async def _drive_defaults(dut) -> None:
    dut.mma_issue.value = 0
    dut.mma_slot.value = 0
    dut.mma_accum.value = 0
    dut.mma_bar_id.value = 0
    dut.issue_a_off.value = 0
    dut.issue_b_off.value = 0
    dut.issue_a_stride.value = 0
    dut.issue_b_stride.value = 0
    dut.rd_a_data.value = 0
    dut.rd_a_valid.value = 0
    dut.rd_a_stall_in.value = 0
    dut.rd_b_data.value = 0
    dut.rd_b_valid.value = 0
    dut.rd_b_stall_in.value = 0
    dut.drain_issue.value = 0
    dut.drain_slot.value = 0
    dut.scrub_en.value = 0


def _read_storage_slot(dut, slot: int) -> np.ndarray:
    """Backdoor-read storage[slot] across all cells. Returns (MMA_M, MMA_N) fp32."""
    out = np.zeros((MMA_M, MMA_N), dtype=np.float32)
    for i in range(MMA_M):
        for j in range(MMA_N):
            bits = int(dut.gen_row[i].gen_col[j].u_cell.storage[slot].value) & 0xFFFFFFFF
            out[i, j] = _bits_to_fp32(bits)
    return out


def _py_storage_slot(py: ComputeArray, slot: int) -> np.ndarray:
    return py.get_tile(slot)


def _set_storage_slot(dut, slot: int, tile: np.ndarray) -> None:
    """Backdoor-write a tile into storage[slot] on all cells."""
    assert tile.shape == (MMA_M, MMA_N)
    for i in range(MMA_M):
        for j in range(MMA_N):
            dut.gen_row[i].gen_col[j].u_cell.storage[slot].value = _fp32_bits(
                tile[i, j]
            )


# ----------------------------------------------------------------------
# Faux SMEM driver
# ----------------------------------------------------------------------
class FauxSmem:
    """Models the registered SMEM read interface for the testbench.

    A is column-major, B is row-major. One column of A or row of B per
    request, served with 1-cycle latency. No stalls (matches the
    well-formed program). The driver tracks ONE outstanding request per
    port (rd_a_en / rd_b_en) and emits rd_*_valid + rd_*_data one cycle
    after the en goes high.
    """

    def __init__(self, A_fp8: np.ndarray, B_fp8: np.ndarray, a_off: int, b_off: int):
        self.A_fp8 = A_fp8  # (MMA_M, MMA_K) bytes
        self.B_fp8 = B_fp8  # (MMA_K, MMA_N) bytes
        self.a_off = a_off
        self.b_off = b_off
        self.a_stride = MMA_M
        self.b_stride = MMA_N

        # Pending response (delivered next cycle).
        self._pending_a: bytes | None = None
        self._pending_b: bytes | None = None

    def serve(self, rd_a_en: int, rd_a_addr: int, rd_b_en: int, rd_b_addr: int):
        """Returns (a_data, a_valid, b_data, b_valid) for THIS cycle.

        Call this BEFORE driving inputs to the DUT for the next cycle.
        The valid+data are based on whatever was requested last cycle.
        """
        a_data = self._pending_a if self._pending_a is not None else bytes(MMA_M)
        a_valid = 1 if self._pending_a is not None else 0
        b_data = self._pending_b if self._pending_b is not None else bytes(MMA_N)
        b_valid = 1 if self._pending_b is not None else 0

        # Now capture new requests for next cycle.
        if rd_a_en:
            k = (rd_a_addr - self.a_off) // self.a_stride
            assert 0 <= k < MMA_K, f"rd_a_addr OOR: k={k}"
            self._pending_a = bytes(self.A_fp8[:, k])
        else:
            self._pending_a = None

        if rd_b_en:
            k = (rd_b_addr - self.b_off) // self.b_stride
            assert 0 <= k < MMA_K, f"rd_b_addr OOR: k={k}"
            self._pending_b = bytes(self.B_fp8[k, :])
        else:
            self._pending_b = None

        return a_data, a_valid, b_data, b_valid


async def _run_matmul_lockstep(
    dut,
    py: ComputeArray,
    A_fp8: np.ndarray,
    B_fp8: np.ndarray,
    slot: int,
    accum: int,
    bar_id: int = 0,
    max_cycles: int = MMA_K * 4 + 20,
) -> int:
    """Issue one matmul; drive SMEM responses; compare SV vs pymodel each cycle.

    Returns the number of cycles taken (until mma_done observed).
    """
    a_off = 0
    b_off = MMA_M * MMA_K
    smem = FauxSmem(A_fp8, B_fp8, a_off, b_off)

    # Issue cycle.
    dut.mma_issue.value = 1
    dut.mma_slot.value = slot
    dut.mma_accum.value = accum
    dut.mma_bar_id.value = bar_id
    dut.issue_a_off.value = a_off
    dut.issue_b_off.value = b_off
    dut.issue_a_stride.value = MMA_M
    dut.issue_b_stride.value = MMA_N
    # No SMEM response yet (no en was high last cycle).
    dut.rd_a_data.value = 0
    dut.rd_a_valid.value = 0
    dut.rd_b_data.value = 0
    dut.rd_b_valid.value = 0

    await RisingEdge(dut.clk)
    py.tick(
        mma_issue=1,
        mma_slot=slot,
        mma_accum=accum,
        mma_bar_id=bar_id,
        issue_a_off=a_off,
        issue_b_off=b_off,
        issue_a_stride=MMA_M,
        issue_b_stride=MMA_N,
    )

    await ReadOnly()
    # Cycle 1 post-issue: rd_a_en should be high in both.
    _assert_ports_match(dut, py, ctx="post-issue cycle 0")
    await NextTimeStep()

    # Clear issue for the rest.
    dut.mma_issue.value = 0

    # Now compute loop. The SV has just commit-set rd_a_en=1/rd_b_en=1 at this
    # RisingEdge. We need to read sv's rd_a_en/addr to feed the SMEM driver.
    cycle = 1
    while cycle < max_cycles:
        # Snapshot current registered rd_*_en for SMEM serving.
        rd_a_en = int(dut.rd_a_en.value)
        rd_a_addr = int(dut.rd_a_addr.value)
        rd_b_en = int(dut.rd_b_en.value)
        rd_b_addr = int(dut.rd_b_addr.value)

        # The SMEM responds based on what was driven LAST cycle. But our
        # FauxSmem.serve() is "set up next-cycle response from this-cycle
        # request". Since we haven't called serve yet, the response THIS
        # cycle reflects whatever was requested last cycle (None initially,
        # then column-0 captured in this serve call → delivered next).
        a_data, a_valid, b_data, b_valid = smem.serve(
            rd_a_en, rd_a_addr, rd_b_en, rd_b_addr
        )

        dut.rd_a_data.value = _bytes_to_int(a_data)
        dut.rd_a_valid.value = a_valid
        dut.rd_b_data.value = _bytes_to_int(b_data)
        dut.rd_b_valid.value = b_valid

        await RisingEdge(dut.clk)
        py.tick(
            rd_a_data=a_data,
            rd_a_valid=a_valid,
            rd_b_data=b_data,
            rd_b_valid=b_valid,
        )

        await ReadOnly()
        _assert_ports_match(dut, py, ctx=f"compute cycle {cycle}")
        sv_done = int(dut.mma_done.value)
        py_done = int(py.mma_done)
        if sv_done or py_done:
            assert sv_done == py_done == 1, (
                f"mma_done mismatch at cycle {cycle}: sv={sv_done} py={py_done}"
            )
            await NextTimeStep()
            return cycle
        await NextTimeStep()
        cycle += 1
    raise AssertionError(f"mma_done never observed within {max_cycles} cycles")


def _assert_ports_match(dut, py: ComputeArray, ctx: str = "") -> None:
    """Compare every registered output port between SV and pymodel."""
    for name in (
        "mma_busy",
        "mma_done",
        "arrive_en",
        "rd_a_en",
        "rd_b_en",
        "drain_busy",
        "drain_done",
        "drain_row_valid",
        "drain_last",
    ):
        sv = int(getattr(dut, name).value)
        pv = int(getattr(py, name))
        assert sv == pv, f"{ctx}: {name} mismatch sv={sv} py={pv}"
    # Address ports and bar_id only compared when their en is high (data
    # bus is don't-care when valid=0).
    if int(dut.rd_a_en.value):
        assert int(dut.rd_a_addr.value) == int(py.rd_a_addr), (
            f"{ctx}: rd_a_addr mismatch"
        )
    if int(dut.rd_b_en.value):
        assert int(dut.rd_b_addr.value) == int(py.rd_b_addr), (
            f"{ctx}: rd_b_addr mismatch"
        )
    if int(dut.arrive_en.value):
        assert int(dut.arrive_bar_id.value) == int(py.arrive_bar_id), (
            f"{ctx}: arrive_bar_id mismatch"
        )
    if int(dut.drain_row_valid.value):
        assert int(dut.drain_row_idx.value) == int(py.drain_row_idx), (
            f"{ctx}: drain_row_idx mismatch"
        )
        assert int(dut.drain_row_data.value) == int(py.drain_row_data), (
            f"{ctx}: drain_row_data mismatch"
        )


async def _drain_lockstep(
    dut,
    py: ComputeArray,
    slot: int,
    max_cycles: int = MMA_M + 10,
) -> dict[int, np.ndarray]:
    """Issue a drain, lockstep with the pymodel, collect the rows.

    Returns {row_idx: fp32 row} from the drain stream.
    """
    rows: dict[int, np.ndarray] = {}

    # Issue tick.
    dut.drain_issue.value = 1
    dut.drain_slot.value = slot
    dut.rd_a_valid.value = 0
    dut.rd_b_valid.value = 0
    await RisingEdge(dut.clk)
    py.tick(drain_issue=1, drain_slot=slot)
    await ReadOnly()
    _assert_ports_match(dut, py, ctx="drain-issue cycle 0")
    await NextTimeStep()

    dut.drain_issue.value = 0

    saw_done = False
    for c in range(max_cycles):
        await RisingEdge(dut.clk)
        py.tick()
        await ReadOnly()
        _assert_ports_match(dut, py, ctx=f"drain cycle {c+1}")
        if int(dut.drain_row_valid.value):
            idx = int(dut.drain_row_idx.value)
            packed = int(dut.drain_row_data.value)
            row = np.zeros((MMA_N,), dtype=np.float32)
            for j in range(MMA_N):
                word = (packed >> (j * 32)) & 0xFFFFFFFF
                row[j] = _bits_to_fp32(word)
            rows[idx] = row
        if int(dut.drain_done.value):
            saw_done = True
        # Exit once drain_busy clears (this is after drain_done plus the
        # one-cycle DRAIN_LAST state). Lets the caller immediately issue
        # another drain.
        if saw_done and not int(dut.drain_busy.value):
            await NextTimeStep()
            break
        await NextTimeStep()
    assert saw_done, "drain_done never pulsed"
    return rows


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
@cocotb.test()
async def test_directed(dut):
    """One small known matmul; verify both SV-vs-pymodel and SV-vs-numpy."""
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)
    py = ComputeArray()

    rng = np.random.RandomState(0xC0FFEE)
    A_fp32 = (rng.randn(MMA_M, MMA_K) * 0.4).astype(np.float32)
    B_fp32 = (rng.randn(MMA_K, MMA_N) * 0.4).astype(np.float32)
    A_fp8 = encode_e4m3(A_fp32)
    B_fp8 = encode_e4m3(B_fp32)

    await _run_matmul_lockstep(dut, py, A_fp8, B_fp8, slot=0, accum=0)

    # Verify storage matches the expected matmul.
    expected = (decode_e4m3(A_fp8) @ decode_e4m3(B_fp8)).astype(np.float32)
    sv_tile = _read_storage_slot(dut, 0)
    py_tile = py.get_tile(0)
    np.testing.assert_array_equal(sv_tile, py_tile)
    np.testing.assert_allclose(sv_tile, expected, rtol=0, atol=1e-5)

    # Drain row-by-row, verify the stream matches.
    rows = await _drain_lockstep(dut, py, slot=0)
    assert set(rows.keys()) == set(range(MMA_M))
    for i in range(MMA_M):
        np.testing.assert_array_equal(rows[i], expected[i, :])


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """5 random matmuls of various sizes; lockstep with pymodel."""
    await start_clock(dut)
    await _drive_defaults(dut)
    await reset(dut)
    py = ComputeArray()

    rng = np.random.RandomState(0xBADBEEF)
    seeds = [1, 2, 3, 4, 5]
    accum_pattern = [0, 1, 0, 1, 0]
    slots = [0, 0, 1, 1, 2]

    for seed, accum, slot in zip(seeds, accum_pattern, slots):
        local_rng = np.random.RandomState(seed)
        A_fp32 = (local_rng.randn(MMA_M, MMA_K) * 0.3).astype(np.float32)
        B_fp32 = (local_rng.randn(MMA_K, MMA_N) * 0.3).astype(np.float32)
        A_fp8 = encode_e4m3(A_fp32)
        B_fp8 = encode_e4m3(B_fp32)
        await _run_matmul_lockstep(dut, py, A_fp8, B_fp8, slot=slot, accum=accum)

        # Verify both models hold the same tile values.
        sv = _read_storage_slot(dut, slot)
        pv = py.get_tile(slot)
        np.testing.assert_array_equal(sv, pv)

    # Drain each slot used, verify stream.
    for slot in sorted(set(slots)):
        rows = await _drain_lockstep(dut, py, slot=slot)
        assert set(rows.keys()) == set(range(MMA_M))
        ref = py.get_tile(slot)
        for i in range(MMA_M):
            np.testing.assert_array_equal(rows[i], ref[i, :])
