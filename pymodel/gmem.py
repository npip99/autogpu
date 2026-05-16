"""
gmem — external DRAM model.

PURPOSE
    Backs the simulated off-chip memory. A flat byte array of GMEM_BYTES.
    During execution it serves LOAD reads and STORE writes. The testbench
    additionally has back-door API to preload A,B before launch and dump C
    after.

INPUTS (sampled at tick start; cleared each cycle by caller)
    reset       : 1-bit    — sync clear of pending state (mem contents preserved)
    rd_en       : 1-bit
    rd_addr     : 32-bit byte address (must be BEAT_BYTES-aligned)
    wr_en       : 1-bit
    wr_addr     : 32-bit byte address (must be BEAT_BYTES-aligned)
    wr_data     : BEAT_BYTES bytes

OUTPUTS (valid after tick, registered)
    rd_data     : BEAT_BYTES bytes — contents of mem[rd_addr_prev : +BEAT_BYTES]
    rd_valid    : 1-bit              — high the cycle AFTER rd_en was high

INTERNAL STATE
    mem         : np.uint8[GMEM_BYTES]   — zero-initialized
    rd_pending  : (addr) | None          — captured this cycle, drained next

BEHAVIOR (per tick, two-phase)
    sample phase:
        capture rd_en, rd_addr, wr_en, wr_addr, wr_data, reset
    commit phase:
        1. if reset:
               rd_pending = None
               rd_valid <= 0
               rd_data  <= 0
               (mem untouched)
               return
           NOTE: reset is DOMINANT — when reset=1, alignment/overlap/length
           asserts (see INVARIANTS) are SUPPRESSED and other inputs are
           ignored for this cycle.
        2. if wr_en:
               mem[wr_addr : wr_addr+BEAT_BYTES] = wr_data
        3. if rd_pending is set (from previous cycle):
               rd_data  <= mem[rd_pending.addr : rd_pending.addr+BEAT_BYTES]
               rd_valid <= 1
               rd_pending = None
           else:
               rd_data  <= 0
               rd_valid <= 0
        4. if rd_en:
               rd_pending = (rd_addr,)
        (rd_en and wr_en may both be asserted in the same cycle — independent paths.)

INVARIANTS
    - rd_addr + BEAT_BYTES <= GMEM_BYTES (else assert at sample phase)
    - wr_addr + BEAT_BYTES <= GMEM_BYTES (else assert)
    - rd_addr and wr_addr are BEAT_BYTES-aligned (else assert)
    - len(wr_data) == BEAT_BYTES when wr_en=1 (else assert)
    - rd_data is all-zero bytes (b"\x00" * BEAT_BYTES) whenever rd_valid=0 (never stale)
    - rd_en in cycle T → rd_valid in cycle T+1 exactly (never T, never T+2)

HANDSHAKE
    Read: 1-cycle latency, fixed. No back-pressure (gmem always accepts).
    Write: 0-cycle apparent latency. mem is updated by end of the issuing cycle;
           subsequent reads of the same address (issued cycle T+1 or later) see
           the new data.
    Same-cycle read and write to OVERLAPPING addresses is undefined behavior —
    asserts. (Strict no-RAW-in-same-cycle policy.)

    WRITE-THEN-DRAIN ordering: per BEHAVIOR step ordering, a wr_en at cycle T
    commits BEFORE the drain of the prior-cycle's pending read (the one captured
    by rd_en at cycle T-1). So if rd_addr at T-1 happens to equal wr_addr at T,
    the rd_data delivered at T reflects the NEW data, not the old. This is a
    DIFFERENT scenario from the asserted same-cycle r/w overlap — it involves a
    drained pending read from a previous cycle, which is legal.
    RTL implementers: this requires a byte-level write-forwarding mux on the
    drain path. Verified by gmem cocotb tests against this pymodel.

TESTBENCH BACK-DOOR API (not part of the hardware port; bypasses ports)
    load(addr: int, data: bytes) -> None
        Direct write to mem; any length, any alignment. Used to preload A,B.
    dump(addr: int, n: int) -> bytes
        Direct read from mem; any length, any alignment. Used to verify C.
    These functions are zero-latency and do not interact with rd_pending.

TEST CASES (pymodel/tests/test_gmem.py)
    1. write_then_read_roundtrip: wr_en(addr=0, data=pattern), next cycle
       rd_en(addr=0); cycle after: rd_data == pattern, rd_valid == 1.
    2. read_latency_is_one: rd_en at T → rd_valid==0 at T, rd_valid==1 at T+1, rd_valid==0 at T+2.
    3. concurrent_rw_different_addresses: same cycle rd_en(A) + wr_en(B) succeed independently.
    4. wr_persists: write at T; T+10 read same addr returns written value.
    5. reset_clears_pending: rd_en at T, reset at T+1 → rd_valid at T+1 is 0.
    6. backdoor_load_dump_roundtrip: load(0, b"\\x01\\x02..."); dump(0,N) returns same bytes.
    7. backdoor_visible_to_port: load(addr, data); rd_en(addr); rd_data matches.
    8. assert_unaligned_addr: rd_en with addr=1 raises AssertionError.
    9. assert_overlap_rw: same cycle rd_en(0)+wr_en(0) raises AssertionError.
"""

