"""
cocotb testbench for chip_top (chip_tb_top wrapper instantiating
chip_top + behavioral off-chip gmem).

This is the chip-level end-to-end harness. Relocated from
cmdproc/tb/test_cmdproc.py as part of Phase 7f when the chip boundary
was drawn (chip_top synthesizable, gmem outside the die). Hierarchy:

    dut (chip_tb_top)
    ├── u_chip   — chip_top (cmdproc + smem + compute_array + load + store
    │              + barrier + reset_seq)  [Phase 7h-3: compute_array
    │              replaces the old mma + tmem pair]
    └── u_gmem   — behavioral off-chip DRAM model

Tests:
  1. test_directed_dispatch  — push one of each old-style opcode, sample
     the engine-issue drives, check FSM transitions align with the spec.
  2. test_e2e_matmul         — push the headline 8-instruction matmul
     program; wait for sys_idle; compare gmem[C_gmem:] against
     golden.matmul_reference.
  3. test_repeat_basic       — REPEAT N + LOAD inside body + END runs N
     times.
  4. test_alu_addi_loop      — manual SET_REG/ADDI/BRNZ loop with no
     engine ops.
  5. test_load_reg_off       — LOAD with reg-offset operand.
  6. test_k_loop_matmul      — REPEAT-driven K-loop matmul (parity-headline);
                               mirrors pymodel test_k_loop_matmul_via_repeat.

We DO NOT cycle-compare cmdproc vs pymodel.cmdproc — cross-module registered
handoffs add a few cycles, so we test on contract signals and final memory
contents (see DEVELOPMENT.md §"Cross-module registered-handoff latency").
"""

import numpy as np

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, reset, wait_until_chip_ready
from config import (
    BEAT_BYTES,
    MMA_K,
    MMA_M,
    MMA_N,
    NUM_BARRIERS,
    OP_BAR_INIT,
    OP_END,
    OP_LOAD,
    OP_MMA,
    OP_REPEAT,
    OP_STORE,
    OP_WAIT,
    SMEM_BYTES,
    SMEM_TILE_BASE,
)
from golden.matmul_reference import generate


# ---------------------------------------------------------------------------
# Opcodes for the new ALU/control-flow instructions. SV-internal — not in
# config.py. Must match the localparams in cmdproc.sv.
# ---------------------------------------------------------------------------
OP_SET_REG = 0x07
OP_ADD     = 0x08
OP_ADDI    = 0x09
OP_SUB     = 0x0A
OP_AND     = 0x0B
OP_BRZ     = 0x0C
OP_BRNZ    = 0x0D
OP_JMP     = 0x0E

# Operand modes (used in pack_LOAD / pack_MMA / pack_STORE).
MODE_IMM       = 0
MODE_ITER_ADDR = 1
MODE_REG_REF   = 2
MODE_REG_OFF   = 3


# ---------------------------------------------------------------------------
# Instruction packing helpers (256-bit packed instruction).
#
# Layout (must match cmdproc.sv header):
#   [  7:  0]  op
#   [ 15:  8]  byte1   — bar_id / rd / tmem_slot
#   [ 23: 16]  byte2   — a_reg / ra / reg
#   [ 31: 24]  byte3   — b_reg / rb
#   [ 33: 32]  a_mode  (2 bits)
#   [ 35: 34]  b_mode  (2 bits)
#   [ 36: 36]  accum_mode (MMA: 0=imm, 1=iter_nonzero)
#   [ 37: 37]  flag1   — dtype / expected_phase / accum_imm
#   [ 47: 40]  d_tmem_slot (MMA)
#   [ 79: 48]  field0  — 32-bit immediate (count / value / imm / offset /
#                        base for a/gmem)
#   [111: 80]  field1  — a/gmem stride
#   [143:112]  field2  — smem/b base
#   [175:144]  field3  — smem/b stride
#   [207:176]  field4  — bytes_n (LOAD)
#   [255:208]  pad
# ---------------------------------------------------------------------------
INSTR_WIDTH = 256
_MASK32 = (1 << 32) - 1


def _u32(x: int) -> int:
    """Mask signed → unsigned 32-bit."""
    return x & _MASK32


