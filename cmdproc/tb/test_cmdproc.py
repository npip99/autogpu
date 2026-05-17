"""
cocotb testbench for cmdproc.sv.

Two tests:
  1. test_directed_dispatch  — push one of each opcode, sample the engine-issue
     drives, check FSM transitions (idle, WAIT stall, STORE stall) align with
     the spec.
  2. test_e2e_matmul         — push the headline 8-instruction matmul program;
     wait for sys_idle; compare gmem[C_gmem:] against golden.matmul_reference.

We DO NOT cycle-compare cmdproc vs pymodel.cmdproc — cross-module registered
handoffs add a few cycles, so we test on contract signals and final memory
contents (see DEVELOPMENT.md §"Cross-module registered-handoff latency").
"""

import numpy as np

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, reset
from config import (
    BEAT_BYTES,
    MMA_K,
    MMA_M,
    MMA_N,
    NUM_BARRIERS,
    OP_BAR_INIT,
    OP_LOAD,
    OP_MMA,
    OP_STORE,
    OP_WAIT,
    SMEM_TILE_BASE,
)
from golden.matmul_reference import generate


# ---------------------------------------------------------------------------
# Instruction packing helper. Mirrors the bit layout in cmdproc.sv header.
#
#   [  2:  0]  op (3 bits)
#   [ 10:  3]  bar_id (8 bits)
#   [ 26: 11]  count (16 bits)
#   [ 58: 27]  gmem_ptr (32 bits)
#   [ 90: 59]  smem_ptr (32 bits)
#   [122: 91]  bytes_n (32 bits)
#   [154:123]  a_smem_offset (32 bits)
#   [186:155]  b_smem_offset (32 bits)
#   [194:187]  d_tmem_slot (8 bits)
#   [195:195]  accum (1 bit)
#   [203:196]  tmem_slot (8 bits)
#   [204:204]  dtype (1 bit)
#   [205:205]  expected_phase (1 bit)
# ---------------------------------------------------------------------------
INSTR_WIDTH = 224  # padded to 28 bytes


def pack_instr(
    op: int,
    bar_id: int = 0,
    count: int = 0,
    gmem_ptr: int = 0,
    smem_ptr: int = 0,
    bytes_n: int = 0,
    a_smem_offset: int = 0,
    b_smem_offset: int = 0,
    d_tmem_slot: int = 0,
    accum: int = 0,
    tmem_slot: int = 0,
    dtype: int = 0,
    expected_phase: int = 0,
) -> int:
    w = 0
    w |= (op             & 0x7)        <<   0
    w |= (bar_id         & 0xFF)       <<   3
    w |= (count          & 0xFFFF)     <<  11
    w |= (gmem_ptr       & 0xFFFFFFFF) <<  27
    w |= (smem_ptr       & 0xFFFFFFFF) <<  59
    w |= (bytes_n        & 0xFFFFFFFF) <<  91
    w |= (a_smem_offset  & 0xFFFFFFFF) << 123
    w |= (b_smem_offset  & 0xFFFFFFFF) << 155
    w |= (d_tmem_slot    & 0xFF)       << 187
    w |= (accum          & 0x1)        << 195
    w |= (tmem_slot      & 0xFF)       << 196
    w |= (dtype          & 0x1)        << 204
    w |= (expected_phase & 0x1)        << 205
    return w


def pack_BAR_INIT(bar: int, count: int) -> int:
    return pack_instr(op=OP_BAR_INIT, bar_id=bar, count=count)


def pack_LOAD(bar: int, gmem_ptr: int, smem_ptr: int, bytes_n: int) -> int:
    return pack_instr(op=OP_LOAD, bar_id=bar, gmem_ptr=gmem_ptr,
                      smem_ptr=smem_ptr, bytes_n=bytes_n)


