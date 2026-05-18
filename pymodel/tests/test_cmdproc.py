"""Tests for pymodel.cmdproc (integrated via Sim, since cmdproc wires to all engines).

Original tests exercise the engine-dispatch and stall behavior with plain-int
operand programs. New tests (suffix _alu/_repeat) exercise the Pydantic-typed
ALU + register-aware operand path.
"""

import numpy as np

from config import MMA_K, MMA_M, MMA_N, SMEM_TILE_BASE, TMEM_SLOTS
from golden.fp8 import decode_e4m3, encode_e4m3
from golden.matmul_reference import generate
from pymodel.cmdproc import (
    ADDI, BAR_INIT, BRNZ, END, JMP, LOAD, MMA, REPEAT, SET_REG, STORE, WAIT,
    iter_addr, iter_nonzero, reg_off, reg_ref,
)
from pymodel.sim import Sim


# ============================================================================
# Existing tests — still using helper functions (now backed by Pydantic).
# ============================================================================

def test_initial_idle():
    sim = Sim()
    assert sim.is_idle()


def test_bar_init_only():
    """A program that just inits a barrier should idle quickly."""
    sim = Sim()
    sim.load_program([BAR_INIT(0, 2)])
    sim.run_until_idle(max_cycles=10)
    assert sim.barrier.bars[0].expected == 2
    assert sim.barrier.bars[0].pending == 2


def test_load_wait_completes():
    """Push a single LOAD that signals barrier; WAIT should release."""
    sim = Sim()
    sim.load_gmem(0, b"\xab" * 1024)
    sim.load_program([
        BAR_INIT(0, 1),
        LOAD(0, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE, bytes_n=1024),
        WAIT(0, expected_phase=0),
    ])
    sim.run_until_idle()
    assert sim.smem.dump(SMEM_TILE_BASE, 1024) == b"\xab" * 1024


def test_store_sync_stalls_then_completes():
    """STORE holds cmdproc until store.done."""
    sim = Sim()
    tile = np.arange(MMA_M * MMA_N, dtype=np.float32).reshape(MMA_M, MMA_N)
    sim.compute_array.set_tile(0, tile)
    sim.load_program([STORE(tmem_slot=0, gmem_ptr=0, dtype=0)])
    sim.run_until_idle()
    expected = bytes(np.ascontiguousarray(tile.astype("<f4")).tobytes())
    assert sim.gmem.dump(0, len(expected)) == expected


def test_two_loads_async_advance():
    """Two LOADs back-to-back should both complete before WAIT releases."""
    sim = Sim()
    sim.load_gmem(0, b"\x01" * 512)
    sim.load_gmem(512, b"\x02" * 512)
    sim.load_program([
        BAR_INIT(0, 2),
        LOAD(0, 0, SMEM_TILE_BASE, 512),
        LOAD(0, 512, SMEM_TILE_BASE + 512, 512),
        WAIT(0, expected_phase=0),
    ])
    sim.run_until_idle()
    assert sim.smem.dump(SMEM_TILE_BASE, 512) == b"\x01" * 512
    assert sim.smem.dump(SMEM_TILE_BASE + 512, 512) == b"\x02" * 512


# ============================================================================
# REPEAT / END
# ============================================================================

def test_repeat_basic():
    """REPEAT 4 + LOAD inside body + END runs body 4 times."""
    sim = Sim()
    sim.load_gmem(0, b"\xcd" * 64)
    sim.load_program([
        BAR_INIT(0, 4),
        REPEAT(4),
        LOAD(0, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE, bytes_n=64),
        END(),
        WAIT(0, expected_phase=0),
    ])
    sim.run_until_idle()
    # Barrier should have seen 4 arrivals and 4*64=256 bytes tx; phase flipped.
    assert sim.barrier.bars[0].phase == 1


def test_repeat_zero_count():
    """REPEAT 0 skips the entire body."""
    sim = Sim()
    sim.load_program([
        BAR_INIT(0, 1),
        REPEAT(0),
        LOAD(0, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE, bytes_n=64),  # never runs
        END(),
    ])
    sim.run_until_idle()
    # Barrier count=1, no arrivals → pending still 1, phase still 0.
    assert sim.barrier.bars[0].pending == 1
    assert sim.barrier.bars[0].phase == 0


