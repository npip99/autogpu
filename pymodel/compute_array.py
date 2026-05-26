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


def _default_fwd_packet() -> dict:
    return {
        "push_now": 0,
        "push_a_bytes": _ZERO_A_BYTES,
        "push_b_bytes": _ZERO_B_BYTES,
        "push_slot": 0,
        "push_accum": 0,
        "cells_drain_en": 0,
        "cells_drain_slot": 0,
        "scrub_en": 0,
    }


def _default_out_packet() -> dict:
    return {
        "mma_busy": 0,
        "mma_done": 0,
        "arrive_en": 0,
        "arrive_bar_id": 0,
        "drain_busy": 0,
        "drain_done": 0,
        "drain_row_valid": 0,
        "drain_row_idx": 0,
        "drain_last": 0,
    }


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
        bcast_pipe: int = 0,
    ):
        self.mma_m = mma_m
        self.mma_n = mma_n
        self.mma_k = mma_k
        self.n_slots = n_slots
        self.bcast_pipe = bcast_pipe
        self._wave_drain_cycles = mma_m + mma_n - 2

        self.cells: list[list[MacTmemCell]] = [
            [MacTmemCell(n_slots=n_slots) for _ in range(mma_n)]
            for _ in range(mma_m)
        ]

        # Registered outputs (mma side). With bcast_pipe>0 these reflect
        # the LAST stage of the output pipe (chip-external view).
        self.mma_busy: int = 0
        self.mma_done: int = 0
        self.arrive_en: int = 0
        self.arrive_bar_id: int = 0
        # rd_*_en/addr are NOT in the output pipe (SMEM lives at the
        # chip boundary, cmd_unit's FSM expects round-trip at the
        # natural clock-cycle latency).
        self.rd_a_en: int = 0
        self.rd_a_addr: int = 0
        self.rd_b_en: int = 0
        self.rd_b_addr: int = 0

        # Registered outputs (drain side, chip-external view).
        self.drain_busy: int = 0
        self.drain_done: int = 0
        self.drain_row_valid: int = 0
        self.drain_row_data: int = 0
        self.drain_row_idx: int = 0
        self.drain_last: int = 0

        # cmd_unit-internal completion registers. These are the inputs to
        # the output pipe; chip-external attributes above mirror them
        # delayed by bcast_pipe cycles.
        self._u_mma_busy: int = 0
        self._u_mma_done: int = 0
        self._u_arrive_en: int = 0
        self._u_arrive_bar_id: int = 0
        self._u_drain_busy: int = 0
        self._u_drain_done: int = 0
        self._u_drain_row_valid: int = 0
        self._u_drain_row_idx: int = 0
        self._u_drain_last: int = 0

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
        # cmd_unit's drain_en output (registered).  Drives the forward
        # pipe input each cycle.
        self._cells_drain_en: int = 0
        self._cells_drain_slot: int = 0

        # Forward pipe: bcast_pipe stages between cmd_unit's push/drain
        # outputs and the cells. Stage [0] is closest to cmd_unit; the
        # last stage drives the cells. Each entry is the full bag of
        # broadcast signals captured one cycle. When bcast_pipe == 0
        # the pipe is empty and cells see the live cmd_unit outputs.
        self._fwd_pipe: list[dict] = [
            _default_fwd_packet() for _ in range(bcast_pipe)
        ]
        # Output pipe: bcast_pipe stages on cmd_unit's chip-external
        # completion outputs. Same layout as the forward pipe.
        self._out_pipe: list[dict] = [
            _default_out_packet() for _ in range(bcast_pipe)
        ]

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
        # Asserts are against cmd_unit's INTERNAL view (_u_*): external
        # observers gate issue on the piped self.* attributes, which lag
        # the internal view by bcast_pipe cycles, so by the time external
        # mma_busy clears the internal value has already cleared.
        if mma_issue:
            assert not self._u_mma_busy, "compute_array: mma_issue while busy"
            assert 0 <= mma_slot < self.n_slots, f"mma_slot {mma_slot} OOR"
        if drain_issue:
            assert not self._u_drain_busy, (
                "compute_array: drain_issue while drain_busy"
            )
            assert 0 <= drain_slot < self.n_slots, f"drain_slot {drain_slot} OOR"
        if scrub_en:
            assert not mma_issue, "scrub_en concurrent with mma_issue"
            assert not drain_issue, "scrub_en concurrent with drain_issue"

        # ---- Snapshot cmd_unit-internal registered outputs at the top
        # of the tick. These represent "value during this cycle T" and
        # are what the SV output pipe captures at edge-T+1 (the in-flight
        # FSM updates within this tick are for cycle T+1).
        snap_u_mma_busy        = self._u_mma_busy
        snap_u_mma_done        = self._u_mma_done
        snap_u_arrive_en       = self._u_arrive_en
        snap_u_arrive_bar_id   = self._u_arrive_bar_id
        snap_u_drain_busy      = self._u_drain_busy
        snap_u_drain_done      = self._u_drain_done
        snap_u_drain_row_valid = self._u_drain_row_valid
        snap_u_drain_row_idx   = self._u_drain_row_idx
        snap_u_drain_last      = self._u_drain_last
        snap_u_cells_drain_en   = self._cells_drain_en
        snap_u_cells_drain_slot = self._cells_drain_slot

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
                self._u_mma_busy = 1
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
        # Output registers default to 0 each cycle; case arms can override.
        next_cells_drain_en = 0
        next_drain_row_valid = 0
        next_drain_row_idx = 0
        next_drain_last = 0
        next_drain_state = self._drain_state
        next_u_drain_busy = self._u_drain_busy
        next_drain_count = self._drain_count
        next_drain_saved_slot = self._drain_saved_slot
        next_drain_done = 0

        if self._drain_state == _DrainState.IDLE:
            if drain_issue:
                next_drain_saved_slot = int(drain_slot)
                next_drain_count = 0
                next_drain_state = _DrainState.PULSE
                next_u_drain_busy = 1
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
            next_u_drain_busy = 0
            next_drain_done = 1

        # ---- Forward pipe output: what cells/skew_lanes actually see
        # this cycle. With bcast_pipe=0 they see the live cmd_unit
        # outputs; with bcast_pipe=N they see the value cmd_unit emitted
        # N cycles ago (drain_en/slot are registered so use the top-of-
        # tick snap; push_* are combinational so use the value computed
        # this tick).
        if self.bcast_pipe > 0:
            fwd_out = self._fwd_pipe[-1]
            fwd_push_now    = fwd_out["push_now"]
            fwd_push_a      = fwd_out["push_a_bytes"]
            fwd_push_b      = fwd_out["push_b_bytes"]
            fwd_push_slot   = fwd_out["push_slot"]
            fwd_push_accum  = fwd_out["push_accum"]
            fwd_cell_de     = fwd_out["cells_drain_en"]
            fwd_cell_ds     = fwd_out["cells_drain_slot"]
            fwd_scrub_en    = fwd_out["scrub_en"]
        else:
            fwd_push_now    = push_now
            fwd_push_a      = push_a_bytes
            fwd_push_b      = push_b_bytes
            fwd_push_slot   = push_slot
            fwd_push_accum  = push_accum
            fwd_cell_de     = snap_u_cells_drain_en
            fwd_cell_ds     = snap_u_cells_drain_slot
            fwd_scrub_en    = scrub_en

        # ---- Skew edge outputs (consume forward-pipe push values) ----
        edge_a = [0] * self.mma_m
        edge_compute = [0] * self.mma_m
        edge_slot = [0] * self.mma_m
        edge_accum = [0] * self.mma_m
        edge_b = [0] * self.mma_n

        if fwd_push_now:
            edge_a[0] = fwd_push_a[0]
            edge_compute[0] = 1
            edge_slot[0] = fwd_push_slot
            edge_accum[0] = fwd_push_accum
            edge_b[0] = fwd_push_b[0]
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

        # ---- Tick each cell using forward-pipe outputs ----
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
                    drain_en=fwd_cell_de,
                    drain_slot=fwd_cell_ds,
                    drain_in=d_in,
                    scrub_en=fwd_scrub_en,
                )

        # ---- Advance skew buffers (consume forward-pipe push values) ----
        for i in range(1, self.mma_m):
            for k in range(len(self._skew_a[i]) - 1, 0, -1):
                self._skew_a[i][k] = self._skew_a[i][k-1]
            self._skew_a[i][0] = (
                {"byte": fwd_push_a[i], "valid": 1,
                 "slot": fwd_push_slot, "accum": fwd_push_accum}
                if fwd_push_now
                else {"byte": 0, "valid": 0, "slot": 0, "accum": 0}
            )
        for j in range(1, self.mma_n):
            for k in range(len(self._skew_b[j]) - 1, 0, -1):
                self._skew_b[j][k] = self._skew_b[j][k-1]
            self._skew_b[j][0] = (
                {"byte": fwd_push_b[j], "valid": 1}
                if fwd_push_now
                else {"byte": 0, "valid": 0}
            )

        # ---- Commit drain FSM registers (cmd_unit-internal view) ----
        self._drain_state = next_drain_state
        self._u_drain_busy = next_u_drain_busy
        self._drain_count = next_drain_count
        self._drain_saved_slot = next_drain_saved_slot
        self._cells_drain_en = next_cells_drain_en
        self._cells_drain_slot = self._drain_saved_slot
        self._u_drain_row_valid = next_drain_row_valid
        self._u_drain_row_idx = next_drain_row_idx
        self._u_drain_last = next_drain_last
        self._u_drain_done = next_drain_done

        # ---- mma_done / arrive_en (one cycle after pending_done) ----
        next_mma_done = 0
        next_arrive_en = 0
        next_arrive_bar_id = 0
        if prev_pending_done:
            next_mma_done = 1
            next_arrive_en = 1
            next_arrive_bar_id = self._saved["bar_id"] if self._saved else 0
            self._pending_done = 0
            self._u_mma_busy = 0
            self._saved = None
            self._accum_done = 0

        # ---- Commit cmd_unit-internal registered outputs ----
        self._u_mma_done = next_mma_done
        self._u_arrive_en = next_arrive_en
        self._u_arrive_bar_id = next_arrive_bar_id
        # rd_* are not piped; they go straight to the chip output.
        self.rd_a_en = next_rd_a_en
        self.rd_a_addr = next_rd_a_addr
        self.rd_b_en = next_rd_b_en
        self.rd_b_addr = next_rd_b_addr

        # ---- Shift forward pipe (cmd_unit -> cells path) ----
        # SV edge captures cycle-T push (combinational) and the
        # registered cells_drain_en/slot (snap from top of tick).
        if self.bcast_pipe > 0:
            fwd_in = {
                "push_now":         push_now,
                "push_a_bytes":     push_a_bytes,
                "push_b_bytes":     push_b_bytes,
                "push_slot":        push_slot,
                "push_accum":       push_accum,
                "cells_drain_en":   snap_u_cells_drain_en,
                "cells_drain_slot": snap_u_cells_drain_slot,
                "scrub_en":         scrub_en,
            }
            self._fwd_pipe = [fwd_in] + self._fwd_pipe[:-1]

        # ---- Shift output pipe (cmd_unit -> chip path) ----
        # SV edge captures the cmd_unit-internal completion regs as they
        # were during cycle T (the top-of-tick snap). The chip-external
        # self.* attributes mirror the LAST stage after the shift.
        if self.bcast_pipe > 0:
            out_in = {
                "mma_busy":        snap_u_mma_busy,
                "mma_done":        snap_u_mma_done,
                "arrive_en":       snap_u_arrive_en,
                "arrive_bar_id":   snap_u_arrive_bar_id,
                "drain_busy":      snap_u_drain_busy,
                "drain_done":      snap_u_drain_done,
                "drain_row_valid": snap_u_drain_row_valid,
                "drain_row_idx":   snap_u_drain_row_idx,
                "drain_last":      snap_u_drain_last,
            }
            self._out_pipe = [out_in] + self._out_pipe[:-1]
            ext = self._out_pipe[-1]
            self.mma_busy        = ext["mma_busy"]
            self.mma_done        = ext["mma_done"]
            self.arrive_en       = ext["arrive_en"]
            self.arrive_bar_id   = ext["arrive_bar_id"]
            self.drain_busy      = ext["drain_busy"]
            self.drain_done      = ext["drain_done"]
            self.drain_row_valid = ext["drain_row_valid"]
            self.drain_row_idx   = ext["drain_row_idx"]
            self.drain_last      = ext["drain_last"]
        else:
            self.mma_busy        = self._u_mma_busy
            self.mma_done        = self._u_mma_done
            self.arrive_en       = self._u_arrive_en
            self.arrive_bar_id   = self._u_arrive_bar_id
            self.drain_busy      = self._u_drain_busy
            self.drain_done      = self._u_drain_done
            self.drain_row_valid = self._u_drain_row_valid
            self.drain_row_idx   = self._u_drain_row_idx
            self.drain_last      = self._u_drain_last

        # ---- Pack chip drain_row_data from top row cells' drain_out ----
        # Gated by chip-external drain_row_valid: with bcast_pipe>0 both
        # the valid signal and the cells' drain_out are delayed by N
        # cycles relative to cmd_unit, so they align by construction.
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
