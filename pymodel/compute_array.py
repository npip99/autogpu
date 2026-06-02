"""
compute_array — MMA_M x MMA_N systolic grid of MacTmemCell with cell-level drain.

PURPOSE (Phase 7i-6)
    The cleanest 2-macro hierarchy. cmd_unit emits a single-cycle broadcast
    drain_en pulse; every cell injects storage[drain_slot] into its drain_out
    register, then on subsequent cycles drain_out forwards drain_in from the
    south neighbor. Values shift north one cell per cycle. The chip's
    drain_row_data is the concatenation of the top row's drain_out ports —
    no centralised drain mux exists.

External interface (mma_issue / mma_done / drain_* / SMEM rd_*) is unchanged
from the broadcast era. chip_top and tests need no edits.

CYCLE TIMING
    K-loop completion: same as Phase 7i-4 (K + M + N − 2 cycles total).
    Drain: drain_issue at cycle T -> drain_en pulse at T+1 -> drain_row_valid
           HIGH for M cycles starting T+2, idx 0..M-1. drain_done at T+M+2.
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
    PULSE = 1
    STREAM = 2
    DONE = 3


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

        # Registered outputs (mma side).
        self.mma_busy: int = 0
        self.mma_done: int = 0
        self.arrive_en: int = 0
        self.arrive_bar_id: int = 0
        self.rd_a_en: int = 0
        self.rd_a_addr: int = 0
        self.rd_b_en: int = 0
        self.rd_b_addr: int = 0

        # Registered outputs (drain side).
        self.drain_busy: int = 0
        self.drain_done: int = 0
        self.drain_row_valid: int = 0
        self.drain_row_data: int = 0
        self.drain_row_idx: int = 0
        self.drain_last: int = 0

        # K-loop FSM.
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

        # Skew buffers.
        self._skew_a: list[list[dict]] = [
            [{"byte": 0, "valid": 0, "slot": 0, "accum": 0} for _ in range(i)]
            for i in range(mma_m)
        ]
        self._skew_b: list[list[dict]] = [
            [{"byte": 0, "valid": 0} for _ in range(j)]
            for j in range(mma_n)
        ]

        # Drain FSM (cmd_unit-side).
        self._drain_state: _DrainState = _DrainState.IDLE
        self._drain_saved_slot: int = 0
        self._drain_count: int = 0
        # cmd_unit's drain_en output (registered).
        self._cells_drain_en: int = 0
        self._cells_drain_slot: int = 0

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

        # ---- Snapshot cells' broadcast inputs at the top of the tick.
        # cmd_unit's drain_en / drain_slot are registered, so the cell
        # mesh sees the previous tick's values during this tick.
        snap_cells_drain_en   = self._cells_drain_en
        snap_cells_drain_slot = self._cells_drain_slot

        prev_pending_done = self._pending_done

        next_rd_a_en = 0
        next_rd_a_addr = 0
        next_rd_b_en = 0
        next_rd_b_addr = 0

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

        # ---- Drain FSM (cmd_unit-side, mirrors SV) ----
        next_cells_drain_en = 0
        next_drain_row_valid = 0
        next_drain_row_idx = 0
        next_drain_last = 0
        next_drain_state = self._drain_state
        next_drain_busy = self.drain_busy
        next_drain_count = self._drain_count
        next_drain_saved_slot = self._drain_saved_slot
        next_drain_done = 0

        if self._drain_state == _DrainState.IDLE:
            if drain_issue:
                next_drain_saved_slot = int(drain_slot)
                next_drain_count = 0
                next_drain_state = _DrainState.PULSE
                next_drain_busy = 1
                next_cells_drain_en = 1
        elif self._drain_state == _DrainState.PULSE:
            next_drain_state = _DrainState.STREAM
            next_drain_count = 0
            next_drain_row_valid = 1
            next_drain_row_idx = 0
            next_drain_last = 1 if self.mma_m == 1 else 0
        elif self._drain_state == _DrainState.STREAM:
            if self._drain_count + 1 < self.mma_m:
                next_drain_count = self._drain_count + 1
                next_drain_row_valid = 1
                next_drain_row_idx = self._drain_count + 1
                next_drain_last = 1 if self._drain_count + 2 == self.mma_m else 0
            else:
                next_drain_state = _DrainState.DONE
        elif self._drain_state == _DrainState.DONE:
            next_drain_state = _DrainState.IDLE
            next_drain_busy = 0
            next_drain_done = 1

        # ---- Skew edge outputs ----
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

        # ---- Snapshot cells' _out values BEFORE ticking ----
        prev_a_out      = [[c.a_out      for c in row] for row in self.cells]
        prev_b_out      = [[c.b_out      for c in row] for row in self.cells]
        prev_compute_out= [[c.compute_out for c in row] for row in self.cells]
        prev_slot_out   = [[c.slot_out   for c in row] for row in self.cells]
        prev_accum_out  = [[c.accum_out  for c in row] for row in self.cells]
        prev_drain_out  = [[c.drain_out  for c in row] for row in self.cells]

        # ---- Tick each cell ----
        for i in range(self.mma_m):
            for j in range(self.mma_n):
                a_in = edge_a[i]       if j == 0 else prev_a_out      [i][j-1]
                c_in = edge_compute[i] if j == 0 else prev_compute_out[i][j-1]
                s_in = edge_slot[i]    if j == 0 else prev_slot_out   [i][j-1]
                ac_in= edge_accum[i]   if j == 0 else prev_accum_out  [i][j-1]
                b_in = edge_b[j]       if i == 0 else prev_b_out      [i-1][j]
                # drain_in: from south neighbor (i+1); 0 if at south edge.
                d_in = 0 if i == self.mma_m - 1 else prev_drain_out[i+1][j]
                self.cells[i][j].tick(
                    compute_in=c_in,
                    a_in=a_in,
                    b_in=b_in,
                    slot_in=s_in,
                    accum_in=ac_in,
                    drain_en=snap_cells_drain_en,
                    drain_slot=snap_cells_drain_slot,
                    drain_in=d_in,
                    scrub_en=scrub_en,
                )

        # ---- Advance skew buffers ----
        for i in range(1, self.mma_m):
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

        # ---- Commit drain FSM registers ----
        self._drain_state = next_drain_state
        self.drain_busy = next_drain_busy
        self._drain_count = next_drain_count
        self._drain_saved_slot = next_drain_saved_slot
        self._cells_drain_en = next_cells_drain_en
        self._cells_drain_slot = self._drain_saved_slot
        self.drain_row_valid = next_drain_row_valid
        self.drain_row_idx = next_drain_row_idx
        self.drain_last = next_drain_last
        self.drain_done = next_drain_done

        # ---- mma_done / arrive_en (one cycle after pending_done) ----
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

        # ---- Commit registered outputs ----
        self.mma_done = next_mma_done
        self.arrive_en = next_arrive_en
        self.arrive_bar_id = next_arrive_bar_id
        self.rd_a_en = next_rd_a_en
        self.rd_a_addr = next_rd_a_addr
        self.rd_b_en = next_rd_b_en
        self.rd_b_addr = next_rd_b_addr

        # ---- Pack chip drain_row_data from top row cells' drain_out ----
        # Gated by drain_row_valid (R5 invariant: receiver gates writes on
        # valid, so the data lines are don't-care when valid=0).
        if self.drain_row_valid:
            packed = 0
            for j in range(self.mma_n):
                word = self.cells[0][j].drain_out & 0xFFFFFFFF
                packed |= word << (j * 32)
            self.drain_row_data = packed
        else:
            self.drain_row_data = 0

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
