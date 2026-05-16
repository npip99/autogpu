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

# Implementation goes here.
