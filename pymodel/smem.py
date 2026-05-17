"""
smem — banked on-chip scratchpad.

PURPOSE
    Holds operand tiles. Written by LOAD (gmem→smem). Read by MMA (one column
    of A and one row of B per K-cycle). Barriers do NOT live in SMEM in this
    pymodel — they're internal to the barrier unit. SMEM is purely a byte array
    structured as 32 banks of 4 bytes each (B200-style banking).

LAYOUT
    Conceptually a flat byte array of length SMEM_BYTES. Bank decode:
        bank_of(addr) = (addr / 4) % 32 = addr[6:2]
    A "wide" port access spans multiple banks:
        LOAD_WR  (BEAT_BYTES=16 → 4 dwords) → 4 banks
        MMA_RD_A (MMA_M=32      → 8 dwords) → 8 banks
        MMA_RD_B (MMA_N=32      → 8 dwords) → 8 banks

    Operand tiles begin at SMEM_TILE_BASE; tile placement chosen by host.

PORTS

    LOAD_WR  (write port, BEAT_BYTES wide)
        INPUTS: wr_en, wr_addr, wr_data
        wr_addr must be BEAT_BYTES-aligned.

    MMA_RD_A (read port, MMA_M bytes wide)
        INPUTS:  rd_a_en, rd_a_addr
        OUTPUTS: rd_a_data, rd_a_valid (both registered, 1-cycle latency)
        rd_a_addr must be MMA_M-aligned.

    MMA_RD_B (read port, MMA_N bytes wide)
        Same as MMA_RD_A but width MMA_N and addr aligned to MMA_N.

BANK CONFLICTS and STALLS
    Each of the 32 banks is a 1RW SRAM (1 read OR 1 write per cycle). When
    two or more ports want overlapping banks in the same cycle, only the
    highest-priority port wins; the others STALL.

    PRIORITY (fixed): LOAD_WR > MMA_RD_A > MMA_RD_B.

    Because LOAD_WR (16B aligned) occupies 4 banks within one 8-bank group
    and MMA_RD_A / MMA_RD_B (32B aligned) each occupy a full 8-bank group,
    conflict detection reduces to comparing the 8-bank-group index
    `addr[6:5]`:

        load_wr_stall_out  = 0                                      # top
        mma_rd_a_stall_out = rd_a_en & wr_en
                             & (rd_a_addr[6:5] == wr_addr[6:5])
        mma_rd_b_stall_out = rd_b_en & (
                                (wr_en   & (rd_b_addr[6:5] == wr_addr[6:5]))
                              | (rd_a_en & (rd_b_addr[6:5] == rd_a_addr[6:5]))
                             )

    Stall outputs are COMBINATIONAL on the same cycle's inputs. A consumer
    that sees its stall asserted must re-issue the same request next cycle
    (no internal state advance).

OUTPUTS (registered)
    rd_a_data, rd_a_valid
    rd_b_data, rd_b_valid

OUTPUTS (combinational, this cycle's inputs)
    load_wr_stall_out
    mma_rd_a_stall_out
    mma_rd_b_stall_out

INTERNAL STATE
    mem            : np.uint8[SMEM_BYTES], zero-init
                     (semantically the union of 32 banks × NUM_WORDS_PER_BANK
                     × 4 bytes — exposed as a flat byte array for testbench
                     simplicity)
    rd_a_pending   : addr | None — captured cycle T-1, drained cycle T
    rd_b_pending   : addr | None

BEHAVIOR (per tick, two-phase)
    sample : capture all port signals; recompute stall_out combinationally.
    commit :
        1. If LOAD_WR.wr_en (never stalls): mem[wr_addr : +BEAT_BYTES] = wr_data.
        2. If rd_a_pending: rd_a_data <= mem[rd_a_pending:+MMA_M]; rd_a_valid <= 1.
           else: rd_a_data <= 0; rd_a_valid <= 0.
        3. Same for MMA_RD_B.
        4. Capture new pending reads (gated on !stall):
             if rd_a_en && !mma_rd_a_stall_out: rd_a_pending <= rd_a_addr
             if rd_b_en && !mma_rd_b_stall_out: rd_b_pending <= rd_b_addr

INVARIANTS
    - All addresses in [0, SMEM_BYTES) and ranges in-bounds.
    - LOAD_WR addr is BEAT_BYTES-aligned.
    - MMA_RD_A addr is MMA_M-aligned; MMA_RD_B is MMA_N-aligned.
    - len(wr_data) == BEAT_BYTES when LOAD_WR.wr_en=1.
    - Same-cycle LOAD_WR + MMA_RD_* to byte-OVERLAPPING addresses is illegal
      (asserts). This is a strict-subset case of bank-conflict: byte overlap
      implies bank-group match, which by itself would cause a graceful
      stall — but historically this case has been asserted on, and the
      pymodel preserves that.
    - rd_a_data is all-zero bytes whenever rd_a_valid=0; same for rd_b_data.

HANDSHAKE
    Write: zero-cycle apparent latency on accepted writes.
    Read:  1-cycle latency when not stalled. On stall, request is dropped
           for this cycle; consumer re-issues next.

    WRITE-THEN-DRAIN ordering preserved: LOAD_WR at cycle T commits BEFORE
    the drain of any pending MMA_RD_* read captured at T-1. Drains observe
    NEW data via byte-level write-forwarding.

TESTBENCH BACK-DOOR API
    load(addr: int, data: bytes) -> None
    dump(addr: int, n: int) -> bytes
    Both bypass ports and stalls.

TEST CASES (pymodel/tests/test_smem.py)
    1. load_then_read_a
    2. parallel_reads
    3. wr_then_rd_next_cycle
    4. wr_rd_overlap_same_cycle_asserts
    5. unaligned_addr_asserts (per-port alignment)
    6. backdoor_roundtrip
    7. read_latency_exact_one
    8. NEW: no_conflict_concurrent_3ports
    9. NEW: rd_a_rd_b_conflict
    10. NEW: load_vs_rd_conflict
"""

