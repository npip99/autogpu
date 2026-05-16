"""
smem — on-chip scratchpad.

PURPOSE
    Holds operand tiles. Written by LOAD (gmem→smem). Read by MMA (one column
    of A and one row of B per K-cycle). Barriers do NOT live in SMEM in this
    pymodel — they're internal to the barrier unit. SMEM is purely a byte array.

LAYOUT
    Single flat byte array of length SMEM_BYTES. Operand tiles begin at
    SMEM_TILE_BASE (the first NUM_BARRIERS*BARRIER_BYTES of address space
    is reserved for future barrier-in-SMEM support). Operand placement
    within [SMEM_TILE_BASE, SMEM_BYTES) is chosen by the host program.

PORTS (each port is independent; no inter-port arbitration in pymodel)

    LOAD_WR (write port, BEAT_BYTES wide)
        INPUTS:  wr_en, wr_addr, wr_data (BEAT_BYTES bytes)
        cycle T: if wr_en, mem[wr_addr : wr_addr+BEAT_BYTES] = wr_data
        wr_addr must be BEAT_BYTES-aligned.

    MMA_RD_A (read port, MMA_M bytes wide)
        INPUTS:  rd_en, rd_addr
        OUTPUTS: rd_data (MMA_M bytes, registered), rd_valid (1-bit, registered)
        cycle T: rd_en + rd_addr captured.
        cycle T+1: rd_data = mem[rd_addr_prev : +MMA_M]; rd_valid = 1.
        rd_addr must be MMA_M-aligned.

    MMA_RD_B (read port, MMA_N bytes wide)
        same semantics as MMA_RD_A but width MMA_N and addr aligned to MMA_N.

INTERNAL STATE
    mem            : np.uint8[SMEM_BYTES], zero-init
    rd_a_pending   : addr | None
    rd_b_pending   : addr | None

BEHAVIOR (per tick, two-phase)
    sample : capture all port signals
    commit :
        1. If LOAD_WR.wr_en: mem[wr_addr : +BEAT_BYTES] = wr_data.
        2. If rd_a_pending: rd_a_data <= mem[rd_a_pending : +MMA_M]; rd_a_valid <= 1.
           else: rd_a_data <= 0; rd_a_valid <= 0.
        3. Same for MMA_RD_B with rd_b_pending and MMA_N width.
        4. Capture new pending reads from rd_en/rd_addr.

INVARIANTS
    - All addresses in [0, SMEM_BYTES) and ranges in-bounds.
    - LOAD_WR addr is BEAT_BYTES-aligned.
    - MMA_RD_A addr is MMA_M-aligned; MMA_RD_B is MMA_N-aligned.
    - len(wr_data) == BEAT_BYTES when LOAD_WR.wr_en=1 (else assert).
    - Same-cycle LOAD_WR + MMA_RD_* to OVERLAPPING addresses is undefined (assert).
    - rd_a_data is all-zero bytes (b"\x00" * MMA_M) whenever rd_a_valid=0; same for rd_b_data / MMA_N.
    - Reads to operand-tile region [SMEM_TILE_BASE, SMEM_BYTES) only;
      reads/writes to [0, SMEM_TILE_BASE) are not protected but should not occur.

HANDSHAKE
    Write: zero-cycle apparent latency (mem updated by end of issuing cycle).
    Read:  1-cycle latency, exact.

    WRITE-THEN-DRAIN ordering: per BEHAVIOR step ordering, LOAD_WR at cycle T
    commits BEFORE the drain of any pending MMA_RD_* read captured at T-1. So
    a pending read whose address overlaps a same-cycle write returns the NEW
    data. This is distinct from the asserted same-cycle wr_en + rd_en overlap
    (which involves a NEW read request at T, not a pending one).
    RTL implementers: requires byte-level write-forwarding on each read port's
    drain path. See tmem.sv / gmem.sv for the pattern.

TESTBENCH BACK-DOOR API
    load(addr: int, data: bytes) -> None
    dump(addr: int, n: int) -> bytes
    Both bypass ports.

TEST CASES (pymodel/tests/test_smem.py)
    1. load_then_read_a: backdoor-load a tile, MMA_RD_A reads first column, matches.
    2. parallel_reads: MMA_RD_A reads addr X, MMA_RD_B reads addr Y in same cycle, both return correct data next cycle.
    3. wr_then_rd_next_cycle: LOAD_WR a beat at cycle T, MMA_RD_A reads same addr at T+1 → returns the written data.
    4. wr_rd_overlap_same_cycle_asserts.
    5. unaligned_addr_asserts (per-port alignment).
    6. backdoor_roundtrip.
    7. read_latency_exact_one.
"""

import numpy as np

from config import BEAT_BYTES, MMA_M, MMA_N, SMEM_BYTES

_ZERO_BEAT = b"\x00" * BEAT_BYTES
_ZERO_A = b"\x00" * MMA_M
_ZERO_B = b"\x00" * MMA_N


def _overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return not (a1 <= b0 or b1 <= a0)


class SMEM:
    def __init__(self):
        self.mem = np.zeros(SMEM_BYTES, dtype=np.uint8)
        self._rd_a_pending = None
        self._rd_b_pending = None
        self.rd_a_data: bytes = _ZERO_A
        self.rd_a_valid: int = 0
        self.rd_b_data: bytes = _ZERO_B
        self.rd_b_valid: int = 0

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
        if wr_en and rd_a_en:
            assert not _overlap(
                wr_addr, wr_addr + BEAT_BYTES, rd_a_addr, rd_a_addr + MMA_M
            ), "LOAD_WR + MMA_RD_A overlap"
        if wr_en and rd_b_en:
            assert not _overlap(
                wr_addr, wr_addr + BEAT_BYTES, rd_b_addr, rd_b_addr + MMA_N
            ), "LOAD_WR + MMA_RD_B overlap"

        # Commit phase. Write first so subsequent drains see fresh data if same addr
        # is read on T+1 (not same cycle — that's the asserted overlap case).
        if wr_en:
            self.mem[wr_addr : wr_addr + BEAT_BYTES] = np.frombuffer(
                bytes(wr_data), dtype=np.uint8
            )

        # Drain port A.
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

        # Capture new pending reads.
        if rd_a_en:
            self._rd_a_pending = rd_a_addr
        if rd_b_en:
            self._rd_b_pending = rd_b_addr

    # --- Testbench back-door ---
    def load(self, addr: int, data: bytes) -> None:
        data = bytes(data)
        self.mem[addr : addr + len(data)] = np.frombuffer(data, dtype=np.uint8)

    def dump(self, addr: int, n: int) -> bytes:
        return bytes(self.mem[addr : addr + n])