def pack_MMA(bar: int, a_smem_offset: int, b_smem_offset: int,
             d_tmem_slot: int, accum: int) -> int:
    return pack_instr(op=OP_MMA, bar_id=bar, a_smem_offset=a_smem_offset,
                      b_smem_offset=b_smem_offset, d_tmem_slot=d_tmem_slot,
                      accum=accum)


def pack_STORE(tmem_slot: int, gmem_ptr: int, dtype: int = 0) -> int:
    return pack_instr(op=OP_STORE, tmem_slot=tmem_slot,
                      gmem_ptr=gmem_ptr, dtype=dtype)


def pack_WAIT(bar: int, expected_phase: int) -> int:
    return pack_instr(op=OP_WAIT, bar_id=bar, expected_phase=expected_phase)


# ---------------------------------------------------------------------------
# Backdoor helpers (hierarchical access into the wrapper's memories).
# ---------------------------------------------------------------------------
def _backdoor_gmem_write(dut, addr: int, data: bytes) -> None:
    for i, b in enumerate(data):
        dut.u_gmem.mem[addr + i].value = b


def _backdoor_gmem_read(dut, addr: int, n: int) -> bytes:
    out = bytearray(n)
    for i in range(n):
        out[i] = int(dut.u_gmem.mem[addr + i].value)
    return bytes(out)


def _drive_defaults(dut) -> None:
    dut.push_en.value = 0
    dut.push_instr.value = 0


# ---------------------------------------------------------------------------
# Push a single instruction in one cycle.
# ---------------------------------------------------------------------------
async def _push(dut, instr_word: int) -> None:
    dut.push_en.value = 1
    dut.push_instr.value = instr_word
    await RisingEdge(dut.clk)
    dut.push_en.value = 0
    dut.push_instr.value = 0


# ---------------------------------------------------------------------------
# Wait until sys_idle high (cmdproc idle AND all engines idle).
# ---------------------------------------------------------------------------
async def _wait_sys_idle(dut, max_cycles: int) -> int:
    for c in range(max_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.sys_idle.value) == 1:
            await NextTimeStep()
            return c + 1
        await NextTimeStep()
    raise AssertionError(f"sys_idle never asserted within {max_cycles} cycles")