def pack_instr(
    op: int,
    byte1: int = 0,
    byte2: int = 0,
    byte3: int = 0,
    a_mode: int = 0,
    b_mode: int = 0,
    accum_mode: int = 0,
    flag1: int = 0,
    d_tmem_slot: int = 0,
    field0: int = 0,
    field1: int = 0,
    field2: int = 0,
    field3: int = 0,
    field4: int = 0,
) -> int:
    w = 0
    w |= (op           & 0xFF)       <<   0
    w |= (byte1        & 0xFF)       <<   8
    w |= (byte2        & 0xFF)       <<  16
    w |= (byte3        & 0xFF)       <<  24
    w |= (a_mode       & 0x3)        <<  32
    w |= (b_mode       & 0x3)        <<  34
    w |= (accum_mode   & 0x1)        <<  36
    w |= (flag1        & 0x1)        <<  37
    w |= (d_tmem_slot  & 0xFF)       <<  40
    w |= (_u32(field0))              <<  48
    w |= (_u32(field1))              <<  80
    w |= (_u32(field2))              << 112
    w |= (_u32(field3))              << 144
    w |= (_u32(field4))              << 176
    return w


def pack_BAR_INIT(bar: int, count: int) -> int:
    return pack_instr(op=OP_BAR_INIT, byte1=bar, field0=count)


def pack_LOAD(
    bar: int,
    gmem_ptr: int,
    smem_ptr: int,
    bytes_n: int,
    *,
    a_mode: int = MODE_IMM,
    b_mode: int = MODE_IMM,
    gmem_stride: int = 0,
    smem_stride: int = 0,
    gmem_reg: int = 0,
    smem_reg: int = 0,
) -> int:
    return pack_instr(
        op=OP_LOAD,
        byte1=bar,
        byte2=gmem_reg,
        byte3=smem_reg,
        a_mode=a_mode,
        b_mode=b_mode,
        field0=gmem_ptr,
        field1=gmem_stride,
        field2=smem_ptr,
        field3=smem_stride,
        field4=bytes_n,
    )


def pack_MMA(
    bar: int,
    a_smem_offset: int,
    b_smem_offset: int,
    d_tmem_slot: int,
    accum: int = 0,
    *,
    a_mode: int = MODE_IMM,
    b_mode: int = MODE_IMM,
    accum_mode: int = 0,
    a_smem_stride: int = 0,
    b_smem_stride: int = 0,
    a_reg: int = 0,
    b_reg: int = 0,
) -> int:
    return pack_instr(
        op=OP_MMA,
        byte1=bar,
        byte2=a_reg,
        byte3=b_reg,
        a_mode=a_mode,
        b_mode=b_mode,
        accum_mode=accum_mode,
        flag1=accum,
        d_tmem_slot=d_tmem_slot,
        field0=a_smem_offset,
        field1=a_smem_stride,
        field2=b_smem_offset,
        field3=b_smem_stride,
    )


def pack_STORE(
    tmem_slot: int,
    gmem_ptr: int,
    dtype: int = 0,
    *,
    a_mode: int = MODE_IMM,
    gmem_stride: int = 0,
    gmem_reg: int = 0,
) -> int:
    return pack_instr(
        op=OP_STORE,
        byte1=tmem_slot,
        byte2=gmem_reg,
        a_mode=a_mode,
        flag1=dtype,
        field0=gmem_ptr,
        field1=gmem_stride,
    )


def pack_WAIT(bar: int, expected_phase: int) -> int:
    return pack_instr(op=OP_WAIT, byte1=bar, field0=expected_phase & 1)


def pack_REPEAT(count: int) -> int:
    return pack_instr(op=OP_REPEAT, field0=count)


def pack_END() -> int:
    return pack_instr(op=OP_END)


def pack_SET_REG(rd: int, value: int) -> int:
    return pack_instr(op=OP_SET_REG, byte1=rd, field0=value)


def pack_ADD(rd: int, ra: int, rb: int) -> int:
    return pack_instr(op=OP_ADD, byte1=rd, byte2=ra, byte3=rb)


def pack_ADDI(rd: int, ra: int, imm: int) -> int:
    return pack_instr(op=OP_ADDI, byte1=rd, byte2=ra, field0=imm)


def pack_SUB(rd: int, ra: int, rb: int) -> int:
    return pack_instr(op=OP_SUB, byte1=rd, byte2=ra, byte3=rb)


