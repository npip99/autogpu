"""
sim — top-level harness. Instantiates all modules; ticks them in lockstep.

PURPOSE
    The pymodel "VM". Owns instances of gmem, smem, tmem, barrier, load, mma,
    store, cmdproc. Per tick, sample all module inputs, then commit all
    states, emulating Verilog two-phase synchronous semantics.

PUBLIC API
    class Sim:
        gmem, smem, tmem, barrier, load, mma, store, cmdproc  (the modules)

        load_program(instrs: list[Instr]) -> None
            Push all instrs into cmdproc.in_fifo at once (or one per cycle if
            FIFO not large enough — implementer choice).

        load_gmem(addr: int, data: bytes) -> None
            Backdoor: gmem.load(addr, data)

        read_gmem(addr: int, n: int) -> bytes
            Backdoor: gmem.dump(addr, n)

        tick() -> None
            Advance every module by one clock cycle.

        run_until_idle(max_cycles: int = 1_000_000) -> int
            Tick until cmdproc.idle == 1 and all engines idle.
            Returns number of cycles taken. Raises if max_cycles exceeded.

TICK ORDERING (two-phase)
    Phase A (sample):
        For each module in any order, call module.sample(inputs)
        where inputs are the CURRENT (registered) outputs of other modules.
        This guarantees nobody sees same-cycle in-progress values.
    Phase B (commit):
        For each module, call module.commit() — updates internal state
        and registered outputs. New values visible to others next tick.

    The exact wiring (who consumes whose outputs) is established at Sim
    construction. Each module exposes ports as Python objects; sim wires
    pointers between them at __init__.

INTERNAL WIRING (summary; exact attribute names TBD by implementer)
    cmdproc.barrier_iface ↔ barrier.init/query/etc
    cmdproc.load_iface    ↔ load.issue
    cmdproc.mma_iface     ↔ mma.start, operands
    cmdproc.store_iface   ↔ store.issue
    load.gmem_iface       ↔ gmem read port
    load.smem_iface       ↔ smem LOAD_WR
    load.barrier_iface    ↔ barrier add_tx/sub_tx/arrive
    mma.smem_a_iface      ↔ smem MMA_RD_A
    mma.smem_b_iface      ↔ smem MMA_RD_B
    mma.tmem_iface        ↔ tmem MMA_PORT
    mma.barrier_iface     ↔ barrier arrive
    store.tmem_iface      ↔ tmem STORE_RD
    store.gmem_iface      ↔ gmem write port

IDLE DETECTION
    Sim is idle when:
      - cmdproc.in_fifo is empty
      - cmdproc.state == IDLE
      - load.busy == 0 AND mma.busy == 0 AND store.busy == 0
      - all barriers have phase that the cmdproc no longer waits on
        (this falls out of cmdproc.state == IDLE)

INVARIANTS
    - At most one engine command issued per cycle (cmdproc enforces).
    - No combinational paths cross module boundaries (every signal seen
      by a module is registered from the previous cycle).
    - run_until_idle is deterministic given the same program + gmem contents.

TEST CASES (pymodel/tests/test_e2e.py)
    1. single_tile_matmul (THE HEADLINE):
         - golden.matmul_reference.generate(M=32, N=32, K=32, seed=0)
           → (A_bytes, B_bytes, C_expected)
         - sim.load_gmem(A_gmem, A_bytes), sim.load_gmem(B_gmem, B_bytes)
         - sim.load_program([
             BAR_INIT(b=0, count=2),
             BAR_INIT(b=1, count=1),
             LOAD(bar=0, gmem=A_gmem, smem=A_smem, bytes=len(A_bytes)),
             LOAD(bar=0, gmem=B_gmem, smem=B_smem, bytes=len(B_bytes)),
             WAIT(bar=0, phase=0),
             MMA(bar=1, A_smem, B_smem, D_tmem=0, accum=0),
             WAIT(bar=1, phase=0),
             STORE(tmem=0, gmem=C_gmem, dtype=0),  # fp32 output
           ])
         - sim.run_until_idle()
         - C = sim.read_gmem(C_gmem, M*N*4)
         - assert C as fp32 matches C_expected within tolerance.

         When this passes, the ARCHITECTURE is proven. Every subsequent failure
         is an RTL-vs-pymodel disagreement, never an architectural question.
"""

