"""
store — drain: tmem → gmem. Synchronous in v1 (cmdproc blocks).

PURPOSE
    Executes the STORE instruction. Reads a TMEM slot (MMA_M × MMA_N fp32),
    optionally converts to fp8 e4m3, writes to gmem in row-major order.

SYNC MODEL
    STORE is synchronous in v1: cmdproc holds `issue_en` high and waits for
    `done`. No input FIFO. cmdproc cannot issue another instruction until
    STORE completes. (This is fine since real workloads do MMA+LOAD overlap;
    STORE is a once-per-output-tile epilogue.)

INPUTS (sampled at tick start)
    issue_en      : 1-bit, held while waiting
    issue_cmd     : { tmem_slot, gmem_ptr, dtype (0=fp32, 1=fp8) }
    tmem.STORE_RD.rd_tile (MMA_M × MMA_N fp32, registered)
    tmem.STORE_RD.rd_valid

OUTPUTS (registered)
    busy          : 1-bit
    done          : 1-bit, pulses one cycle on completion
    tmem.STORE_RD.rd_en, slot
    gmem.wr_en, wr_addr, wr_data (BEAT_BYTES bytes)

INTERNAL STATE
    busy          : 1-bit, init 0
    saved         : { tmem_slot, gmem_ptr, dtype }
    tile_buf      : MMA_M × MMA_N fp32, captured after tmem read
    bytes_written : counter

PIPELINE
    Cycle 0 (issue_en=1, busy=0):
        - latch saved
        - issue tmem read of saved.tmem_slot
        - busy <= 1
    Cycle 1 (tmem.rd_valid):
        - tile_buf <= tmem.rd_tile
        - if dtype == 1: tile_bytes = fp8.encode_e4m3(tile_buf.flatten()) — M*N bytes
          else:          tile_bytes = tile_buf.astype('<f4').tobytes()    — M*N*4 bytes
        - bytes_written = 0
    Cycles 2..K (K = ceil(total_bytes / BEAT_BYTES)):
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
    - TMEM tile packing (in RTL): consume tiles per the convention in
      pymodel/tmem.py §"RTL TILE PACKING CONVENTION". Element [i][j] at bit
      (i*MMA_N+j)*32, IEEE 754 fp32 LSB-first.

HANDSHAKE
    Cmdproc issues with issue_en=1 + operands, holds until done pulses.
    busy goes high cycle after issue; done pulses one cycle and busy returns to 0.

TEST CASES (pymodel/tests/test_store.py)
    1. store_fp32: backdoor-set a tmem slot, STORE with dtype=0, gmem contents match flattened tile bytes.
    2. store_fp8: dtype=1, gmem contains fp8.encode_e4m3 of the tile.
    3. roundtrip: set slot to known tile, STORE fp8, decode_e4m3(gmem) approximates original tile (fp8 precision).
    4. multi_beat_correctness: tile big enough to need multiple BEAT_BYTES writes.
    5. busy_during_drain: busy=1 from issue to done.
"""

"""
Implementation NOTES (back-door simplification for pymodel):

STORE accesses TMEM and GMEM via back-door (tmem.get_slot, gmem.load).
Cycle latency is still modeled as 1 (tmem read) + ceil(total_bytes / BEAT_BYTES)
beats + 1 (done) cycles. Synchronous: cmdproc holds issue_en until done.
"""

import numpy as np

from config import BEAT_BYTES, MMA_M, MMA_N
from golden.fp8 import encode_e4m3


class Store:
    def __init__(self, tmem, gmem):
        self.tmem = tmem
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
                tile = self.tmem.get_slot(tmem_slot)
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
