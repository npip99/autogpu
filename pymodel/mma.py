"""
mma — broadcast MAC grid: smem × smem → tmem accumulator.

PURPOSE
    Executes one MMA instruction: D = (accum ? D : 0) + A @ B^T, where A is
    MMA_M×MMA_K, B is MMA_N×MMA_K, D is MMA_M×MMA_N. Async issue; barrier
    arrival on completion.

INPUTS (sampled at tick start)
    start          : 1-bit
    a_smem_offset  : 32-bit  — A tile base, column-major in SMEM
    b_smem_offset  : 32-bit  — B tile base, row-major in SMEM
    d_tmem_slot    : log2(TMEM_SLOTS) bits
    accum          : 1-bit (0 = zero D first, 1 = add into existing D)
    bar_id         : log2(NUM_BARRIERS) bits
    (smem MMA_RD_A.rd_data, rd_valid)
    (smem MMA_RD_B.rd_data, rd_valid)
    (tmem MMA_PORT.rd_tile, rd_valid)

OUTPUTS (registered)
    done           : 1-bit, pulses one cycle on completion
    busy           : 1-bit, high while computing
    smem.MMA_RD_A.rd_en, rd_addr
    smem.MMA_RD_B.rd_en, rd_addr
    tmem.MMA_PORT.op, slot, write_tile
    barrier.arrive_en, arrive_bar_id

INTERNAL STATE
    busy           : 1-bit, init 0
    k_counter      : log2(MMA_K)+1 bits, init 0
    saved          : { a_off, b_off, d_slot, accum, bar_id }
    acc            : np.float32[MMA_M, MMA_N], init zeros — internal accumulator

PIPELINE (MMA_K + 2 cycles total from start to done)
    Cycle 0 (start asserted, busy 0 → 1):
        - latch saved operands
        - if accum: issue tmem READ on slot = d_slot
                    issue smem read A col 0 (addr = a_off + 0*MMA_M)
                    issue smem read B row 0 (addr = b_off + 0*MMA_N)
        - else:     acc <= 0
                    issue smem read A col 0, B row 0
        - k_counter <= 0
    Cycle 1..MMA_K:
        on tick T = 1..MMA_K:
            - if accum and T == 1: tmem rd_tile is valid → acc <= rd_tile
            - smem rd_a_data, rd_b_data are valid for column (T-1)
              A_col_fp32 = fp8.decode_e4m3(rd_a_data)   # MMA_M values
              B_row_fp32 = fp8.decode_e4m3(rd_b_data)   # MMA_N values
              acc += outer(A_col_fp32, B_row_fp32)      # fp32 MMA_M × MMA_N
            - if T < MMA_K: issue smem reads for column T (= k_counter+1)
              else (T == MMA_K): no more smem reads
            - k_counter += 1
    Cycle MMA_K + 1 (completion):
        - issue tmem WRITE on slot d_slot with write_tile = acc
        - arrive on barrier bar_id
        - done <= 1
        - busy <= 0

LATENCY
    From the cycle `start=1` is sampled, `done` pulses MMA_K + 2 cycles later
    (or MMA_K + 1 if no initial tmem read; for simplicity we always wait
    the tmem read cycle, so it's MMA_K + 2 regardless of accum flag).

INVARIANTS
    - Only one MMA in flight: start while busy=1 asserts.
    - acc is fp32 throughout; fp8 decoding happens at the SMEM read boundary.
    - smem layouts: A is column-major (a_off + k*MMA_M is column k);
                    B is row-major     (b_off + k*MMA_N is row    k).
    - TMEM tile packing (in RTL): use the convention in pymodel/tmem.py
      §"RTL TILE PACKING CONVENTION". Element [i][j] at bit (i*MMA_N+j)*32,
      IEEE 754 fp32 LSB-first.

HANDSHAKE
    Issue: start=1 at cycle T (when busy=0) → busy=1 from cycle T+1.
    Done:  done pulses high for exactly one cycle (T + MMA_K + 2).
           barrier arrival is concurrent with done.

TEST CASES (pymodel/tests/test_mma.py)
    1. accum0_known_inputs: backdoor-load known A,B into SMEM (column/row-major as spec),
       run MMA with accum=0 → after MMA_K+2 cycles, tmem slot equals A @ B^T (fp8 decode then fp32 matmul).
    2. accum1_adds_into_existing: pre-set slot=D0, run MMA accum=1, slot equals D0 + A@B.
    3. barrier_arrival: barrier.pending decremented by 1 on completion.
    4. random_via_golden: use golden.matmul_reference to generate A,B,C; run MMA; slot matches C within fp32 tolerance.
    5. busy_blocks_start: assert start while busy=1 asserts.
"""

