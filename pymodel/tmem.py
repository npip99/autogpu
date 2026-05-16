"""
tmem — accumulator scratchpad.

PURPOSE
    Stores MMA accumulator tiles. Each slot is one MMA_M × MMA_N fp32 tile.
    Written by MMA (final accumulator after K cycles). Read by MMA (initial
    value if accum=1) and by STORE (drain to gmem).

LAYOUT
    Array of TMEM_SLOTS slots, each shape (MMA_M, MMA_N), dtype float32.

PORTS

    MMA_PORT (one cycle: read OR write, not both)
        INPUTS:  op (NONE | READ | WRITE), slot, write_tile (MMA_M×MMA_N fp32)
        OUTPUTS: rd_tile (MMA_M×MMA_N fp32, registered), rd_valid (1-bit)
        cycle T:
            if op == WRITE: slots[slot] = write_tile (commits this cycle)
            if op == READ:  capture (slot) → rd_valid at T+1 with rd_tile

    STORE_RD (read-only)
        INPUTS:  rd_en, slot
        OUTPUTS: rd_tile, rd_valid (registered)
        cycle T: if rd_en, capture slot → rd_valid at T+1 with rd_tile

INTERNAL STATE
    slots          : np.float32[TMEM_SLOTS, MMA_M, MMA_N], zero-init
    mma_rd_pending : slot | None
    store_rd_pending: slot | None

BEHAVIOR (per tick, two-phase)
    sample : capture op + slot + write_tile per port
    commit :
        1. If MMA_PORT.op == WRITE: slots[slot] = write_tile.
        2. Drain MMA_PORT pending read: rd_tile <= slots[mma_rd_pending], rd_valid <= 1.
        3. Drain STORE_RD pending: rd_tile <= slots[store_rd_pending], rd_valid <= 1.
        4. Capture new pending reads.

INVARIANTS
    - slot in [0, TMEM_SLOTS).
    - write_tile.shape == (MMA_M, MMA_N) and dtype float32.
    - Same-cycle MMA_PORT WRITE + STORE_RD on same slot is undefined (assert).
    - MMA_PORT cannot simultaneously read and write (op is one of NONE/READ/WRITE).
    - rd_tile is np.zeros((MMA_M, MMA_N), dtype=np.float32) whenever rd_valid=0 (never stale).

HANDSHAKE
    Write: 0-cycle visible (committed by end of issuing cycle).
    Read:  1-cycle latency, exact.

    WRITE-THEN-DRAIN ordering: per BEHAVIOR step ordering, an MMA_PORT WRITE at
    cycle T commits BEFORE the drain of a pending MMA or STORE read captured at
    T-1. So a pending read of slot S that's being written at T returns the NEW
    tile. (The asserted constraint is on same-cycle WRITE + STORE_RD CAPTURE on
    the same slot — a fresh read request, not a drain.)
    RTL implementers: requires tile-level write-forwarding on the drain path.

RTL TILE PACKING CONVENTION (for any module that wires to TMEM in SV)
    Tile signals (mma_rd_tile, mma_write_tile, store_rd_tile) are packed
    MMA_M * MMA_N * 32-bit logic vectors with this layout:
      - Element at row i, column j (0-indexed) lives in bits
            [((i*MMA_N + j) * 32) +: 32]
        of the packed vector. Row-major; [0][0] occupies the low 32 bits.
      - Each 32-bit slot holds an IEEE 754 fp32 bit pattern, LSB-first.
      - Python equivalent for packing a numpy array into an int:
            int.from_bytes(tile.astype('<f4').tobytes(), "little")
    MMA, STORE, and any cocotb adapter MUST use this convention.

TESTBENCH BACK-DOOR API
    set_slot(slot: int, tile: np.ndarray) -> None
    get_slot(slot: int) -> np.ndarray

TEST CASES (pymodel/tests/test_tmem.py)
    1. write_then_read_same_slot: MMA_PORT writes slot 0, STORE_RD reads it next cycle, tiles match.
    2. parallel_reads_different_slots: MMA_PORT reads slot 0, STORE_RD reads slot 1 same cycle.
    3. write_persists: write slot 2 at T=0, do nothing for 5 cycles, read returns same tile.
    4. backdoor_roundtrip: set_slot / get_slot equivalent to port writes/reads.
    5. assert_slot_out_of_range.
    6. assert_wrong_dtype_or_shape on write_tile.
"""