"""
Implementation NOTES:

Tick order each cycle (matters for cross-module signal visibility):
    1. cmdproc.tick()   — reads engines.busy/done and barrier.phase from
                          previous cycle's commit; sets drive signals.
    2. mma.tick(...)    — reads cmdproc's drives this cycle.
    3. load.tick(...)   — same.
    4. store.tick(...)  — same.
    5. barrier.tick(...) — reads cmdproc.init_* + engine arrive/tx signals
                          from this cycle; flip check runs last.

This produces a 1-cycle observation latency on barrier flips and engine
completion as seen by cmdproc — architecturally correct (matches RTL's
registered output semantics).
"""

from pymodel.barrier import Barrier
from pymodel.cmdproc import CmdProc
from pymodel.gmem import GMEM
from pymodel.load import Load
from pymodel.mma import MMA
from pymodel.smem import SMEM
from pymodel.store import Store
from pymodel.tmem import TMEM


class Sim:
    def __init__(self):
        self.gmem = GMEM()
        self.smem = SMEM()
        self.tmem = TMEM()
        self.barrier = Barrier()
        self.mma = MMA(self.smem, self.tmem)
        self.load = Load(self.gmem, self.smem)
        self.store = Store(self.tmem, self.gmem)
        self.cmdproc = CmdProc(self.mma, self.load, self.store, self.barrier)
        self.cycle: int = 0

    # ---- Public API ----

    def load_program(self, program: list[dict]) -> None:
        self.cmdproc.push_program(program)

    def load_gmem(self, addr: int, data: bytes) -> None:
        self.gmem.load(addr, data)

    def read_gmem(self, addr: int, n: int) -> bytes:
        return self.gmem.dump(addr, n)

    def tick(self) -> None:
        self.cycle += 1
        cp = self.cmdproc

        # 1. cmdproc decides
        cp.tick()

        # 2. engines (gated on cmdproc's current-cycle drives)
        if cp.mma_start:
            self.mma.tick(start=1, **cp.mma_args)
        else:
            self.mma.tick()

        if cp.load_issue_en:
            self.load.tick(issue_en=1, **cp.load_args)
        else:
            self.load.tick()

        if cp.store_issue_en:
            self.store.tick(issue_en=1, **cp.store_args)
        else:
            self.store.tick()

        # 3. barrier — gathers init + arrive + tx from this cycle's signals
        self.barrier.tick(
            init_en=cp.init_en,
            init_bar_id=cp.init_bar_id,
            init_count=cp.init_count,
            arrive_en_a=self.load.arrive_en,
            arrive_bar_id_a=self.load.arrive_bar_id,
            arrive_en_b=self.mma.arrive_en,
            arrive_bar_id_b=self.mma.arrive_bar_id,
            add_tx_en=self.load.add_tx_en,
            add_tx_bar_id=self.load.add_tx_bar_id,
            add_tx_bytes=self.load.add_tx_bytes,
            sub_tx_en=self.load.sub_tx_en,
            sub_tx_bar_id=self.load.sub_tx_bar_id,
            sub_tx_bytes=self.load.sub_tx_bytes,
        )

    def is_idle(self) -> bool:
        return (
            bool(self.cmdproc.idle)
            and not self.mma.busy
            and not self.load.busy
            and not self.store.busy
        )

    def run_until_idle(self, max_cycles: int = 100_000) -> int:
        for _ in range(max_cycles):
            self.tick()
            if self.is_idle():
                return self.cycle
        raise AssertionError(
            f"Sim did not become idle within {max_cycles} cycles (cycle={self.cycle})"
        )