# ---------------------------------------------------------------------------
# Test 1: directed dispatch — verify each opcode produces the right drive
# and FSM transitions through WAIT/STORE.
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_directed_dispatch(dut):
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)

    # After reset, cmdproc should be idle, no drives asserted.
    await ReadOnly()
    assert int(dut.idle.value)           == 1
    assert int(dut.init_en.value)        == 0
    assert int(dut.mma_start.value)      == 0
    assert int(dut.load_issue_en.value)  == 0
    assert int(dut.store_issue_en.value) == 0
    await NextTimeStep()

    # ---- 1) BAR_INIT(0, 1) ----
    # Push at cycle T. Dispatch happens at cycle T's posedge (FIFO empty +
    # push -> take_from_push path). init_en should be high at cycle T+1.
    # count=1 because we only do 1 LOAD on bar 0 in this directed test.
    await _push(dut, pack_BAR_INIT(bar=0, count=1))
    await ReadOnly()
    assert int(dut.init_en.value)     == 1, "BAR_INIT should drive init_en"
    assert int(dut.init_bar_id.value) == 0
    assert int(dut.init_count.value)  == 1
    await NextTimeStep()

    # Next cycle init_en should drop. Cmdproc should be back to idle.
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.init_en.value) == 0, "init_en should pulse for 1 cycle only"
    assert int(dut.idle.value)    == 1
    await NextTimeStep()

    # Init bar 1 with count=1 (needed for MMA wait later).
    await _push(dut, pack_BAR_INIT(bar=1, count=1))
    await RisingEdge(dut.clk)  # let init_en drop

    # ---- 2) LOAD(bar=0, gmem=0, smem=128, bytes=64) ----
    # Preload gmem so the LOAD has something to copy (otherwise the data
    # path is irrelevant for the dispatch test, but the engine needs to
    # produce a sub_tx+arrive to release the barrier).
    pat = bytes((i & 0xFF) for i in range(64))
    _backdoor_gmem_write(dut, 0, pat)

    await _push(dut, pack_LOAD(bar=0, gmem_ptr=0,
                               smem_ptr=SMEM_TILE_BASE, bytes_n=64))
    await ReadOnly()
    assert int(dut.load_issue_en.value) == 1, "LOAD should drive load_issue_en"
    assert int(dut.load_gmem_ptr.value) == 0
    assert int(dut.load_smem_ptr.value) == SMEM_TILE_BASE
    assert int(dut.load_bytes_n.value)  == 64
    assert int(dut.load_bar_id.value)   == 0
    await NextTimeStep()

    # ---- 3) Push WAIT(bar=0, expected_phase=0). The FIFO is non-empty at the
    # moment we push (load just issued, but cmdproc itself is back to IDLE).
    # The WAIT will dispatch the next cycle (or immediately if same-cycle
    # push-into-empty), and the FSM transitions to S_WAITING_FOR_WAIT_DONE.
    await _push(dut, pack_WAIT(bar=0, expected_phase=0))

    # After the WAIT dispatches, query_* should be combinationally driven
    # while in WAITING_FOR_WAIT_DONE. idle should be 0 (state != IDLE).
    # cmdproc.idle goes 0 when state is non-IDLE. Wait one cycle for state
    # to settle, then sample.
    for _ in range(2):
        await RisingEdge(dut.clk)
    await ReadOnly()
    # cmdproc-local idle should be 0 while WAIT pending OR while we're
    # in another state (load could still be busy).
    state_idle = int(dut.idle.value)
    # idle is 0 because state is WAITING_FOR_WAIT_DONE.
    assert state_idle == 0, f"idle should be 0 while WAITING; got {state_idle}"
    # query_bar_id should be 0 (the bar we're waiting on).
    assert int(dut.query_bar_id.value) == 0
    await NextTimeStep()

    # The WAIT shouldn't release until barrier flips on bar 0. Load is going
    # to finish in <some cycles> -> sub_tx + arrive -> barrier flips ->
    # wait_done -> cmdproc releases. Run the system until sys_idle.
    cycles = await _wait_sys_idle(dut, max_cycles=2000)
    cocotb.log.info(f"directed test: LOAD+WAIT settled in {cycles} cycles")

    # Verify bar 0 phase flipped to 1 (since expected was 0 and we WAITed).
    bars_phase = int(dut.bars_phase.value)
    bar0_phase = bars_phase & 1
    assert bar0_phase == 1, f"bar 0 phase should be 1 after flip; got {bar0_phase}"

    # ---- 4) MMA dispatch ----
    # Push an MMA. SMEM has A (bytes 0..) from the LOAD; B is implicitly zero
    # (no preload). The dispatch test only verifies the cmdproc drives;
    # the actual matmul correctness is covered by test_e2e_matmul.
    # First we re-init bar 1 (it's count=1 and unflipped; an MMA will
    # arrive once -> flip).
    await _push(dut, pack_MMA(bar=1, a_smem_offset=SMEM_TILE_BASE,
                              b_smem_offset=SMEM_TILE_BASE + 1024,
                              d_tmem_slot=0, accum=0))
    await ReadOnly()
    assert int(dut.mma_start.value)         == 1, "MMA should drive mma_start"
    assert int(dut.mma_a_smem_offset.value) == SMEM_TILE_BASE
    assert int(dut.mma_b_smem_offset.value) == SMEM_TILE_BASE + 1024
    assert int(dut.mma_d_tmem_slot.value)   == 0
    assert int(dut.mma_accum.value)         == 0
    assert int(dut.mma_bar_id.value)        == 1
    await NextTimeStep()

    # Wait for MMA to finish.
    await _wait_sys_idle(dut, max_cycles=2000)

    # ---- 5) STORE dispatch ----
    # The MMA wrote slot 0; STORE drains it. We just verify the dispatch
    # signals here.
    await _push(dut, pack_STORE(tmem_slot=0, gmem_ptr=16 * 1024, dtype=0))
    await ReadOnly()
    assert int(dut.store_issue_en.value)  == 1
    assert int(dut.store_tmem_slot.value) == 0
    assert int(dut.store_gmem_ptr.value)  == 16 * 1024
    assert int(dut.store_dtype.value)     == 0
    await NextTimeStep()

    # While STORE busy, cmdproc.idle should be 0 (state = WAITING_FOR_STORE_DONE).
    # Verify within a few cycles.
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    await ReadOnly()
    # State should be WAITING_FOR_STORE_DONE -> idle = 0.
    assert int(dut.idle.value) == 0, "idle should be 0 while waiting for STORE"
    # store_issue_en should now be 0 (pulsed for 1 cycle).
    assert int(dut.store_issue_en.value) == 0, (
        "store_issue_en should be 0 after the dispatch cycle (pulse-only)"
    )
    await NextTimeStep()

    cycles = await _wait_sys_idle(dut, max_cycles=4000)
    cocotb.log.info(f"directed test: STORE settled in {cycles} cycles")