from enum import IntEnum

import numpy as np

from config import MMA_M, MMA_N, TMEM_SLOTS


class MMAOp(IntEnum):
    NONE = 0
    READ = 1
    WRITE = 2


class TMEM:
    def __init__(self):
        self.slots = np.zeros((TMEM_SLOTS, MMA_M, MMA_N), dtype=np.float32)
        self._mma_rd_pending = None  # slot index or None
        self._store_rd_pending = None
        self.mma_rd_tile = np.zeros((MMA_M, MMA_N), dtype=np.float32)
        self.mma_rd_valid: int = 0
        self.store_rd_tile = np.zeros((MMA_M, MMA_N), dtype=np.float32)
        self.store_rd_valid: int = 0

    def tick(
        self,
        mma_op: int = MMAOp.NONE,
        mma_slot: int = 0,
        mma_write_tile=None,
        store_rd_en: int = 0,
        store_rd_slot: int = 0,
    ) -> None:
        mma_op = MMAOp(int(mma_op))

        # Sample-phase asserts.
        if mma_op != MMAOp.NONE:
            assert 0 <= mma_slot < TMEM_SLOTS, f"mma_slot {mma_slot} OOR"
        if mma_op == MMAOp.WRITE:
            assert mma_write_tile is not None, "mma_write_tile required for WRITE"
            assert mma_write_tile.shape == (MMA_M, MMA_N), (
                f"mma_write_tile shape {mma_write_tile.shape} != ({MMA_M},{MMA_N})"
            )
            assert mma_write_tile.dtype == np.float32, (
                f"mma_write_tile dtype {mma_write_tile.dtype} must be float32"
            )
        if store_rd_en:
            assert 0 <= store_rd_slot < TMEM_SLOTS, f"store_rd_slot {store_rd_slot} OOR"
        if mma_op == MMAOp.WRITE and store_rd_en:
            assert mma_slot != store_rd_slot, (
                f"same-slot WRITE+STORE_RD on slot {mma_slot} undefined"
            )

        # Commit phase.
        if mma_op == MMAOp.WRITE:
            self.slots[mma_slot] = mma_write_tile

        # Drain MMA read.
        if self._mma_rd_pending is not None:
            self.mma_rd_tile = self.slots[self._mma_rd_pending].copy()
            self.mma_rd_valid = 1
            self._mma_rd_pending = None
        else:
            self.mma_rd_tile = np.zeros((MMA_M, MMA_N), dtype=np.float32)
            self.mma_rd_valid = 0

        # Drain STORE read.
        if self._store_rd_pending is not None:
            self.store_rd_tile = self.slots[self._store_rd_pending].copy()
            self.store_rd_valid = 1
            self._store_rd_pending = None
        else:
            self.store_rd_tile = np.zeros((MMA_M, MMA_N), dtype=np.float32)
            self.store_rd_valid = 0

        # Capture new pending reads.
        if mma_op == MMAOp.READ:
            self._mma_rd_pending = mma_slot
        if store_rd_en:
            self._store_rd_pending = store_rd_slot

    # --- Testbench back-door ---
    def set_slot(self, slot: int, tile: np.ndarray) -> None:
        assert 0 <= slot < TMEM_SLOTS
        assert tile.shape == (MMA_M, MMA_N)
        assert tile.dtype == np.float32
        self.slots[slot] = tile

    def get_slot(self, slot: int) -> np.ndarray:
        assert 0 <= slot < TMEM_SLOTS
        return self.slots[slot].copy()
