"""
cmdproc — command FIFO + decoder + dispatcher. Front-end.

PURPOSE
    Pops decoded instructions from an instruction FIFO. Dispatches to LOAD,
    MMA, STORE engines. Drives BAR.INIT directly into the barrier unit.
    On WAIT, stalls the front-end until barrier.wait_done asserts.

    No PC, no branches. Straight-line execution only. Idle = FIFO empty
    and no in-flight ops.

INSTRUCTION REPRESENTATION (pymodel form)
    Instructions are pre-decoded dataclasses (not 64-bit words) for pymodel
    simplicity. RTL will decode 64-bit words per ISA.md encoding; cocotb TBs
    convert dataclasses → words for stimulus.

    @dataclass
    class Instr:
        op: int                # OP_BAR_INIT | OP_LOAD | OP_MMA | OP_STORE | OP_WAIT
        # operand fields depend on op (use a dataclass per opcode or a union)

INPUTS (sampled at tick start)
    push_en      : 1-bit                — testbench pushes a new instr into FIFO
    push_instr   : Instr                — the instruction
    load.accept  : 1-bit                — from LOAD engine
    load.busy    : 1-bit
    mma.busy     : 1-bit
    mma.done     : 1-bit
    store.busy   : 1-bit
    store.done   : 1-bit
    barrier.wait_done  : 1-bit          — combinational query result

OUTPUTS (registered)
    idle         : 1-bit                — FIFO empty AND no engine busy
    stalled_on_wait : 1-bit             — high while a WAIT is blocked

    barrier.init_en, init_bar_id, init_count
    barrier.query_bar_id, query_expected_phase

    load.issue_en,  load.issue_cmd
    mma.start,      mma.<operands>
    store.issue_en, store.issue_cmd

INTERNAL STATE
    in_fifo      : queue[Instr], depth INSTR_FIFO_DEPTH
    pc           : index into "currently-being-executed" instr (effectively FIFO head)
    state        : enum { IDLE, ISSUING, WAITING_FOR_WAIT_DONE,
                          WAITING_FOR_STORE_DONE }
    wait_target  : (bar_id, expected_phase) — saved across the stall

BEHAVIOR (per tick, two-phase)
    sample : capture push, engine signals, barrier.wait_done
    commit : (one instruction issued or one stall step per tick)
        1. Accept push: in_fifo.push(push_instr) if push_en and not full.
        2. If state == IDLE and in_fifo non-empty:
             instr = in_fifo.pop()
             dispatch by op:
                BAR.INIT  → barrier.init_*; state stays IDLE
                LOAD      → load.issue_en=1 + cmd; state IDLE next cycle (LOAD is async)
                MMA       → mma.start=1 + operands; state IDLE next cycle (MMA is async)
                STORE     → store.issue_en=1 + cmd; state WAITING_FOR_STORE_DONE
                WAIT      → set query_bar_id and query_expected_phase;
                            state WAITING_FOR_WAIT_DONE
        3. If state == WAITING_FOR_WAIT_DONE:
             keep driving query inputs.
             if barrier.wait_done: state = IDLE.
        4. If state == WAITING_FOR_STORE_DONE:
             if store.done: state = IDLE.
        5. Compute idle = (state == IDLE) AND in_fifo.empty AND not load.busy AND not mma.busy AND not store.busy.

INVARIANTS
    - No more than one engine command issued per cycle (LOAD or MMA or STORE).
    - WAIT does not pop the next instruction until released.
    - BAR.INIT takes effect the cycle after dispatch (per barrier's registered
      state).

HANDSHAKE
    push: testbench-side. push_en + push_instr; FIFO accepts if not full.
    dispatch: drives engine inputs for one cycle.

TEST CASES (pymodel/tests/test_cmdproc.py)
    1. straight_line_dispatch: push BAR.INIT, LOAD, LOAD, WAIT, MMA, WAIT, STORE
       and verify each engine receives correct start signals in order.
    2. wait_stall: WAIT blocks until barrier.wait_done; STORE not dispatched
       early.
    3. load_async_advances: push 2 LOADs then MMA. cmdproc doesn't stall
       between LOADs (both accepted within 2 cycles); MMA dispatched after.
    4. store_sync_stalls: STORE dispatched, cmdproc state =
       WAITING_FOR_STORE_DONE until store.done.
    5. idle_signal: idle goes high exactly when FIFO empty + engines done +
       no pending wait.
"""

# Implementation goes here.