def pack_AND(rd: int, ra: int, rb: int) -> int:
    return pack_instr(op=OP_AND, byte1=rd, byte2=ra, byte3=rb)


def pack_BRZ(ra: int, offset: int) -> int:
    return pack_instr(op=OP_BRZ, byte2=ra, field0=offset)


def pack_BRNZ(ra: int, offset: int) -> int:
    return pack_instr(op=OP_BRNZ, byte2=ra, field0=offset)


def pack_JMP(offset: int) -> int:
    return pack_instr(op=OP_JMP, field0=offset)


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


async def _push_program(dut, program: list) -> None:
    for instr in program:
        dut.push_en.value = 1
        dut.push_instr.value = instr
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
    await wait_until_chip_ready(dut)

    # After reset, cmdproc should be idle, no drives asserted.
    await ReadOnly()
    assert int(dut.idle.value)           == 1
    assert int(dut.init_en.value)        == 0
    assert int(dut.mma_start.value)      == 0
    assert int(dut.load_issue_en.value)  == 0
    assert int(dut.store_issue_en.value) == 0
    await NextTimeStep()

    # ---- 1) BAR_INIT(0, 1) ----
    # Push at cycle T. Dispatch happens at cycle T's posedge (imem empty +
    # push -> take_from_push path). init_en should be high at cycle T+1.
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

    # ---- 3) WAIT(bar=0, expected_phase=0).
    await _push(dut, pack_WAIT(bar=0, expected_phase=0))
    for _ in range(2):
        await RisingEdge(dut.clk)
    await ReadOnly()
    state_idle = int(dut.idle.value)
    assert state_idle == 0, f"idle should be 0 while WAITING; got {state_idle}"
    assert int(dut.query_bar_id.value) == 0
    await NextTimeStep()

    cycles = await _wait_sys_idle(dut, max_cycles=2000)
    cocotb.log.info(f"directed test: LOAD+WAIT settled in {cycles} cycles")

    # Backdoor read — barrier no longer exposes bars_phase as a chip pin.
    bar0_phase = int(dut.u_chip.u_barrier.phase[0].value)
    assert bar0_phase == 1, f"bar 0 phase should be 1 after flip; got {bar0_phase}"

    # ---- 4) MMA dispatch ----
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

    await _wait_sys_idle(dut, max_cycles=2000)

    # ---- 5) STORE dispatch ----
    await _push(dut, pack_STORE(tmem_slot=0, gmem_ptr=16 * 1024, dtype=0))
    await ReadOnly()
    assert int(dut.store_issue_en.value)  == 1
    assert int(dut.store_tmem_slot.value) == 0
    assert int(dut.store_gmem_ptr.value)  == 16 * 1024
    assert int(dut.store_dtype.value)     == 0
    await NextTimeStep()

    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.idle.value) == 0, "idle should be 0 while waiting for STORE"
    assert int(dut.store_issue_en.value) == 0, (
        "store_issue_en should be 0 after the dispatch cycle (pulse-only)"
    )
    await NextTimeStep()

    cycles = await _wait_sys_idle(dut, max_cycles=4000)
    cocotb.log.info(f"directed test: STORE settled in {cycles} cycles")


# ---------------------------------------------------------------------------
# Test 2: end-to-end matmul (single K-tile).
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
    await wait_until_chip_ready(dut)

    A_bytes, B_bytes, C_expected = generate(MMA_M, MMA_N, MMA_K, seed=0)

    A_gmem = 0
    B_gmem = len(A_bytes)
    C_gmem = 16 * 1024  # past A/B
    A_smem = SMEM_TILE_BASE
    # +32 puts B in a different 8-bank group from A, avoiding RD_A/RD_B
    # bank conflicts during the MMA. See pymodel/smem.py §BANK CONFLICTS.
    # Post-B1 region-partitioned smem: A in region 0 (addr < 4096), B in
    # region 1 (addr 4096..8191). bank = {addr[13:12], addr[4:2]}.
    B_smem = SMEM_BYTES // 2  # = 4096 = start of region 1

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

    await _push_program(dut, program)

    cycles = await _wait_sys_idle(dut, max_cycles=50_000)
    cocotb.log.info(f"e2e matmul: completed in {cycles} cycles "
                    f"(including {len(program)} push cycles)")

    C_bytes = _backdoor_gmem_read(dut, C_gmem, MMA_M * MMA_N * 4)
    C_actual = np.frombuffer(C_bytes, dtype="<f4").reshape(MMA_M, MMA_N)

    np.testing.assert_allclose(C_actual, C_expected, rtol=0, atol=1e-5)
    cocotb.log.info(f"e2e matmul: result matches golden (rtol=0, atol=1e-5)")


