"""
compute_array — MMA_M x MMA_N systolic grid of MacTmemCell.

PURPOSE (Phase 7i-4 refactor)
    Replaces the broadcast network with a systolic mesh. Wraps 1024
    MacTmemCell leaves (Phase 7i-1) with:
      - A triangular row-skew buffer at the WEST edge: row i's a-byte is
        delayed by i cycles, so a[i, k] enters cell (i, 0) at cycle k+i.
      - A triangular col-skew buffer at the NORTH edge: col j's b-byte is
        delayed by j cycles, so b[k, j] enters cell (0, j) at cycle k+j.
      - The (compute, slot, accum) packet rides the row-skew with a.
      - A K-loop sequencer that issues SMEM rd_a / rd_b and PUSHES into the
        skew buffers each cycle a valid K-stripe arrives. Same cross-stall
        protocol as the broadcast version.
      - A wave-drain counter: after the K-th push, wait MMA_M + MMA_N - 2
        more cycles for the last K-element's wave to reach cell (M-1, N-1).
      - A row-by-row drain mux: identical to the broadcast version.

    External interface (mma_issue / mma_done / drain_* / SMEM rd_*) is
    UNCHANGED from the broadcast era — chip_top and tests need no edits.

CYCLE TIMING DIFF vs broadcast version
    Old: mma_done pulses 1 cycle after the last accumulate_now.
    New: mma_done pulses MMA_M + MMA_N - 1 cycles after the last
         push (= 1 + WAVE_DRAIN_CYCLES). For M=N=32 that's +62 cycles.

INPUTS / OUTPUTS / INVARIANTS: see the SV header for the canonical spec.

INTERNAL STATE (additions vs broadcast)
    skew_a[i] : list of (byte, valid, slot, accum) of length i  (per row)
    skew_b[j] : list of (byte, valid)                 of length j  (per col)
    wave_cnt  : countdown after K pushes
    state     : enum {IDLE, COMPUTE, WAVE_DRAIN}
"""

from enum import IntEnum

import numpy as np

from config import MMA_K, MMA_M, MMA_N, TMEM_SLOTS
from pymodel.mac_tmem_cell import MacTmemCell


_ZERO_A_BYTES = bytes(MMA_M)
_ZERO_B_BYTES = bytes(MMA_N)


class _MMAState(IntEnum):
    IDLE = 0
    COMPUTE = 1
    WAVE_DRAIN = 2


class _DrainState(IntEnum):
    IDLE = 0
    ISSUE = 1
    DRAIN_LAST = 2