"""
Implementation NOTES (deviation from spec wire-level for pymodel simplicity):

The pymodel MMA accesses SMEM and TMEM directly via back-door (smem.dump,
tmem.get_slot, tmem.set_slot) instead of through registered ports. Cycle
latency (MMA_K + 2 from start to done) is still modeled accurately. The
Phase 4 RTL cocotb TBs will exercise the actual port-level interface;
this back-door simplification keeps pymodel wiring tractable.

Outputs to the SIM harness (not registered SMEM/TMEM port drives):
    arrive_en, arrive_bar_id  — barrier arrival on completion
    done, busy                — handshake signals
"""

import numpy as np

from config import MMA_K, MMA_M, MMA_N
from golden.fp8 import decode_e4m3


class MMA:
    def __init__(self, smem, tmem):
        self.smem = smem
        self.tmem = tmem
        # Registered outputs
        self.busy: int = 0
        self.done: int = 0
        self.arrive_en: int = 0
        self.arrive_bar_id: int = 0
        # Internal state
        self._cyc: int = 0
        self._saved: dict | None = None
        self._acc = np.zeros((MMA_M, MMA_N), dtype=np.float32)

    def tick(
        self,
        *,
        start: int = 0,
        a_smem_offset: int = 0,
        b_smem_offset: int = 0,
        d_tmem_slot: int = 0,
        accum: int = 0,
        bar_id: int = 0,
        rd_a_stall_in: int = 0,
        rd_b_stall_in: int = 0,
    ) -> None:
        """Tick one cycle.

        `rd_a_stall_in` / `rd_b_stall_in` mirror the SMEM's combinational
        stall outputs for this cycle. In the current pymodel sim, MMA uses
        back-door SMEM access (smem.dump) so it never actually contends
        with LOAD writes — but for parity with the RTL stall protocol,
        when either stall is asserted we pause the per-K-cycle accumulate
        (do not advance _cyc this tick). When the stall clears, MMA
        resumes from the same column.
        """
        # Default pulse outputs to 0 each cycle.
        self.done = 0
        self.arrive_en = 0

        if not self.busy:
            if start:
                self._saved = {
                    "a_off": a_smem_offset,
                    "b_off": b_smem_offset,
                    "d_slot": d_tmem_slot,
                    "accum": accum,
                    "bar_id": bar_id,
                }
                if accum:
                    self._acc = self.tmem.get_slot(d_tmem_slot).astype(np.float32)
                else:
                    self._acc = np.zeros((MMA_M, MMA_N), dtype=np.float32)
                self.busy = 1
                self._cyc = 1  # next tick will be cyc=1
            return

        # busy
        if start:
            assert False, "MMA start asserted while busy"

        cyc = self._cyc
        if 1 <= cyc <= MMA_K:
            # If either SMEM read port stalled this cycle, pause the
            # accumulate. The consumer would re-issue the same reads next
            # cycle and re-attempt the same column.
            if rd_a_stall_in or rd_b_stall_in:
                return
            k = cyc - 1
            a_addr = self._saved["a_off"] + k * MMA_M
            b_addr = self._saved["b_off"] + k * MMA_N
            A_col = decode_e4m3(np.frombuffer(self.smem.dump(a_addr, MMA_M), dtype=np.uint8))
            B_row = decode_e4m3(np.frombuffer(self.smem.dump(b_addr, MMA_N), dtype=np.uint8))
            self._acc += np.outer(A_col, B_row)
            self._cyc = cyc + 1
        elif cyc == MMA_K + 1:
            # Writeback + arrive + done
            self.tmem.set_slot(self._saved["d_slot"], self._acc)
            self.arrive_en = 1
            self.arrive_bar_id = self._saved["bar_id"]
            self.done = 1
            self.busy = 0
            self._cyc = 0