# ---------------------------------------------------------------------------
# Test 3: REPEAT basic — body loops N times.
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_repeat_basic(dut):
    """REPEAT 4 + LOAD inside body + END runs body 4 times."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)
    await wait_until_chip_ready(dut)

    pat = b"\xcd" * 64
    _backdoor_gmem_write(dut, 0, pat)

    program = [
        pack_BAR_INIT(bar=0, count=4),
        pack_REPEAT(4),
        pack_LOAD(bar=0, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE, bytes_n=64),
        pack_END(),
        pack_WAIT(bar=0, expected_phase=0),
    ]
    await _push_program(dut, program)

    cycles = await _wait_sys_idle(dut, max_cycles=10_000)
    cocotb.log.info(f"repeat_basic: settled in {cycles} cycles")

    # Barrier should have seen 4 arrivals → phase flipped.
    bar0_phase = int(dut.u_chip.u_barrier.phase[0].value)
    assert bar0_phase == 1, (
        f"bar 0 phase should be 1 after 4 loop iters; got {bar0_phase}"
    )


# ---------------------------------------------------------------------------
# Test 4: ALU ADDI loop — manual counted loop using SET_REG/ADDI/BRNZ.
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_alu_addi_loop(dut):
    """4-iteration counted loop with no engine ops; verify reg state via
       observable side effect (single LOAD using register-relative addr at the
       end)."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)
    await wait_until_chip_ready(dut)

    # Place data so a LOAD at gmem=64 (r0 final value after loop: 64) copies it.
    pat = bytes([0x5A] * 32)
    _backdoor_gmem_write(dut, 64, pat)

    # Program:
    #   SET_REG r0, 0
    #   SET_REG r2, 4           # iter count
    # loop:                       (pc=2)
    #   ADDI r0, r0, 16         # r0 += 16
    #   ADDI r2, r2, -1         # r2 -= 1
    #   BRNZ r2, -2             # if r2 != 0, back to ADDI r0
    # # r0 should be 64 after 4 iters.
    #   BAR_INIT(0, 1)
    #   LOAD bar=0, gmem=reg_ref(r0), smem=SMEM_TILE_BASE, bytes=32
    #   WAIT(0, 0)
    program = [
        pack_SET_REG(rd=0, value=0),
        pack_SET_REG(rd=2, value=4),
        pack_ADDI(rd=0, ra=0, imm=16),
        pack_ADDI(rd=2, ra=2, imm=-1),
        pack_BRNZ(ra=2, offset=-2),
        pack_BAR_INIT(bar=0, count=1),
        pack_LOAD(
            bar=0,
            gmem_ptr=0, smem_ptr=SMEM_TILE_BASE, bytes_n=32,
            a_mode=MODE_REG_REF, gmem_reg=0,
        ),
        pack_WAIT(bar=0, expected_phase=0),
    ]
    await _push_program(dut, program)

    cycles = await _wait_sys_idle(dut, max_cycles=10_000)
    cocotb.log.info(f"alu_addi_loop: settled in {cycles} cycles")

    for _ in range(10):
        await RisingEdge(dut.clk)

    # If the loop and reg-ref operand worked, the data at gmem[64:64+32] will
    # have been loaded into smem[SMEM_TILE_BASE..]. Verify via the smem.
    actual = bytes(int(dut.u_chip.u_smem.mem[SMEM_TILE_BASE + i].value) for i in range(32))
    assert actual == pat, (
        f"alu_addi_loop: smem mismatch; final r0 likely wrong "
        f"(expected 64 → gmem[64:64+32]=={pat!r}, got {actual!r})"
    )


