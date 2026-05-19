"""
mac_tmem_cell — one MAC + per-position TMEM micro-storage with systolic drain.

PURPOSE (Phase 7i-6)
    Leaf module of compute_array. Each cell owns ONE fp32 fused-multiply-add
    datapath and a small register file of N_SLOTS fp32 accumulator words.
    The five-signal compute packet (a, b, compute, slot, accum) flows from
    west/north neighbors through a single pipeline register per cell.

    Drain port is SYSTOLIC: drain_in (32 bits from south neighbor) and
    drain_out (32 bits to north neighbor) form a per-column drain chain.
    A single-cycle broadcast drain_en pulse causes every cell to inject its
    storage[drain_slot] into drain_out. On subsequent cycles, drain_out
    forwards drain_in. Values shift north one cell per cycle; the array's
    top row's drain_out is the chip's drain output. Drain takes M cycles
    for an M-row array — no centralised mux.

INPUTS (sampled at tick start)
    compute_in : 1-bit       — this cycle's (a_in, b_in, slot_in, accum_in)
                               is a valid MAC. Fires the FMA on the FRESH
                               packet; result lands in storage at edge.
    a_in       : 8-bit fp8   — from west neighbor's a_out (or row-feed edge).
    b_in       : 8-bit fp8   — from north neighbor's b_out (or col-feed edge).
    slot_in    : log2(N_SLOTS) — which slot to update.
    accum_in   : 1-bit
    drain_en   : 1-bit       — broadcast; injects storage[drain_slot] into
                               drain_out at the next edge.
    drain_slot : log2(N_SLOTS)
    drain_in   : 32-bit      — from south neighbor's drain_out (or 0 at the
                               south edge of the array).
    init_en, init_slot, init_data, scrub_en : as before.

OUTPUTS (registered, available after tick())
    compute_out, a_out, b_out, slot_out, accum_out : last cycle's _in.
    drain_out  : 32-bit      — registered. If drain_en at cycle T-1 was 1,
                               this is storage[drain_slot] from the cell;
                               else it's drain_in from cycle T-1 (forwarded).

INTERNAL STATE
    storage : np.float32[N_SLOTS], zero-init

BEHAVIOR (per tick)
    sample : check mutex invariants.
    commit, in priority order:
        1. scrub_en : storage[:] = 0
        2. init_en  : storage[init_slot] = init_data (bit-cast to fp32)
        3. compute_in: storage[slot_in] = fp32_fma(a_f, b_f,
                                                  storage[slot_in] if accum_in
                                                  else 0)
        4. Drain commit:
             drain_out = storage[drain_slot] if drain_en else drain_in
             (storage is read BEFORE any same-cycle write above.)
        5. Pipeline-register *_in → *_out.

WRITE-VS-DRAIN ORDERING
    drain reads PRE-WRITE storage. If compute_in writes slot S at cycle T
    AND drain_en pulses with drain_slot=S at cycle T, drain_out at T+1
    reflects the OLD value of storage[S], not the FMA result. In practice
    compute and drain operate on different slots (slot-disjoint) so this
    doesn't matter.

INVARIANTS
    - scrub_en mutually exclusive with compute_in and init_en.
    - init_en  mutually exclusive with compute_in.
    - drain_en may coexist with any of the above (different slots).
    - slot_in, init_slot, drain_slot in [0, N_SLOTS).
"""

import numpy as np

from golden.fp8 import decode_e4m3


class MacTmemCell:
    def __init__(self, n_slots: int = 4):
        assert n_slots >= 1
        self.n_slots: int = n_slots
        self.storage: np.ndarray = np.zeros((n_slots,), dtype=np.float32)
        # Registered outputs.
        self.compute_out: int = 0
        self.a_out: int = 0
        self.b_out: int = 0
        self.slot_out: int = 0
        self.accum_out: int = 0
        self.drain_out: int = 0

    def tick(
        self,
        *,
        compute_in: int = 0,
        a_in: int = 0,
        b_in: int = 0,
        slot_in: int = 0,
        accum_in: int = 0,
        drain_en: int = 0,
        drain_slot: int = 0,
        drain_in: int = 0,
        init_en: int = 0,
        init_slot: int = 0,
        init_data: int = 0,
        scrub_en: int = 0,
    ) -> None:
        # ---- Sample-phase asserts ----
        if scrub_en:
            assert not compute_in, "scrub_en concurrent with compute_in"
            assert not init_en, "scrub_en concurrent with init_en"
        if init_en:
            assert not compute_in, "init_en concurrent with compute_in"
        if compute_in:
            assert 0 <= slot_in < self.n_slots, f"slot_in {slot_in} OOR"
            assert 0 <= a_in <= 0xFF, "a_in must be a uint8 fp8 byte"
            assert 0 <= b_in <= 0xFF, "b_in must be a uint8 fp8 byte"
        if init_en:
            assert 0 <= init_slot < self.n_slots, f"init_slot {init_slot} OOR"
            assert 0 <= init_data <= 0xFFFFFFFF, "init_data must be a uint32"
        if drain_en:
            assert 0 <= drain_slot < self.n_slots, f"drain_slot {drain_slot} OOR"
        assert 0 <= drain_in <= 0xFFFFFFFF, "drain_in must be a uint32"

        # ---- Drain commit: read storage BEFORE writes ----
        # drain_out registers either storage[drain_slot] (if drain_en) or
        # drain_in (passthrough). Captures the PRE-write storage value.
        if drain_en:
            val = self.storage[int(drain_slot)]
            new_drain_out = int(
                np.array([val], dtype=np.float32).view(np.uint32)[0]
            )
        else:
            new_drain_out = int(drain_in) & 0xFFFFFFFF

        # ---- Storage commit (priority order) ----
        if scrub_en:
            self.storage[:] = np.float32(0.0)
        elif init_en:
            bits = np.array([init_data & 0xFFFFFFFF], dtype=np.uint32)
            self.storage[init_slot] = bits.view(np.float32)[0]
        elif compute_in:
            a_f = float(decode_e4m3(np.array([a_in & 0xFF], dtype=np.uint8))[0])
            b_f = float(decode_e4m3(np.array([b_in & 0xFF], dtype=np.uint8))[0])
            c = float(self.storage[slot_in]) if accum_in else 0.0
            af = np.float32(a_f)
            bf = np.float32(b_f)
            cf = np.float32(c)
            self.storage[slot_in] = np.float32(af * bf + cf)

        # ---- Commit registered outputs ----
        self.drain_out = new_drain_out
        self.compute_out = int(compute_in)
        self.a_out = int(a_in) & 0xFF
        self.b_out = int(b_in) & 0xFF
        self.slot_out = int(slot_in)
        self.accum_out = int(accum_in)
