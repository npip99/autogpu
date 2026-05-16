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
    - Same-cycle LOAD_WR + MMA_RD_* to OVERLAPPING addresses is undefined (assert).
    - Reads to operand-tile region [SMEM_TILE_BASE, SMEM_BYTES) only;
      reads/writes to [0, SMEM_TILE_BASE) are not protected but should not occur.

HANDSHAKE
    Write: zero-cycle apparent latency (mem updated by end of issuing cycle).
    Read:  1-cycle latency, exact.

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

# Implementation goes here.