def test_repeat_with_iter_addr():
    """LOAD inside REPEAT uses iter-aware gmem_ptr; each iter loads a different region."""
    sim = Sim()
    # Place 4 distinct beats at gmem offsets 0, 64, 128, 192.
    for k in range(4):
        pat = bytes([(k + 1) & 0xFF] * 64)
        sim.load_gmem(k * 64, pat)

    sim.load_program([
        BAR_INIT(0, 4),
        REPEAT(4),
        LOAD(0,
             gmem_ptr=iter_addr(base=0, stride=64),
             smem_ptr=iter_addr(base=SMEM_TILE_BASE, stride=64),
             bytes_n=64),
        END(),
        WAIT(0, expected_phase=0),
    ])
    sim.run_until_idle()

    for k in range(4):
        assert sim.smem.dump(SMEM_TILE_BASE + k * 64, 64) == bytes([(k + 1) & 0xFF] * 64)


def test_nested_repeat():
    """Outer REPEAT 2 with inner REPEAT 3 — body runs 6 times."""
    sim = Sim()
    sim.load_gmem(0, b"\xee" * 16)
    sim.load_program([
        BAR_INIT(0, 6),
        REPEAT(2),
        REPEAT(3),
        LOAD(0, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE, bytes_n=16),
        END(),
        END(),
        WAIT(0, expected_phase=0),
    ])
    sim.run_until_idle()
    # 6 arrivals (count=6) → phase flips. tx_pending also back to 0.
    assert sim.barrier.bars[0].phase == 1


# ============================================================================
# ALU / branches
# ============================================================================

def test_alu_set_reg_addi():
    """SET_REG + ADDI updates registers correctly."""
    sim = Sim()
    sim.load_program([
        SET_REG(rd=0, value=10),
        ADDI(rd=1, ra=0, imm=5),    # r1 = r0 + 5 = 15
        ADDI(rd=2, ra=1, imm=-7),   # r2 = r1 - 7 = 8
    ])
    sim.run_until_idle()
    assert sim.cmdproc.regs[0] == 10
    assert sim.cmdproc.regs[1] == 15
    assert sim.cmdproc.regs[2] == 8


def test_branch_brnz_loops():
    """Build a 4-iteration counted loop using SET_REG + ADDI + BRNZ (no REPEAT)."""
    sim = Sim()
    # Program:
    #   SET_REG r0, 4       # iter count
    #   SET_REG r1, 0       # accumulator
    # loop:                  (pc=2)
    #   ADDI r1, r1, 7      # r1 += 7
    #   ADDI r0, r0, -1     # r0 -= 1
    #   BRNZ r0, -2         # if r0 != 0, jump back to ADDI r1
    # After loop: r1 = 4*7 = 28, r0 = 0.
    sim.load_program([
        SET_REG(rd=0, value=4),
        SET_REG(rd=1, value=0),
        ADDI(rd=1, ra=1, imm=7),
        ADDI(rd=0, ra=0, imm=-1),
        BRNZ(ra=0, offset=-2),
    ])
    sim.run_until_idle()
    assert sim.cmdproc.regs[0] == 0
    assert sim.cmdproc.regs[1] == 28


def test_jmp_unconditional():
    """JMP skips ahead unconditionally. Semantic: pc += offset from current pc.
    From PC=1 with offset=3, lands at PC=4 (skipping PC=2 and PC=3)."""
    sim = Sim()
    sim.load_program([
        SET_REG(rd=0, value=1),         # PC=0
        JMP(offset=3),                   # PC=1 → pc = 1+3 = 4
        SET_REG(rd=0, value=99),         # PC=2  (skipped)
        SET_REG(rd=1, value=99),         # PC=3  (skipped)
        SET_REG(rd=2, value=42),         # PC=4  (executes)
    ])
    sim.run_until_idle()
    assert sim.cmdproc.regs[0] == 1
    assert sim.cmdproc.regs[1] == 0
    assert sim.cmdproc.regs[2] == 42


def test_load_with_reg_off():
    """LOAD with RegOff operand: gmem_ptr = base + r1."""
    sim = Sim()
    sim.load_gmem(64, b"\xab" * 32)
    sim.load_program([
        BAR_INIT(0, 1),
        SET_REG(rd=1, value=64),
        LOAD(0,
             gmem_ptr=reg_ref(1),
             smem_ptr=SMEM_TILE_BASE,
             bytes_n=32),
        WAIT(0, expected_phase=0),
    ])
    sim.run_until_idle()
    assert sim.smem.dump(SMEM_TILE_BASE, 32) == b"\xab" * 32