import numpy as np

from config import BEAT_BYTES, MMA_M, MMA_N, SMEM_BYTES

_ZERO_BEAT = b"\x00" * BEAT_BYTES
_ZERO_A = b"\x00" * MMA_M
_ZERO_B = b"\x00" * MMA_N


def _overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return not (a1 <= b0 or b1 <= a0)


def _group_of(addr: int) -> int:
    """8-bank-group index of an aligned address (= addr[6:5])."""
    return (addr >> 5) & 0x3


class SMEM:
    def __init__(self):
        self.mem = np.zeros(SMEM_BYTES, dtype=np.uint8)
        self._rd_a_pending = None
        self._rd_b_pending = None
        # Registered outputs.
        self.rd_a_data: bytes = _ZERO_A
        self.rd_a_valid: int = 0
        self.rd_b_data: bytes = _ZERO_B
        self.rd_b_valid: int = 0
        # Combinational stall outputs (reset on each tick).
        self.load_wr_stall_out: int = 0
        self.mma_rd_a_stall_out: int = 0
        self.mma_rd_b_stall_out: int = 0

    def tick(
        self,
        wr_en: int = 0,
        wr_addr: int = 0,
        wr_data: bytes = _ZERO_BEAT,
        rd_a_en: int = 0,
        rd_a_addr: int = 0,
        rd_b_en: int = 0,
        rd_b_addr: int = 0,
    ) -> None:
        # Sample-phase asserts.
        if wr_en:
            assert isinstance(wr_data, (bytes, bytearray)), "wr_data must be bytes-like"
            assert len(wr_data) == BEAT_BYTES, (
                f"len(wr_data)={len(wr_data)} must equal BEAT_BYTES={BEAT_BYTES}"
            )
            assert wr_addr % BEAT_BYTES == 0, f"wr_addr {wr_addr} not BEAT_BYTES-aligned"
            assert 0 <= wr_addr <= SMEM_BYTES - BEAT_BYTES, f"wr_addr {wr_addr} OOB"
        if rd_a_en:
            assert rd_a_addr % MMA_M == 0, f"rd_a_addr {rd_a_addr} not MMA_M-aligned"
            assert 0 <= rd_a_addr <= SMEM_BYTES - MMA_M, f"rd_a_addr {rd_a_addr} OOB"
        if rd_b_en:
            assert rd_b_addr % MMA_N == 0, f"rd_b_addr {rd_b_addr} not MMA_N-aligned"
            assert 0 <= rd_b_addr <= SMEM_BYTES - MMA_N, f"rd_b_addr {rd_b_addr} OOB"
        # Byte-level overlap is a strict subset of bank-group conflict; we
        # preserve the original assertion for backward compatibility (see
        # test_wr_rd_overlap_same_cycle_asserts). Pure bank conflicts with
        # non-overlapping bytes are handled gracefully by the stall protocol.
        if wr_en and rd_a_en:
            assert not _overlap(
                wr_addr, wr_addr + BEAT_BYTES, rd_a_addr, rd_a_addr + MMA_M
            ), "LOAD_WR + MMA_RD_A overlap"
        if wr_en and rd_b_en:
            assert not _overlap(
                wr_addr, wr_addr + BEAT_BYTES, rd_b_addr, rd_b_addr + MMA_N
            ), "LOAD_WR + MMA_RD_B overlap"

        # Combinational stall outputs (priority LOAD_WR > RD_A > RD_B).
        wr_rd_a_conflict = bool(wr_en and rd_a_en and _group_of(wr_addr) == _group_of(rd_a_addr))
        wr_rd_b_conflict = bool(wr_en and rd_b_en and _group_of(wr_addr) == _group_of(rd_b_addr))
        rd_a_rd_b_conflict = bool(
            rd_a_en and rd_b_en and _group_of(rd_a_addr) == _group_of(rd_b_addr)
        )
        self.load_wr_stall_out = 0
        self.mma_rd_a_stall_out = 1 if wr_rd_a_conflict else 0
        self.mma_rd_b_stall_out = 1 if (wr_rd_b_conflict or rd_a_rd_b_conflict) else 0

        # Commit phase. Write first so subsequent drains see fresh data if same
        # byte is read on T+1 (not same cycle — that's the asserted overlap case).
        if wr_en:
            self.mem[wr_addr : wr_addr + BEAT_BYTES] = np.frombuffer(
                bytes(wr_data), dtype=np.uint8
            )

        # Drain port A (independent of stall — stall affects new CAPTURE only).
        if self._rd_a_pending is not None:
            a = self._rd_a_pending
            self.rd_a_data = bytes(self.mem[a : a + MMA_M])
            self.rd_a_valid = 1
            self._rd_a_pending = None
        else:
            self.rd_a_data = _ZERO_A
            self.rd_a_valid = 0

        # Drain port B.
        if self._rd_b_pending is not None:
            a = self._rd_b_pending
            self.rd_b_data = bytes(self.mem[a : a + MMA_N])
            self.rd_b_valid = 1
            self._rd_b_pending = None
        else:
            self.rd_b_data = _ZERO_B
            self.rd_b_valid = 0

        # Capture new pending reads, gated on !stall.
        if rd_a_en and not self.mma_rd_a_stall_out:
            self._rd_a_pending = rd_a_addr
        if rd_b_en and not self.mma_rd_b_stall_out:
            self._rd_b_pending = rd_b_addr

    # --- Testbench back-door ---
    def load(self, addr: int, data: bytes) -> None:
        data = bytes(data)
        self.mem[addr : addr + len(data)] = np.frombuffer(data, dtype=np.uint8)

    def dump(self, addr: int, n: int) -> bytes:
        return bytes(self.mem[addr : addr + n])
