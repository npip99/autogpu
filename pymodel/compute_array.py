"""
compute_array — MMA_M x MMA_N grid of MacTmemCell with K-loop sequencer and drain mux.

PURPOSE
    Phase 7h-2 integration target. Wraps 1024 MacTmemCell leaves (Phase 7h-1)
    with:
      - a broadcast network: one A column (MMA_M fp8 bytes) + one B row
        (MMA_N fp8 bytes) per K cycle, fanned out to all (i, j) cells;
      - a K-loop sequencer that issues SMEM reads for A and B operand
        rows and drives `compute` on the cells for MMA_K cycles;
      - a row-by-row drain mux that streams an MMA_M×MMA_N tile out one
        row at a time after a `drain_issue`.

    This is the integration logic that used to be split between mma.sv
    (K-loop, SMEM addressing) and tmem.sv (slot storage). chip_top (7h-3)
    will swap out the old monolithic mma + tmem pair for this module +
    a thin shim adapter for STORE.

INPUTS (sampled at tick start)
    # Issue from cmdproc — new matmul.
    mma_issue       : 1-bit
    mma_slot        : log2(N_SLOTS) — destination accumulator slot
    mma_accum       : 1-bit — 1: storage[slot] += AB; 0: storage[slot] = AB
    mma_bar_id      : 32-bit — barrier id to arrive on completion
    issue_a_off     : 32-bit — SMEM byte address of A's first column
    issue_b_off     : 32-bit — SMEM byte address of B's first row
    issue_a_stride  : 32-bit — bytes per A column (typically MMA_M)
    issue_b_stride  : 32-bit — bytes per B row    (typically MMA_N)

    # SMEM read response (1-cycle latency, may stall).
    rd_a_data       : MMA_M bytes — A column for the issued read
    rd_a_valid      : 1-bit
    rd_a_stall_in   : 1-bit — combinational stall: re-issue this cycle's request
    rd_b_data       : MMA_N bytes
    rd_b_valid      : 1-bit
    rd_b_stall_in   : 1-bit

    # Issue from cmdproc — drain a slot to STORE.
    drain_issue     : 1-bit
    drain_slot      : log2(N_SLOTS)

    # Scrub from reset_seq.
    scrub_en        : 1-bit

OUTPUTS (registered)
    mma_busy        : 1-bit
    mma_done        : 1-bit — pulses on K-loop completion
    arrive_en       : 1-bit — pulses with mma_done
    arrive_bar_id   : 32-bit

    rd_a_en         : 1-bit
    rd_a_addr       : 32-bit
    rd_b_en         : 1-bit
    rd_b_addr       : 32-bit

    drain_busy      : 1-bit
    drain_done      : 1-bit — pulses on drain completion (last row out)
    drain_row_valid : 1-bit
    drain_row_data  : MMA_N * 32 bits — row of fp32 words (LSB-first per word)
    drain_row_idx   : log2(MMA_M) — row index for drain_row_data
    drain_last      : 1-bit — pulses with drain_row_valid on the final row

INTERNAL STATE
    cells           : list[list[MacTmemCell]]  (MMA_M × MMA_N)

    # K-loop
    state           : enum {IDLE, COMPUTE}
    saved           : { a_off, b_off, a_stride, b_stride, slot, accum, bar_id }
    pa_valid, pa_data, pb_valid, pb_data : pending-arrival stash mirroring
                       mma.sv's cross-stall protocol
    a_inflight, b_inflight    : whether a read driven last cycle is still in
                       flight (accepted but no data yet)
    cur_collect_k   : K index of the column we are CURRENTLY collecting
                       operands for; bumped on each accumulate_now.
    accum_done      : whether the first compute of this matmul has fired
                       (drives per-cell accum: false on first, true after)
    pending_done    : pulse done on the next tick (matches mma.sv's
                       WRITEBACK cycle layout).

    # Drain
    drain_state     : enum {IDLE, ISSUE, DRAIN_LAST}
    drain_saved_slot
    drain_next_row  : next row to assert drain_en on (0..MMA_M)
    s1_valid/s1_row/s1_last  : drain pipeline stage 1 (row issued LAST cycle,
                              cell's drain_pending captured but drain_data
                              not yet committed)
    s2_valid/s2_row/s2_last  : drain pipeline stage 2 (row issued TWO cycles
                              ago, cell.drain_data is valid THIS cycle and
                              ready to be combinationally assembled into
                              drain_row_data)

BEHAVIOR (per tick, two-phase)
    sample:
        - mma_issue while busy: assert.
        - drain_issue while drain_busy: assert.
        - scrub_en concurrent with mma_issue/drain_issue: assert
          (chip_in_reset gates upstream).

    commit (one pass per tick, in order):
        1. Build per-cell broadcast inputs for this tick from K-loop FSM
           (`compute`, `a_row`, `b_col`, `slot`, `accum`) and drain FSM
           (`drain_en` on row R, `drain_slot`).
        2. tick() each cell with those inputs.
        3. Advance drain pipeline (s1, s2) and FSM state, matching the SV's
           registered-pipeline semantics (transitions use ENTERING values).
        4. drain_row_* are derived combinationally from the NEWLY-committed
           s2_* and cell.drain_data.
        5. drain_done is high when ENTERING (pre-edge) s2_valid && s2_last
           were both true — i.e. the cycle AFTER drain_last fires.
        6. mma_done / arrive_en pulse from prior tick's `_pending_done`.

INVARIANTS
    - At most one matmul and at most one drain in flight at a time.
    - mma + drain may overlap (slot-disjoint guarantees no per-cell
      compute/drain mutex violation).
    - scrub_en mutually exclusive with all other compute/drain activity
      (gated by chip_in_reset upstream).

HANDSHAKE
    Issue: mma_issue=1 at cycle T → mma_busy from cycle T+1 → done one
           cycle after the last compute fires.
    Drain: drain_issue=1 at cycle T → drain_busy from cycle T+1.
           drain_row_valid pulses for MMA_M consecutive cycles (one per
           row, first at T+2). On the final pulse, drain_last=1. The
           cycle AFTER, drain_done=1 and drain_busy=0.

TEST CASES (pymodel/tests/test_compute_array.py)
    1. single_matmul_no_accum: drive A,B operand bytes synthetically; issue
       MMA; tick until done; drain slot; verify rows match A @ B.
    2. matmul_accum: pre-seed slot via cells; issue MMA accum=1; verify
       result is prior + A @ B.
    3. drain_outputs_correct_rows.
    4. scrub_clears_all_slots_via_array.
    5. back_to_back_matmuls (slot isolation).
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

        # Per-cell leaves.
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
        self.drain_row_data: int = 0  # MMA_N * 32 bits packed
        self.drain_row_idx: int = 0
        self.drain_last: int = 0

        # ---- Internal K-loop state ----
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

        # ---- Internal drain state ----
        # Mirrors the SV pipeline exactly so cycle counts match.
        # state, drain_next_row, and (s1, s2) advance per posedge.
        self._drain_state: _DrainState = _DrainState.IDLE
        self._drain_saved_slot: int = 0
        self._drain_next_row: int = 0
        # Stage 1: drain_en went out to a row last cycle. cell.drain_data
        # not yet valid this cycle.
        self._s1_valid: int = 0
        self._s1_row: int = 0
        self._s1_last: int = 0
        # Stage 2: drain_en went out two cycles ago; cell.drain_data is
        # valid this cycle. drain_row_* are driven combinationally from
        # s2_* and cell.drain_data.
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

        # ---- Snapshot pulse flags before clearing ----
        prev_pending_done = self._pending_done

        # ---- Compute K-loop's broadcast outputs for THIS tick ----
        # We must read the CURRENT (registered) rd_*_en values to determine
        # whether last cycle's drive was accepted by SMEM (not stalled). The
        # NEW rd_*_en we compute below becomes self.rd_*_en at the end.
        next_rd_a_en = 0
        next_rd_a_addr = 0
        next_rd_b_en = 0
        next_rd_b_addr = 0

        # Per-cell compute broadcast inputs for THIS tick.
        cell_compute = 0
        cell_a_bytes: bytes = _ZERO_A_BYTES
        cell_b_bytes: bytes = _ZERO_B_BYTES
        cell_slot = 0
        cell_accum = 0

        # K-loop FSM logic.
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
                # Issue column 0.
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

            # Inflight tracking: a read driven last cycle (and accepted by
            # SMEM = not stalled) is "in flight" until rd_*_valid arrives.
            a_just_success = self.rd_a_en and not rd_a_stall_in
            b_just_success = self.rd_b_en and not rd_b_stall_in
            a_inflight_after = (self._a_inflight and not a_arrives) | a_just_success
            b_inflight_after = (self._b_inflight and not b_arrives) | b_just_success

            next_collect_k = self._cur_collect_k + (1 if accumulate_now else 0)
            pa_after = 0 if accumulate_now else next_pa
            pb_after = 0 if accumulate_now else next_pb
            a_inflight_after2 = 0 if accumulate_now else a_inflight_after
            b_inflight_after2 = 0 if accumulate_now else b_inflight_after

            # Issue next reads if more K columns remain and ports free.
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

            # Commit pa/pb stash state.
            if accumulate_now:
                self._pa_valid = 0
                self._pa_data = _ZERO_A_BYTES
                self._pb_valid = 0
                self._pb_data = _ZERO_B_BYTES
                self._a_inflight = 0
                self._b_inflight = 0
            else:
                if a_arrives and not self._pa_valid:
                    self._pa_valid = 1
                    self._pa_data = bytes(rd_a_data)
                if b_arrives and not self._pb_valid:
                    self._pb_valid = 1
                    self._pb_data = bytes(rd_b_data)
                self._a_inflight = a_inflight_after
                self._b_inflight = b_inflight_after

            # Drive compute broadcast this tick if both halves landed.
            if accumulate_now:
                cell_compute = 1
                cell_a_bytes = a_data_now
                cell_b_bytes = b_data_now
                cell_slot = self._saved["slot"]
                cell_accum = 1 if self._accum_done else self._saved["accum"]
                self._accum_done = 1
                self._cur_collect_k = next_collect_k
                if self._cur_collect_k == self.mma_k:
                    self._pending_done = 1

        # ---- Drain FSM: decide whether to drive drain_en on a row ----
        # drain_issue at tick T transitions IDLE→ISSUE; the first drain_en
        # to cells fires at tick T+1 (when state is observed as ISSUE).
        cell_drain_en_row: int | None = None
        cell_drain_slot = 0
        drain_issue_now = 0  # whether we're issuing a new row this tick

        # The current (registered) state determines whether drain_en fires.
        if self._drain_state == _DrainState.ISSUE:
            if self._drain_next_row < self.mma_m:
                cell_drain_en_row = self._drain_next_row
                cell_drain_slot = self._drain_saved_slot
                drain_issue_now = 1

        # ---- Tick all cells with the broadcast inputs ----
        # The compute-bytes are broadcast: row i sees A[i], col j sees B[j].
        for i in range(self.mma_m):
            ai = cell_a_bytes[i] if cell_compute else 0
            for j in range(self.mma_n):
                bj = cell_b_bytes[j] if cell_compute else 0
                de = 1 if cell_drain_en_row == i else 0
                self.cells[i][j].tick(
                    compute=cell_compute,
                    a=ai,
                    b=bj,
                    slot=cell_slot,
                    accum=cell_accum,
                    drain_en=de,
                    drain_slot=cell_drain_slot,
                    scrub_en=scrub_en,
                )

        # Note: drain row output is computed AFTER the pipeline advance
        # below, because in SV drain_row_* are combinational from the
        # NEWLY-committed s2_* (= post-edge values). Defer this assignment.

        # ---- Compute next-state for drain pipeline (sample phase) ----
        # All transitions below evaluate using PRE-edge values (the values
        # observed during this tick); we commit them at the end so they're
        # visible next tick.
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

        # Drain FSM state transition. Match SV: uses ENTERING (pre-edge)
        # values for the transition condition.
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
                # Clear pipeline regs (paranoia).
                next_s1_valid = 0
                next_s2_valid = 0
        elif self._drain_state == _DrainState.ISSUE:
            if drain_issue_now:
                next_drain_next_row = self._drain_next_row + 1
            # Exit when all rows issued AND pipeline drained (using
            # ENTERING values, mirroring SV).
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

        # Commit pipeline + state.
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

        # ---- Drain row output (combinational from NEWLY-committed s2_*) ----
        # In SV: drain_row_* are combinational from s2_* (registered) and
        # cell.drain_data (registered). After the posedge commits s2_* to
        # their next values, drain_row_* reflect those.
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

        # drain_done: in SV, `drain_done <= (s2_valid && s2_last)` using
        # ENTERING (pre-edge) values. The cycle AFTER drain_last is high,
        # drain_done is high.
        next_drain_done = 1 if (entering_s2_valid and entering_s2_last) else 0

        # ---- Done pulses (latched one cycle ago) ----
        next_mma_done = 0
        next_arrive_en = 0
        next_arrive_bar_id = 0
        if prev_pending_done:
            next_mma_done = 1
            next_arrive_en = 1
            next_arrive_bar_id = self._saved["bar_id"] if self._saved else 0
            self._pending_done = 0
            self._state = _MMAState.IDLE
            self.mma_busy = 0
            self._saved = None
            self._accum_done = 0

        # next_drain_done is set above (combinational from entering s2_*).

        # ---- Commit registered outputs ----
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
    # Backdoor helpers (test-only)
    # ------------------------------------------------------------------
    def get_tile(self, slot: int) -> np.ndarray:
        """Return the MMA_M×MMA_N fp32 tile stored at `slot` across all cells."""
        out = np.zeros((self.mma_m, self.mma_n), dtype=np.float32)
        for i in range(self.mma_m):
            for j in range(self.mma_n):
                out[i, j] = float(self.cells[i][j].storage[slot])
        return out

    def set_tile(self, slot: int, tile: np.ndarray) -> None:
        """Backdoor-write a tile into `slot`."""
        assert tile.shape == (self.mma_m, self.mma_n)
        for i in range(self.mma_m):
            for j in range(self.mma_n):
                self.cells[i][j].storage[slot] = np.float32(tile[i, j])