class ComputeArray:
    def __init__(
        self,
        mma_m: int = MMA_M,
        mma_n: int = MMA_N,
        mma_k: int = MMA_K,
        n_slots: int = TMEM_SLOTS,
    ):
        self.mma_m = mma_m
        self.mma_n = mma_n
        self.mma_k = mma_k
        self.n_slots = n_slots
        self._wave_drain_cycles = mma_m + mma_n - 2

        self.cells: list[list[MacTmemCell]] = [
            [MacTmemCell(n_slots=n_slots) for _ in range(mma_n)]
            for _ in range(mma_m)
        ]

        # ---- Registered outputs (mma side) ----
        self.mma_busy: int = 0
        self.mma_done: int = 0
        self.arrive_en: int = 0
        self.arrive_bar_id: int = 0
        self.rd_a_en: int = 0
        self.rd_a_addr: int = 0
        self.rd_b_en: int = 0
        self.rd_b_addr: int = 0

        # ---- Registered outputs (drain side) ----
        self.drain_busy: int = 0
        self.drain_done: int = 0
        self.drain_row_valid: int = 0
        self.drain_row_data: int = 0
        self.drain_row_idx: int = 0
        self.drain_last: int = 0

        # ---- K-loop FSM ----
        self._state: _MMAState = _MMAState.IDLE
        self._saved: dict | None = None
        self._pa_valid: int = 0
        self._pa_data: bytes = _ZERO_A_BYTES
        self._pb_valid: int = 0
        self._pb_data: bytes = _ZERO_B_BYTES
        self._a_inflight: int = 0
        self._b_inflight: int = 0
        self._cur_collect_k: int = 0
        self._accum_done: int = 0
        self._pending_done: int = 0
        self._wave_cnt: int = 0

        # ---- Skew buffers ----
        # skew_a[i] is a list of dicts (head = index 0) of length i (rows >= 1).
        # Each entry: {byte, valid, slot, accum}.  Row 0 reads directly from
        # the push payload (no skew needed).
        self._skew_a: list[list[dict]] = [
            [{"byte": 0, "valid": 0, "slot": 0, "accum": 0} for _ in range(i)]
            for i in range(mma_m)
        ]
        # skew_b[j] is a list of dicts of length j (cols >= 1).
        # Each entry: {byte, valid}.  Col 0 reads directly from push.
        self._skew_b: list[list[dict]] = [
            [{"byte": 0, "valid": 0} for _ in range(j)]
            for j in range(mma_n)
        ]

        # ---- Drain FSM ----
        self._drain_state: _DrainState = _DrainState.IDLE
        self._drain_saved_slot: int = 0
        self._drain_next_row: int = 0
        self._s1_valid: int = 0
        self._s1_row: int = 0
        self._s1_last: int = 0
        self._s2_valid: int = 0
        self._s2_row: int = 0
        self._s2_last: int = 0

    # ------------------------------------------------------------------
    # tick
    # ------------------------------------------------------------------
    def tick(
        self,
        *,
        mma_issue: int = 0,
        mma_slot: int = 0,
        mma_accum: int = 0,
        mma_bar_id: int = 0,
        issue_a_off: int = 0,
        issue_b_off: int = 0,
        issue_a_stride: int = 0,
        issue_b_stride: int = 0,
        rd_a_data: bytes = _ZERO_A_BYTES,
        rd_a_valid: int = 0,
        rd_a_stall_in: int = 0,
        rd_b_data: bytes = _ZERO_B_BYTES,
        rd_b_valid: int = 0,
        rd_b_stall_in: int = 0,
        drain_issue: int = 0,
        drain_slot: int = 0,
        scrub_en: int = 0,
    ) -> None:
        # ---- Sample-phase asserts ----
        if mma_issue:
            assert not self.mma_busy, "compute_array: mma_issue while busy"
            assert 0 <= mma_slot < self.n_slots, f"mma_slot {mma_slot} OOR"
        if drain_issue:
            assert not self.drain_busy, (
                "compute_array: drain_issue while drain_busy"
            )
            assert 0 <= drain_slot < self.n_slots, f"drain_slot {drain_slot} OOR"
        if scrub_en:
            assert not mma_issue, "scrub_en concurrent with mma_issue"
            assert not drain_issue, "scrub_en concurrent with drain_issue"

        prev_pending_done = self._pending_done

        next_rd_a_en = 0
        next_rd_a_addr = 0
        next_rd_b_en = 0
        next_rd_b_addr = 0

        # Push payload computed this tick by the K-loop FSM.
        push_now = 0
        push_a_bytes: bytes = _ZERO_A_BYTES
        push_b_bytes: bytes = _ZERO_B_BYTES
        push_slot = 0
        push_accum = 0

        # ---- K-loop FSM ----
        if self._state == _MMAState.IDLE:
            if mma_issue:
                self._saved = {
                    "a_off": int(issue_a_off),
                    "b_off": int(issue_b_off),
                    "a_stride": int(issue_a_stride),
                    "b_stride": int(issue_b_stride),
                    "slot": int(mma_slot),
                    "accum": int(mma_accum),
                    "bar_id": int(mma_bar_id),
                }
                self._state = _MMAState.COMPUTE
                self.mma_busy = 1
                self._pa_valid = 0
                self._pa_data = _ZERO_A_BYTES
                self._pb_valid = 0
                self._pb_data = _ZERO_B_BYTES
                self._a_inflight = 0
                self._b_inflight = 0
                self._cur_collect_k = 0
                self._accum_done = 0
                next_rd_a_en = 1
                next_rd_a_addr = self._saved["a_off"]
                next_rd_b_en = 1
                next_rd_b_addr = self._saved["b_off"]

        elif self._state == _MMAState.COMPUTE:
            a_arrives = int(bool(rd_a_valid))
            b_arrives = int(bool(rd_b_valid))
            next_pa = self._pa_valid | a_arrives
            next_pb = self._pb_valid | b_arrives
            accumulate_now = next_pa & next_pb

            a_data_now = self._pa_data if self._pa_valid else bytes(rd_a_data)
            b_data_now = self._pb_data if self._pb_valid else bytes(rd_b_data)

            a_just_success = self.rd_a_en and not rd_a_stall_in
            b_just_success = self.rd_b_en and not rd_b_stall_in
            a_inflight_after = (self._a_inflight and not a_arrives) | a_just_success
            b_inflight_after = (self._b_inflight and not b_arrives) | b_just_success

            next_collect_k = self._cur_collect_k + (1 if accumulate_now else 0)
            pa_after = 0 if accumulate_now else next_pa
            pb_after = 0 if accumulate_now else next_pb
            a_inflight_after2 = 0 if accumulate_now else a_inflight_after
            b_inflight_after2 = 0 if accumulate_now else b_inflight_after

            if next_collect_k < self.mma_k:
                if not pa_after and not a_inflight_after2:
                    next_rd_a_en = 1
                    next_rd_a_addr = (
                        self._saved["a_off"]
                        + next_collect_k * self._saved["a_stride"]
                    )
                if not pb_after and not b_inflight_after2:
                    next_rd_b_en = 1
                    next_rd_b_addr = (
                        self._saved["b_off"]
                        + next_collect_k * self._saved["b_stride"]
                    )

            if accumulate_now:
                self._pa_valid = 0
                self._pa_data = _ZERO_A_BYTES
                self._pb_valid = 0
                self._pb_data = _ZERO_B_BYTES
                self._a_inflight = 0
                self._b_inflight = 0
                push_now = 1
                push_a_bytes = a_data_now
                push_b_bytes = b_data_now
                push_slot = self._saved["slot"]
                push_accum = 1 if self._accum_done else self._saved["accum"]
                self._accum_done = 1
                self._cur_collect_k = next_collect_k
                if self._cur_collect_k == self.mma_k:
                    # Last K-element just pushed. Transition to WAVE_DRAIN.
                    self._state = _MMAState.WAVE_DRAIN
                    self._wave_cnt = self._wave_drain_cycles
            else:
                if a_arrives and not self._pa_valid:
                    self._pa_valid = 1
                    self._pa_data = bytes(rd_a_data)
                if b_arrives and not self._pb_valid:
                    self._pb_valid = 1
                    self._pb_data = bytes(rd_b_data)
                self._a_inflight = a_inflight_after
                self._b_inflight = b_inflight_after

        elif self._state == _MMAState.WAVE_DRAIN:
            if self._wave_cnt == 0:
                self._pending_done = 1
                self._state = _MMAState.IDLE
            else:
                self._wave_cnt -= 1

        # ---- Compute systolic edge inputs from CURRENT skew state +
        #      push payload ----
        # Row 0 / col 0 read fresh push directly. Rows/cols >= 1 read
        # from the deepest position of their respective skew queue
        # (which holds the byte pushed i cycles ago for row i).
        edge_a = [0] * self.mma_m
        edge_compute = [0] * self.mma_m
        edge_slot = [0] * self.mma_m
        edge_accum = [0] * self.mma_m
        edge_b = [0] * self.mma_n

        if push_now:
            edge_a[0] = push_a_bytes[0]
            edge_compute[0] = 1
            edge_slot[0] = push_slot
            edge_accum[0] = push_accum
            edge_b[0] = push_b_bytes[0]
        for i in range(1, self.mma_m):
            tail = self._skew_a[i][-1]
            edge_a[i] = tail["byte"]
            edge_compute[i] = tail["valid"]
            edge_slot[i] = tail["slot"]
            edge_accum[i] = tail["accum"]
        for j in range(1, self.mma_n):
            tail = self._skew_b[j][-1]
            edge_b[j] = tail["byte"]

        # ---- Drain FSM combinational outputs ----
        cell_drain_en_row: int | None = None
        cell_drain_slot = 0
        drain_issue_now = 0
        if self._drain_state == _DrainState.ISSUE:
            if self._drain_next_row < self.mma_m:
                cell_drain_en_row = self._drain_next_row
                cell_drain_slot = self._drain_saved_slot
                drain_issue_now = 1

        # ---- Snapshot all cells' _out values BEFORE ticking ----
        # Each cell's _in for THIS tick depends on its west/north neighbor's
        # _out as of the START of this tick.
        prev_a_out      = [[c.a_out      for c in row] for row in self.cells]
        prev_b_out      = [[c.b_out      for c in row] for row in self.cells]
        prev_compute_out= [[c.compute_out for c in row] for row in self.cells]
        prev_slot_out   = [[c.slot_out   for c in row] for row in self.cells]
        prev_accum_out  = [[c.accum_out  for c in row] for row in self.cells]

        # ---- Tick each cell with its proper _in values ----
        for i in range(self.mma_m):
            for j in range(self.mma_n):
                a_in = edge_a[i]       if j == 0 else prev_a_out      [i][j-1]
                c_in = edge_compute[i] if j == 0 else prev_compute_out[i][j-1]
                s_in = edge_slot[i]    if j == 0 else prev_slot_out   [i][j-1]
                ac_in= edge_accum[i]   if j == 0 else prev_accum_out  [i][j-1]
                b_in = edge_b[j]       if i == 0 else prev_b_out      [i-1][j]
                de   = 1 if cell_drain_en_row == i else 0
                self.cells[i][j].tick(
                    compute_in=c_in,
                    a_in=a_in,
                    b_in=b_in,
                    slot_in=s_in,
                    accum_in=ac_in,
                    drain_en=de,
                    drain_slot=cell_drain_slot,
                    scrub_en=scrub_en,
                )

        # ---- Advance skew buffers (after cells consumed this cycle's
        #      head/tail values) ----
        for i in range(1, self.mma_m):
            # Shift down: position k+1 gets position k, head gets push.
            for k in range(len(self._skew_a[i]) - 1, 0, -1):
                self._skew_a[i][k] = self._skew_a[i][k-1]
            self._skew_a[i][0] = (
                {"byte": push_a_bytes[i], "valid": 1,
                 "slot": push_slot, "accum": push_accum}
                if push_now
                else {"byte": 0, "valid": 0, "slot": 0, "accum": 0}
            )
        for j in range(1, self.mma_n):
            for k in range(len(self._skew_b[j]) - 1, 0, -1):
                self._skew_b[j][k] = self._skew_b[j][k-1]
            self._skew_b[j][0] = (
                {"byte": push_b_bytes[j], "valid": 1}
                if push_now
                else {"byte": 0, "valid": 0}
            )

        # ---- Drain pipeline + FSM (identical to broadcast version) ----
        entering_s1_valid = self._s1_valid
        entering_s1_row = self._s1_row
        entering_s1_last = self._s1_last
        entering_s2_valid = self._s2_valid
        entering_s2_last = self._s2_last

        if drain_issue_now:
            next_s1_valid = 1
            next_s1_row = cell_drain_en_row
            next_s1_last = 1 if cell_drain_en_row == self.mma_m - 1 else 0
        else:
            next_s1_valid = 0
            next_s1_row = 0
            next_s1_last = 0

        next_s2_valid = entering_s1_valid
        next_s2_row = entering_s1_row
        next_s2_last = entering_s1_last

        next_drain_state = self._drain_state
        next_drain_busy = self.drain_busy
        next_drain_next_row = self._drain_next_row
        next_drain_saved_slot = self._drain_saved_slot

        if self._drain_state == _DrainState.IDLE:
            if drain_issue:
                next_drain_state = _DrainState.ISSUE
                next_drain_busy = 1
                next_drain_saved_slot = int(drain_slot)
                next_drain_next_row = 0
                next_s1_valid = 0
                next_s2_valid = 0
        elif self._drain_state == _DrainState.ISSUE:
            if drain_issue_now:
                next_drain_next_row = self._drain_next_row + 1
            if (
                self._drain_next_row >= self.mma_m
                and not drain_issue_now
                and not entering_s1_valid
                and not entering_s2_valid
            ):
                next_drain_state = _DrainState.DRAIN_LAST
        elif self._drain_state == _DrainState.DRAIN_LAST:
            next_drain_state = _DrainState.IDLE
            next_drain_busy = 0

        self._s1_valid = next_s1_valid
        self._s1_row = next_s1_row
        self._s1_last = next_s1_last
        self._s2_valid = next_s2_valid
        self._s2_row = next_s2_row
        self._s2_last = next_s2_last
        self._drain_state = next_drain_state
        self.drain_busy = next_drain_busy
        self._drain_next_row = next_drain_next_row
        self._drain_saved_slot = next_drain_saved_slot

        next_drain_row_valid = 0
        next_drain_row_data = 0
        next_drain_row_idx = 0
        next_drain_last = 0

        if self._s2_valid:
            row_idx = self._s2_row
            packed = 0
            for j in range(self.mma_n):
                word = self.cells[row_idx][j].drain_data & 0xFFFFFFFF
                packed |= word << (j * 32)
            next_drain_row_valid = 1
            next_drain_row_data = packed
            next_drain_row_idx = row_idx
            if self._s2_last:
                next_drain_last = 1

        next_drain_done = 1 if (entering_s2_valid and entering_s2_last) else 0

        next_mma_done = 0
        next_arrive_en = 0
        next_arrive_bar_id = 0
        if prev_pending_done:
            next_mma_done = 1
            next_arrive_en = 1
            next_arrive_bar_id = self._saved["bar_id"] if self._saved else 0
            self._pending_done = 0
            self.mma_busy = 0
            self._saved = None
            self._accum_done = 0

        self.mma_done = next_mma_done
        self.arrive_en = next_arrive_en
        self.arrive_bar_id = next_arrive_bar_id
        self.rd_a_en = next_rd_a_en
        self.rd_a_addr = next_rd_a_addr
        self.rd_b_en = next_rd_b_en
        self.rd_b_addr = next_rd_b_addr

        self.drain_row_valid = next_drain_row_valid
        self.drain_row_data = next_drain_row_data
        self.drain_row_idx = next_drain_row_idx
        self.drain_last = next_drain_last
        self.drain_done = next_drain_done

    # ------------------------------------------------------------------
    # Backdoor helpers
    # ------------------------------------------------------------------
    def get_tile(self, slot: int) -> np.ndarray:
        out = np.zeros((self.mma_m, self.mma_n), dtype=np.float32)
        for i in range(self.mma_m):
            for j in range(self.mma_n):
                out[i, j] = float(self.cells[i][j].storage[slot])
        return out

    def set_tile(self, slot: int, tile: np.ndarray) -> None:
        assert tile.shape == (self.mma_m, self.mma_n)
        for i in range(self.mma_m):
            for j in range(self.mma_n):
                self.cells[i][j].storage[slot] = np.float32(tile[i, j])