# ============================================================================
# Full K-loop matmul using REPEAT + iter_addr (Phase 6 headline)
# ============================================================================

def test_k_loop_matmul_via_repeat():
    """K=128 matmul via REPEAT 4 over K-chunks (each K-chunk = MMA_K=32)."""
    M, N, K_total = MMA_M, MMA_N, 4 * MMA_K  # 32x32x128
    n_chunks = K_total // MMA_K              # 4

    # Build A,B,C using golden reference.
    A_bytes, B_bytes, C_expected = generate(M, N, K_total, seed=0)

    A_gmem = 0
    B_gmem = len(A_bytes)
    C_gmem = 16 * 1024

    A_smem = SMEM_TILE_BASE
    # +32 puts B in a different 8-bank group from A → no bank conflicts.
    B_smem = SMEM_TILE_BASE + MMA_M * MMA_K + 32

    A_chunk_bytes = MMA_M * MMA_K   # one column-major K-chunk of A
    B_chunk_bytes = MMA_K * MMA_N   # one row-major K-chunk of B

    sim = Sim()
    sim.load_gmem(A_gmem, A_bytes)
    sim.load_gmem(B_gmem, B_bytes)

    sim.load_program([
        BAR_INIT(0, 2),   # 2 LOAD arrivals per K-chunk
        BAR_INIT(1, 1),   # 1 MMA arrival per K-chunk
        REPEAT(n_chunks),
        LOAD(0,
             gmem_ptr=iter_addr(base=A_gmem, stride=A_chunk_bytes),
             smem_ptr=A_smem,
             bytes_n=A_chunk_bytes),
        LOAD(0,
             gmem_ptr=iter_addr(base=B_gmem, stride=B_chunk_bytes),
             smem_ptr=B_smem,
             bytes_n=B_chunk_bytes),
        WAIT(0, expected_phase=0),  # phase resets each K-iter via BAR_INIT? No -- single init
        MMA(1,
            a_smem_offset=A_smem,
            b_smem_offset=B_smem,
            d_tmem_slot=0,
            accum=iter_nonzero()),
        WAIT(1, expected_phase=0),
        END(),
        STORE(tmem_slot=0, gmem_ptr=C_gmem, dtype=0),
    ])
    # NOTE: re-issuing WAIT(phase=0) for each iter only works if the barrier
    # is freshly INITed each iter. Since we INIT once outside, phase will
    # flip after each pair of arrivals — so WAIT phase must alternate 0,1,0,1.
    # Easier: re-init barriers inside the loop. Let's rebuild correctly.
    sim2 = Sim()
    sim2.load_gmem(A_gmem, A_bytes)
    sim2.load_gmem(B_gmem, B_bytes)
    sim2.load_program([
        REPEAT(n_chunks),
        BAR_INIT(0, 2),
        BAR_INIT(1, 1),
        LOAD(0,
             gmem_ptr=iter_addr(base=A_gmem, stride=A_chunk_bytes),
             smem_ptr=A_smem,
             bytes_n=A_chunk_bytes),
        LOAD(0,
             gmem_ptr=iter_addr(base=B_gmem, stride=B_chunk_bytes),
             smem_ptr=B_smem,
             bytes_n=B_chunk_bytes),
        WAIT(0, expected_phase=0),
        MMA(1,
            a_smem_offset=A_smem,
            b_smem_offset=B_smem,
            d_tmem_slot=0,
            accum=iter_nonzero()),
        WAIT(1, expected_phase=0),
        END(),
        STORE(tmem_slot=0, gmem_ptr=C_gmem, dtype=0),
    ])
    cycles = sim2.run_until_idle()

    C_bytes = sim2.read_gmem(C_gmem, MMA_M * MMA_N * 4)
    C_actual = np.frombuffer(C_bytes, dtype="<f4").reshape(MMA_M, MMA_N)
    np.testing.assert_allclose(C_actual, C_expected, rtol=0, atol=1e-5)
    print(f"\nK=128 matmul via REPEAT completed in {cycles} cycles")
