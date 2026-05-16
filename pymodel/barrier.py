"""
barrier — mbarrier state machine, NUM_BARRIERS independent objects.

PURPOSE
    Tracks completion of async LOAD and MMA operations. WAIT instruction
    queries phase; cmdproc stalls until flip.

    Each mbarrier has 4 state elements:
        pending     : N-bit counter; decremented on arrive; reloads on flip
        expected    : N-bit; set on init, used to reload pending on flip
        tx_pending  : 32-bit byte counter; tracks in-flight LOAD bytes
        phase       : 1-bit; toggles when (pending==0 && tx_pending==0)

    STORAGE: internal to this module (NOT in SMEM in this pymodel; see
    ARCHITECTURE.md §2 — real HW stores in SMEM, pymodel keeps it internal
    for simplicity).

PORTS

    INIT (from cmdproc)
        INPUTS: init_en, init_bar_id, init_count

    ARRIVE (from LOAD and MMA)
        INPUTS: arrive_en, arrive_bar_id

    ADD_TX (from LOAD on accept)
        INPUTS: add_tx_en, add_tx_bar_id, add_tx_bytes

    SUB_TX (from LOAD on completion)
        INPUTS: sub_tx_en, sub_tx_bar_id, sub_tx_bytes

    WAIT_QUERY (from cmdproc — combinational read)
        INPUTS: query_bar_id, query_expected_phase
        OUTPUTS: wait_done (1-bit) — asserts when bar.phase != query_expected_phase

INPUTS (sampled at tick start)
    init_en, init_bar_id, init_count
    arrive_en[2], arrive_bar_id[2]      — 2 source channels (LOAD, MMA)
    add_tx_en, add_tx_bar_id, add_tx_bytes
    sub_tx_en, sub_tx_bar_id, sub_tx_bytes
    query_bar_id, query_expected_phase  — combinational read

OUTPUTS (registered, except wait_done which is combinational on query inputs)
    wait_done    : 1-bit, combinational over current state
    bar_state[i] : { pending, expected, tx_pending, phase } — observable for tests

INTERNAL STATE
    bars : array of NUM_BARRIERS mbarrier records
           each: { pending: int, expected: int, tx_pending: int, phase: 0|1 }
           init: all zeros

RTL FIELD WIDTHS (per ISA.md §"Barrier State (mbarrier)")
    pending     : 16 bits
    expected    : 16 bits
    tx_pending  : 32 bits
    phase       : 1 bit
    Pymodel uses Python ints (unbounded). Tests should constrain init_count
    and arrive counts so the 16-bit pending field can't wrap.

INIT DOMINANCE IS PER-BAR
    When INIT targets bar X, other channels' ops on bar X are dropped this cycle.
    Ops on DIFFERENT bars (Y != X) proceed normally. This means INIT on bar 0 +
    ARRIVE on bar 1 in the same cycle both apply.

BEHAVIOR (per tick, two-phase)
    sample : capture all enabled signals
    commit : apply updates in this STRICT PRIORITY ORDER to each bar:
        1. INIT:    bars[id] = { expected: count, pending: count, tx_pending: 0, phase: 0 }
        2. ADD_TX:  bars[id].tx_pending += bytes
        3. SUB_TX:  bars[id].tx_pending -= bytes  (assert >= 0)
        4. ARRIVE (from LOAD AND/OR MMA, may be 2 in one cycle):
                   bars[id].pending -= (number of arrives this cycle)  (assert >= 0)
        5. FLIP CHECK: ONLY for bars that had SUB_TX or ARRIVE this cycle.
                   if bars[id].pending == 0 AND bars[id].tx_pending == 0:
                       bars[id].phase ^= 1
                       bars[id].pending = bars[id].expected
                   INIT alone (even with count=0) does NOT trigger a flip —
                   flips are caused by *decrement* events reaching zero,
                   not by hard resets to zero.

    Multi-driver-per-cycle policy: all of {INIT, ADD_TX, SUB_TX, ARRIVE×2} for the
    same bar in the same cycle is supported. INIT is DOMINANT — when INIT
    targets a bar, other ops (ADD_TX/SUB_TX/ARRIVE) on the SAME bar in the
    SAME cycle are dropped. Ops on DIFFERENT bars are independent.
    Two ARRIVEs on the same bar in one cycle decrement pending by 2.

    WAIT_QUERY is combinational (not registered):
        wait_done = (bars[query_bar_id].phase != query_expected_phase)

INVARIANTS
    - pending >= 0; tx_pending >= 0 at all times (after each tick's commits).
    - expected does not change after INIT until next INIT.
    - bar_id in [0, NUM_BARRIERS).

HANDSHAKE
    INIT: synchronous, takes effect this cycle (one-cycle latency before WAIT can observe).
    ARRIVE/ADD_TX/SUB_TX: take effect this cycle.
    WAIT: combinational; cmdproc samples wait_done in the cycle after the flip-causing event.

TEST CASES (pymodel/tests/test_barrier.py)
    1. init_sets_state: INIT(bar=0, count=2), bar 0 has pending=2, expected=2, tx=0, phase=0.
    2. arrive_only_flip: INIT(2), 2 arrives → phase flips, pending reloads to 2.
    3. tx_only_flip: INIT(0), add_tx 1024 → flip blocked; sub_tx 1024 → flips (since pending=0).
    4. combined_load_pattern: INIT(2), then (add_tx 1024, add_tx 1024) in successive cycles,
       then sub_tx + arrive twice → flips when both counters reach 0.
    5. dual_arrive_same_cycle: 2 arrives in one tick decrement pending by 2.
    6. wait_query_combinational: WAIT(bar=0, expected=0) → returns 0 (not flipped) when phase=0,
       returns 1 immediately after flip.
    7. priority_order: INIT + arrive in same cycle resets to fresh state regardless of arrive.
"""