# ---------------------------------------------------------------------------
# Test 2: end-to-end matmul.
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_e2e_matmul(dut):
    """
    Headline test: push the same 8-instruction program as
    pymodel/tests/test_e2e.py, wait for sys_idle, compare gmem[C] to
    golden.matmul_reference.
    """
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)

    A_bytes, B_bytes, C_expected = generate(MMA_M, MMA_N, MMA_K, seed=0)

    A_gmem = 0
    B_gmem = len(A_bytes)
    C_gmem = 16 * 1024  # past A/B
    A_smem = SMEM_TILE_BASE
    B_smem = SMEM_TILE_BASE + len(A_bytes)

    # Preload gmem with A_bytes and B_bytes.
    _backdoor_gmem_write(dut, A_gmem, A_bytes)
    _backdoor_gmem_write(dut, B_gmem, B_bytes)

    program = [
        pack_BAR_INIT(bar=0, count=2),
        pack_BAR_INIT(bar=1, count=1),
        pack_LOAD(bar=0, gmem_ptr=A_gmem, smem_ptr=A_smem, bytes_n=len(A_bytes)),
        pack_LOAD(bar=0, gmem_ptr=B_gmem, smem_ptr=B_smem, bytes_n=len(B_bytes)),
        pack_WAIT(bar=0, expected_phase=0),
        pack_MMA(bar=1, a_smem_offset=A_smem, b_smem_offset=B_smem,
                 d_tmem_slot=0, accum=0),
        pack_WAIT(bar=1, expected_phase=0),
        pack_STORE(tmem_slot=0, gmem_ptr=C_gmem, dtype=0),
    ]

    # Push all instructions back-to-back (one per cycle).
    for instr in program:
        dut.push_en.value = 1
        dut.push_instr.value = instr
        await RisingEdge(dut.clk)
    dut.push_en.value = 0
    dut.push_instr.value = 0

    # Run until sys_idle.
    cycles = await _wait_sys_idle(dut, max_cycles=50_000)
    cocotb.log.info(f"e2e matmul: completed in {cycles} cycles "
                    f"(including {len(program)} push cycles)")

    # Read back C from gmem.
    C_bytes = _backdoor_gmem_read(dut, C_gmem, MMA_M * MMA_N * 4)
    C_actual = np.frombuffer(C_bytes, dtype="<f4").reshape(MMA_M, MMA_N)

    np.testing.assert_allclose(C_actual, C_expected, rtol=0, atol=1e-5)
    cocotb.log.info(f"e2e matmul: result matches golden (rtol=0, atol=1e-5)")
