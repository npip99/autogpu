"""
store — drain: compute_array → gmem. Synchronous in v1 (cmdproc blocks).

PURPOSE
    Executes the STORE instruction. Asks compute_array to drain a slot
    (MMA_M × MMA_N fp32) row-by-row, optionally converts to fp8 e4m3,
    writes to gmem in row-major order.

SYNC MODEL
    STORE is synchronous in v1: cmdproc PULSES `issue_en` for ONE cycle on
    dispatch, then waits (in WAITING_FOR_STORE_DONE state) for `done`. No
    input FIFO. cmdproc cannot issue another instruction until STORE
    completes. (This is fine since real workloads do MMA+LOAD overlap;
    STORE is a once-per-output-tile epilogue.)

    PULSE, NOT HOLD: STORE only accepts `issue_en` when not busy (see
    BEHAVIOR). Pymodel happens to work either way because Python tick order
    masks the race. In RTL, holding causes a re-fire: store.done is registered,
    cmdproc observes it 1 cycle late, and during that gap STORE has already
    returned to IDLE — a held issue_en would trigger a second STORE op.
    Confirmed by cmdproc.sv agent during Phase 4.

INTERFACE (Phase 7h-3 — drain-stream)
    STORE no longer reads a 32k-bit tile from TMEM in one shot. Instead:
      - drives `drain_issue` + `drain_slot` to compute_array on issue,
      - receives one row (MMA_N fp32 words) per cycle on
        `drain_row_valid` / `drain_row_data` / `drain_row_idx`, with
        `drain_last` marking the final row,
      - accumulates rows into an internal MMA_M × MMA_N fp32 buffer, then
        drains BEAT_BYTES at a time to GMEM.

    In this pymodel, we still access compute_array via back-door
    (compute_array.get_tile) for the gathered tile — the cycle-by-cycle
    interface is modeled on the SV side and exercised by the cocotb TB.
    Cycle latency is modeled by holding `busy` for the full duration:
    GATHER (~MMA_M+2) + FORMAT (1) + DRAIN (total_bytes/BEAT_BYTES).

INPUTS (sampled at tick start)
    issue_en      : 1-bit, held while waiting
    issue_cmd     : { tmem_slot, gmem_ptr, dtype (0=fp32, 1=fp8) }

OUTPUTS (registered)
    busy          : 1-bit
    done          : 1-bit, pulses one cycle on completion
    compute_array.drain_issue, drain_slot
    gmem.wr_en, wr_addr, wr_data (BEAT_BYTES bytes)

INTERNAL STATE
    busy          : 1-bit, init 0
    saved         : { tmem_slot, gmem_ptr, dtype }
    tile_bytes    : bytes — packed output (fp32 verbatim or fp8-encoded)
    bytes_written : counter

PIPELINE (cycle counts approximate the SV implementation)
    Cycle 0 (issue_en=1, busy=0):
        - latch saved
        - back-door read compute_array.get_tile(saved.tmem_slot) → tile
        - if dtype == 1: tile_bytes = fp8.encode_e4m3(tile.flatten())
          else:          tile_bytes = tile.astype('<f4').tobytes()
        - busy <= 1
    Cycles 1..K (K = ceil(total_bytes / BEAT_BYTES)):
        - gmem.wr_en = 1
        - gmem.wr_addr = saved.gmem_ptr + bytes_written
        - gmem.wr_data = tile_bytes[bytes_written : +BEAT_BYTES]
        - bytes_written += BEAT_BYTES
    Cycle K+1 (last write):
        - done <= 1
        - busy <= 0

OUTPUT LAYOUT
    Row-major in gmem: byte for (m, n) at gmem_ptr + (m*MMA_N + n) * elem_size,
    where elem_size = 1 (fp8) or 4 (fp32).

INVARIANTS
    - dtype in {0, 1}.
    - gmem_ptr is BEAT_BYTES-aligned.
    - Total bytes (M*N or M*N*4) must be a multiple of BEAT_BYTES (assert otherwise).

HANDSHAKE
    Cmdproc issues with issue_en=1 + operands, holds until done pulses.
    busy goes high cycle after issue; done pulses one cycle and busy returns to 0.

TEST CASES (pymodel/tests/test_store.py)
    1. store_fp32: backdoor-set a compute_array slot, STORE with dtype=0,
       gmem contents match flattened tile bytes.
    2. store_fp8: dtype=1, gmem contains fp8.encode_e4m3 of the tile.
    3. roundtrip: set slot to known tile, STORE fp8, decode_e4m3(gmem)
       approximates original tile (fp8 precision).
    4. multi_beat_correctness: tile big enough to need multiple BEAT_BYTES writes.
    5. busy_during_drain: busy=1 from issue to done.
"""

"""
Implementation NOTES (back-door simplification for pymodel):

STORE accesses compute_array's accumulator and GMEM via back-door
(compute_array.get_tile, gmem.load). Cycle latency is still modeled
as drain-gather + drain-to-gmem cycles. Synchronous: cmdproc holds
issue_en until done.
"""

import numpy as np

from config import BEAT_BYTES, MMA_M, MMA_N
from golden.fp8 import encode_e4m3


class Store:
    def __init__(self, compute_array, gmem):
        # compute_array is the new drain source (was tmem pre-7h-3).
        self.compute_array = compute_array
        self.gmem = gmem
        # Registered outputs
        self.busy: int = 0
        self.done: int = 0
        # Internal state
        self._cyc: int = 0
        self._saved: dict | None = None
        self._tile_bytes: bytes = b""

    def tick(
        self,
        *,
        issue_en: int = 0,
        tmem_slot: int = 0,
        gmem_ptr: int = 0,
        dtype: int = 0,  # 0 = fp32, 1 = fp8
    ) -> None:
        self.done = 0

        if not self.busy:
            if issue_en:
                assert dtype in (0, 1), "dtype must be 0 (fp32) or 1 (fp8)"
                assert gmem_ptr % BEAT_BYTES == 0, "gmem_ptr not BEAT_BYTES-aligned"
                tile = self.compute_array.get_tile(tmem_slot)
                if dtype == 1:
                    self._tile_bytes = bytes(np.ascontiguousarray(encode_e4m3(tile)).reshape(-1))
                else:
                    self._tile_bytes = bytes(np.ascontiguousarray(tile.astype("<f4")).tobytes())
                assert len(self._tile_bytes) % BEAT_BYTES == 0, "tile bytes not BEAT_BYTES-multiple"
                self._saved = {"gmem_ptr": gmem_ptr}
                self._cyc = 0
                self.busy = 1
            return

        # busy: drain self._tile_bytes one BEAT_BYTES at a time.
        off = self._cyc * BEAT_BYTES
        if off < len(self._tile_bytes):
            chunk = self._tile_bytes[off : off + BEAT_BYTES]
            self.gmem.load(self._saved["gmem_ptr"] + off, chunk)
            self._cyc += 1
            if self._cyc * BEAT_BYTES >= len(self._tile_bytes):
                # Last beat — pulse done.
                self.done = 1
                self.busy = 0
                self._cyc = 0
                self._tile_bytes = b""