from dataclasses import dataclass

from config import NUM_BARRIERS


@dataclass
class _BarState:
    pending: int = 0
    expected: int = 0
    tx_pending: int = 0
    phase: int = 0


class Barrier:
    def __init__(self):
        self.bars = [_BarState() for _ in range(NUM_BARRIERS)]

    def tick(
        self,
        init_en: int = 0,
        init_bar_id: int = 0,
        init_count: int = 0,
        arrive_en_a: int = 0,
        arrive_bar_id_a: int = 0,
        arrive_en_b: int = 0,
        arrive_bar_id_b: int = 0,
        add_tx_en: int = 0,
        add_tx_bar_id: int = 0,
        add_tx_bytes: int = 0,
        sub_tx_en: int = 0,
        sub_tx_bar_id: int = 0,
        sub_tx_bytes: int = 0,
    ) -> None:
        # Sample-phase asserts.
        if init_en:
            assert 0 <= init_bar_id < NUM_BARRIERS
            assert init_count >= 0
        if arrive_en_a:
            assert 0 <= arrive_bar_id_a < NUM_BARRIERS
        if arrive_en_b:
            assert 0 <= arrive_bar_id_b < NUM_BARRIERS
        if add_tx_en:
            assert 0 <= add_tx_bar_id < NUM_BARRIERS
            assert add_tx_bytes >= 0
        if sub_tx_en:
            assert 0 <= sub_tx_bar_id < NUM_BARRIERS
            assert sub_tx_bytes >= 0

        # Track which bars had a *decrement* event this cycle (gates FLIP CHECK).
        decremented: set[int] = set()

        # Priority 1: INIT. Dominant — drops other ops on same bar this cycle.
        if init_en:
            self.bars[init_bar_id] = _BarState(
                pending=init_count,
                expected=init_count,
                tx_pending=0,
                phase=0,
            )

        def _init_dominates(bar_id: int) -> bool:
            return bool(init_en) and init_bar_id == bar_id

        # Priority 2: ADD_TX.
        if add_tx_en and not _init_dominates(add_tx_bar_id):
            self.bars[add_tx_bar_id].tx_pending += add_tx_bytes

        # Priority 3: SUB_TX.
        if sub_tx_en and not _init_dominates(sub_tx_bar_id):
            self.bars[sub_tx_bar_id].tx_pending -= sub_tx_bytes
            assert self.bars[sub_tx_bar_id].tx_pending >= 0, "sub_tx underflow"
            decremented.add(sub_tx_bar_id)

        # Priority 4: ARRIVE (LOAD + MMA channels; may decrement by up to 2 per bar).
        arrives_per_bar: dict[int, int] = {}
        if arrive_en_a:
            arrives_per_bar[arrive_bar_id_a] = arrives_per_bar.get(arrive_bar_id_a, 0) + 1
        if arrive_en_b:
            arrives_per_bar[arrive_bar_id_b] = arrives_per_bar.get(arrive_bar_id_b, 0) + 1
        for bar_id, count in arrives_per_bar.items():
            if _init_dominates(bar_id):
                continue
            self.bars[bar_id].pending -= count
            assert self.bars[bar_id].pending >= 0, "arrive underflow"
            decremented.add(bar_id)

        # Priority 5: FLIP CHECK — only on bars that had a decrement event.
        for bar_id in decremented:
            b = self.bars[bar_id]
            if b.pending == 0 and b.tx_pending == 0:
                b.phase ^= 1
                b.pending = b.expected

    def wait_query(self, bar_id: int, expected_phase: int) -> int:
        """Combinational: 1 iff barrier has flipped past expected_phase."""
        assert 0 <= bar_id < NUM_BARRIERS
        return 1 if self.bars[bar_id].phase != expected_phase else 0