# ---------------------------------------------------------------------------
# Test 5: LOAD with reg-offset operand.
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_load_reg_off(dut):
    """LOAD with reg-offset operand: gmem_ptr = base(0) + regs[1]."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)
    await wait_until_chip_ready(dut)

    pat = b"\xab" * 32
    _backdoor_gmem_write(dut, 64, pat)

    program = [
        pack_BAR_INIT(bar=0, count=1),
        pack_SET_REG(rd=1, value=64),
        pack_LOAD(
            bar=0,
            gmem_ptr=0,                              # base=0
            smem_ptr=SMEM_TILE_BASE,
            bytes_n=32,
            a_mode=MODE_REG_OFF, gmem_reg=1,         # gmem = 0 + regs[1]
        ),
        pack_WAIT(bar=0, expected_phase=0),
    ]
    await _push_program(dut, program)

    cycles = await _wait_sys_idle(dut, max_cycles=10_000)
    cocotb.log.info(f"load_reg_off: settled in {cycles} cycles")

    # Give the LOAD pipeline a few more cycles to drain its smem writes
    # (cross-module registered handoff latency: see DEVELOPMENT.md).
    for _ in range(10):
        await RisingEdge(dut.clk)

    actual = bytes(int(dut.u_chip.u_smem.mem[SMEM_TILE_BASE + i].value) for i in range(32))
    assert actual == pat


# ---------------------------------------------------------------------------
# Test 6: K-loop matmul via REPEAT — the headline parity test.
# Mirrors pymodel/tests/test_cmdproc.py::test_k_loop_matmul_via_repeat.
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_k_loop_matmul(dut):
    """K=128 matmul via REPEAT 4 over K-chunks (each K-chunk = MMA_K=32)."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)
    await wait_until_chip_ready(dut)

    M, N, K_total = MMA_M, MMA_N, 4 * MMA_K  # 32x32x128
    n_chunks = K_total // MMA_K              # 4

    A_bytes, B_bytes, C_expected = generate(M, N, K_total, seed=0)

    A_gmem = 0
    B_gmem = len(A_bytes)
    C_gmem = 16 * 1024

    A_smem = SMEM_TILE_BASE
    # +32 puts B in a different 8-bank group from A → no bank conflicts.
    # Post-B1 region-partitioned smem: A in region 0, B in region 1.
    B_smem = SMEM_BYTES // 2  # = 4096 = start of region 1

    A_chunk_bytes = MMA_M * MMA_K   # 1024 bytes
    B_chunk_bytes = MMA_K * MMA_N   # 1024 bytes

    _backdoor_gmem_write(dut, A_gmem, A_bytes)
    _backdoor_gmem_write(dut, B_gmem, B_bytes)

    program = [
        pack_REPEAT(n_chunks),
        pack_BAR_INIT(bar=0, count=2),
        pack_BAR_INIT(bar=1, count=1),
        pack_LOAD(
            bar=0,
            gmem_ptr=A_gmem, smem_ptr=A_smem, bytes_n=A_chunk_bytes,
            a_mode=MODE_ITER_ADDR, gmem_stride=A_chunk_bytes,
        ),
        pack_LOAD(
            bar=0,
            gmem_ptr=B_gmem, smem_ptr=B_smem, bytes_n=B_chunk_bytes,
            a_mode=MODE_ITER_ADDR, gmem_stride=B_chunk_bytes,
        ),
        pack_WAIT(bar=0, expected_phase=0),
        pack_MMA(
            bar=1,
            a_smem_offset=A_smem, b_smem_offset=B_smem,
            d_tmem_slot=0, accum=0,
            accum_mode=1,  # iter_nonzero
        ),
        pack_WAIT(bar=1, expected_phase=0),
        pack_END(),
        pack_STORE(tmem_slot=0, gmem_ptr=C_gmem, dtype=0),
    ]

    await _push_program(dut, program)

    cycles = await _wait_sys_idle(dut, max_cycles=200_000)
    cocotb.log.info(
        f"k_loop_matmul: K={K_total} ({n_chunks} chunks) completed in "
        f"{cycles} cycles (pymodel ref: 919)"
    )

    C_bytes = _backdoor_gmem_read(dut, C_gmem, MMA_M * MMA_N * 4)
    C_actual = np.frombuffer(C_bytes, dtype="<f4").reshape(MMA_M, MMA_N)

    np.testing.assert_allclose(C_actual, C_expected, rtol=0, atol=1e-5)
    cocotb.log.info("k_loop_matmul: result matches golden (rtol=0, atol=1e-5)")
