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

HANDSHAKE
    Write: 0-cycle visible (committed by end of issuing cycle).
    Read:  1-cycle latency, exact.

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

# Implementation goes here.