import numpy as np

from config import BEAT_BYTES, GMEM_BYTES

_ZERO_BEAT = b"\x00" * BEAT_BYTES


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return not (a_end <= b_start or b_end <= a_start)


class GMEM:
    def __init__(self):
        self.mem = np.zeros(GMEM_BYTES, dtype=np.uint8)
        self._rd_pending = None  # captured rd_addr from previous tick, or None
        self.rd_data: bytes = _ZERO_BEAT
        self.rd_valid: int = 0

    def tick(
        self,
        reset: int = 0,
        rd_en: int = 0,
        rd_addr: int = 0,
        wr_en: int = 0,
        wr_addr: int = 0,
        wr_data: bytes = _ZERO_BEAT,
    ) -> None:
        # Sample-phase asserts (suppressed when reset is dominant).
        if not reset:
            if wr_en:
                assert isinstance(wr_data, (bytes, bytearray)), "wr_data must be bytes-like"
                assert len(wr_data) == BEAT_BYTES, (
                    f"len(wr_data)={len(wr_data)} must equal BEAT_BYTES={BEAT_BYTES}"
                )
                assert wr_addr % BEAT_BYTES == 0, f"wr_addr {wr_addr} not BEAT_BYTES-aligned"
                assert 0 <= wr_addr <= GMEM_BYTES - BEAT_BYTES, f"wr_addr {wr_addr} OOB"
            if rd_en:
                assert rd_addr % BEAT_BYTES == 0, f"rd_addr {rd_addr} not BEAT_BYTES-aligned"
                assert 0 <= rd_addr <= GMEM_BYTES - BEAT_BYTES, f"rd_addr {rd_addr} OOB"
            if rd_en and wr_en:
                assert not _ranges_overlap(
                    rd_addr, rd_addr + BEAT_BYTES, wr_addr, wr_addr + BEAT_BYTES
                ), f"same-cycle r/w overlap: rd={rd_addr} wr={wr_addr}"

        # Commit phase.
        if reset:
            self._rd_pending = None
            self.rd_data = _ZERO_BEAT
            self.rd_valid = 0
            return

        if wr_en:
            self.mem[wr_addr : wr_addr + BEAT_BYTES] = np.frombuffer(
                bytes(wr_data), dtype=np.uint8
            )

        # Drain previous pending read into registered outputs.
        if self._rd_pending is not None:
            addr = self._rd_pending
            self.rd_data = bytes(self.mem[addr : addr + BEAT_BYTES])
            self.rd_valid = 1
            self._rd_pending = None
        else:
            self.rd_data = _ZERO_BEAT
            self.rd_valid = 0

        # Capture new pending read for next cycle.
        if rd_en:
            self._rd_pending = rd_addr

    # --- Testbench back-door (bypasses ports; zero latency, any length/align) ---
    def load(self, addr: int, data: bytes) -> None:
        data = bytes(data)
        self.mem[addr : addr + len(data)] = np.frombuffer(data, dtype=np.uint8)

    def dump(self, addr: int, n: int) -> bytes:
        return bytes(self.mem[addr : addr + n])
