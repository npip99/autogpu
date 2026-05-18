"""
mac_tmem_cell — one MAC + one cell's per-position TMEM micro-storage.

PURPOSE
    Leaf module of the future compute_array fabric (Phase 7h-1). Each cell
    owns ONE fp32 fused-multiply-add datapath and a small register file of
    N_SLOTS fp32 accumulator words (its own column of the spatially-banked
    TMEM at this (i, j) MAC position). compute_array (Phase 7h-2) will
    instantiate MMA_M x MMA_N of these and feed them via a broadcast
    network; chip_top (Phase 7h-3) will swap out the old monolithic
    mma + tmem pair.

    Splitting MMA and TMEM down to per-cell granularity avoids two
    synth-time issues on sky130:
      - 32k-bit packed tile signals across a module boundary won't fit
        on a macro perimeter.
      - ABC chokes on the resulting flattened combinational fanout.

INPUTS (sampled at tick start)
    compute    : 1-bit       — fire the FMA this cycle
    a          : 8-bit fp8   — already row-selected by the broadcast net
    b          : 8-bit fp8   — already col-selected by the broadcast net
    slot       : log2(N_SLOTS) bits — which accumulator slot to update
    accum      : 1-bit       — 1: storage[slot] += a*b ; 0: storage[slot] = a*b
    drain_en   : 1-bit       — fire a slot read (registered, 1-cycle latency)
    drain_slot : log2(N_SLOTS) bits
    init_en    : 1-bit       — seed storage[init_slot] with init_data
    init_slot  : log2(N_SLOTS) bits
    init_data  : 32-bit      — fp32 bit pattern
    scrub_en   : 1-bit       — clear ALL slots to 0 (reset_seq drives this)

OUTPUTS (registered)
    drain_data : 32-bit      — storage[drain_slot] captured one cycle prior

INTERNAL STATE
    storage         : np.float32[N_SLOTS], zero-init
    _drain_pending  : (slot or None) — captured this cycle, drained next

BEHAVIOR (per tick, two-phase)
    sample phase: check input-mutex invariants below.
    commit phase, in priority order (only one of 1-3 fires per cycle):
        1. scrub_en:  storage[:] = 0.
        2. init_en:   storage[init_slot] = bit-cast(init_data, fp32).
        3. compute:   a_f = fp8_to_fp32(a)
                      b_f = fp8_to_fp32(b)
                      c   = storage[slot] if accum else 0.0
                      storage[slot] = fp32_fma(a_f, b_f, c)
        Then: drain the previous cycle's pending read (with write-forwarding
              from the same-cycle commit above), capture the new drain_en.

WRITE-FORWARDING SEMANTICS (drain vs same-cycle commit)
    drain captures at cycle T, drives drain_data at T+1. If a compute / init /
    scrub commits to storage[drain_slot] at cycle T+1 BEFORE the drain reads,
    drain_data presents the NEW value at T+1 — exactly mirroring tmem.sv
    §"WRITE-THEN-DRAIN ordering" / pack_slot() forwarding. A drain on cycle T
    of the same slot that compute also writes on cycle T sees the OLD value;
    the write-forward happens for the drain captured on T-1 and drained at T.
    Equivalently: drain reads storage AFTER the same-cycle write commits.

INVARIANTS
    - scrub_en mutually exclusive with compute and init_en.
    - init_en mutually exclusive with compute.
    - drain_en is read-only and may coexist with any of the above.
    - slot, init_slot, drain_slot in [0, N_SLOTS).

TEST CASES (pymodel/tests/test_mac_tmem_cell.py)
    1. compute_no_accum: drive compute accum=0, storage equals a*b.
    2. compute_with_accum: pre-init, then compute accum=1, storage equals prior + a*b.
    3. drain_latency_1: drain_en at T -> drain_data valid at T+1.
    4. drain_write_forwarding: compute writes slot S at T, drain captured at T-1
       on slot S sees the NEW value at T (write-then-drain order).
    5. init_writes_slot.
    6. scrub_clears_all_slots.
    7. slot_isolation: ops to slot A don't affect slot B.
    8. mutex_assert: simultaneous compute+scrub raises AssertionError.
"""

import numpy as np

from golden.fp8 import decode_e4m3


class MacTmemCell:
    def __init__(self, n_slots: int = 4):
        assert n_slots >= 1
        self.n_slots: int = n_slots
        # Internal state — flip-flop storage, zero-init.
        self.storage: np.ndarray = np.zeros((n_slots,), dtype=np.float32)
        self._drain_pending: int | None = None
        # Registered output.
        self.drain_data: int = 0  # exposed as a 32-bit int matching SV port

    def tick(
        self,
        *,
        compute: int = 0,
        a: int = 0,
        b: int = 0,
        slot: int = 0,
        accum: int = 0,
        drain_en: int = 0,
        drain_slot: int = 0,
        init_en: int = 0,
        init_slot: int = 0,
        init_data: int = 0,
        scrub_en: int = 0,
    ) -> None:
        # ---- Sample-phase asserts (mutex invariants) ----
        if scrub_en:
            assert not compute, "scrub_en concurrent with compute"
            assert not init_en, "scrub_en concurrent with init_en"
        if init_en:
            assert not compute, "init_en concurrent with compute"
        if compute:
            assert 0 <= slot < self.n_slots, f"slot {slot} OOR"
            assert 0 <= a <= 0xFF, "a must be a uint8 fp8 byte"
            assert 0 <= b <= 0xFF, "b must be a uint8 fp8 byte"
        if init_en:
            assert 0 <= init_slot < self.n_slots, f"init_slot {init_slot} OOR"
            assert 0 <= init_data <= 0xFFFFFFFF, "init_data must be a uint32"
        if drain_en:
            assert 0 <= drain_slot < self.n_slots, f"drain_slot {drain_slot} OOR"

        # ---- Commit phase ----
        # 1-3: storage update (priority order).
        if scrub_en:
            self.storage[:] = np.float32(0.0)
        elif init_en:
            # init_data is a 32-bit fp32 bit pattern; reinterpret to fp32.
            bits = np.array([init_data & 0xFFFFFFFF], dtype=np.uint32)
            self.storage[init_slot] = bits.view(np.float32)[0]
        elif compute:
            a_f = float(decode_e4m3(np.array([a & 0xFF], dtype=np.uint8))[0])
            b_f = float(decode_e4m3(np.array([b & 0xFF], dtype=np.uint8))[0])
            c = float(self.storage[slot]) if accum else 0.0
            # Native fp32 a*b + c. fpnew_fma in FMADD/RNE produces bit-exact
            # results vs IEEE-754 fp32 a*b+c with round-to-nearest-even, which
            # is what numpy float32 arithmetic does.
            af = np.float32(a_f)
            bf = np.float32(b_f)
            cf = np.float32(c)
            self.storage[slot] = np.float32(af * bf + cf)
        # else: storage unchanged.

        # 4: drain the previous-cycle pending read (write-forwarded by virtue
        # of having committed above already).
        if self._drain_pending is not None:
            val = self.storage[self._drain_pending]
            self.drain_data = int(
                np.array([val], dtype=np.float32).view(np.uint32)[0]
            )
        else:
            self.drain_data = 0
        self._drain_pending = None

        # 5: capture new pending drain.
        if drain_en:
            self._drain_pending = int(drain_slot)
