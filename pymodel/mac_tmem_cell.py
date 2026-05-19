"""
mac_tmem_cell — one MAC + one cell's per-position TMEM micro-storage (systolic).

PURPOSE
    Leaf module of compute_array (Phase 7i). Each cell owns ONE fp32 fused-
    multiply-add datapath and a small register file of N_SLOTS fp32 accumulator
    words. The five-signal compute packet (a, b, compute, slot, accum) flows
    from west/north neighbors through a single pipeline register per cell and
    out to east/south neighbors; the per-cell hop delay is 1.

    Latency to the (M-1, N-1) corner for a K-loop fed at cycle 0:
        K + M + N - 2 cycles.

INPUTS (sampled at tick start)
    compute_in : 1-bit       — this cycle's (a_in, b_in, slot_in, accum_in) is
                               a valid MAC. Fires the FMA on the FRESH packet
                               (the result lands in storage at edge).
    a_in       : 8-bit fp8   — comes from west neighbor's a_out (or row-feed edge).
    b_in       : 8-bit fp8   — comes from north neighbor's b_out (or col-feed edge).
    slot_in    : log2(N_SLOTS) — which slot to update.
    accum_in   : 1-bit       — 1: storage[slot_in] += a*b ; 0: storage[slot_in] = a*b
    drain_en   : 1-bit       — fire a slot read (registered, 1-cycle latency).
    drain_slot : log2(N_SLOTS)
    init_en    : 1-bit       — seed storage[init_slot] with init_data.
    init_slot  : log2(N_SLOTS)
    init_data  : 32-bit fp32 bit pattern
    scrub_en   : 1-bit       — clear ALL slots to 0 (reset_seq drives this).

OUTPUTS (registered, available after tick())
    compute_out: 1-bit       — last cycle's compute_in.
    a_out      : 8-bit       — last cycle's a_in.
    b_out      : 8-bit       — last cycle's b_in.
    slot_out   : log2(N_SLOTS) — last cycle's slot_in.
    accum_out  : 1-bit       — last cycle's accum_in.
    drain_data : 32-bit      — storage[drain_slot] captured one cycle prior.

INTERNAL STATE
    storage        : np.float32[N_SLOTS], zero-init
    _drain_pending : (slot or None) — captured this cycle, drained next.

BEHAVIOR (per tick, two-phase)
    sample phase: check input-mutex invariants below.
    commit phase, in priority order (only one of 1-3 fires per cycle):
        1. scrub_en:    storage[:] = 0.
        2. init_en:     storage[init_slot] = bit-cast(init_data, fp32).
        3. compute_in:  a_f = fp8_to_fp32(a_in)
                        b_f = fp8_to_fp32(b_in)
                        c   = storage[slot_in] if accum_in else 0.0
                        storage[slot_in] = fp32_fma(a_f, b_f, c)
        4. Drain the previous cycle's pending read with write-forwarding.
        5. Capture new pending drain (if drain_en).
        6. Pipeline-register the compute packet: *_out <- *_in.

WRITE-FORWARDING SEMANTICS (drain vs same-cycle commit)
    drain captures at cycle T, drives drain_data at T+1. A compute / init /
    scrub on cycle T+1 to storage[drain_slot] is visible in drain_data at T+1.

ORDERING NOTE FOR PARENT MODULE
    compute_array MUST snapshot all cells' _out values BEFORE any cell's tick,
    then feed each cell's _in from the snapshot. Otherwise a cell's tick will
    overwrite an upstream cell's _out before downstream cells read it.

INVARIANTS
    - scrub_en mutually exclusive with compute_in and init_en.
    - init_en  mutually exclusive with compute_in.
    - drain_en may coexist with any of the above.
    - slot_in, init_slot, drain_slot in [0, N_SLOTS).
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
        # Registered outputs (last cycle's _in values).
        self.compute_out: int = 0
        self.a_out: int = 0
        self.b_out: int = 0
        self.slot_out: int = 0
        self.accum_out: int = 0
        self.drain_data: int = 0  # 32-bit int matching SV port

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
        init_en: int = 0,
        init_slot: int = 0,
        init_data: int = 0,
        scrub_en: int = 0,
    ) -> None:
        # ---- Sample-phase asserts (mutex invariants) ----
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

        # ---- Commit phase ----
        # 1-3: storage update (priority order).
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
        # else: storage unchanged.

        # 4: drain the previous-cycle pending read (write-forwarded by
        # virtue of having committed above already).
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

        # 6: pipeline-register the compute packet.
        self.compute_out = int(compute_in)
        self.a_out = int(a_in) & 0xFF
        self.b_out = int(b_in) & 0xFF
        self.slot_out = int(slot_in)
        self.accum_out = int(accum_in)
