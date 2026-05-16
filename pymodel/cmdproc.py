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

"""
Implementation NOTES:

Instructions are plain dicts with an 'op' field (see helper builders below).
RTL will decode 64-bit words per ISA.md encoding; cocotb TBs will convert
dicts → words for stimulus.

Tick order in the sim harness: cmdproc.tick() runs FIRST in each cycle and
reads engine.busy/done/etc. from the PREVIOUS cycle's commit (registered).
This gives a 1-cycle observation latency on engine completion / barrier
flips, which is architecturally correct.
"""

from config import OP_BAR_INIT, OP_LOAD, OP_MMA, OP_STORE, OP_WAIT


# ---- Instruction builders ----

def BAR_INIT(bar: int, count: int) -> dict:
    return {"op": OP_BAR_INIT, "bar_id": bar, "count": count}


def LOAD(bar: int, gmem_ptr: int, smem_ptr: int, bytes_n: int) -> dict:
    return {
        "op": OP_LOAD,
        "bar_id": bar,
        "gmem_ptr": gmem_ptr,
        "smem_ptr": smem_ptr,
        "bytes_n": bytes_n,
    }


def MMA(bar: int, a_smem_offset: int, b_smem_offset: int,
        d_tmem_slot: int, accum: int) -> dict:
    return {
        "op": OP_MMA,
        "bar_id": bar,
        "a_smem_offset": a_smem_offset,
        "b_smem_offset": b_smem_offset,
        "d_tmem_slot": d_tmem_slot,
        "accum": accum,
    }


def STORE(tmem_slot: int, gmem_ptr: int, dtype: int = 0) -> dict:
    return {
        "op": OP_STORE,
        "tmem_slot": tmem_slot,
        "gmem_ptr": gmem_ptr,
        "dtype": dtype,
    }


def WAIT(bar: int, expected_phase: int) -> dict:
    return {"op": OP_WAIT, "bar_id": bar, "expected_phase": expected_phase}


# ---- Cmdproc class ----

class CmdProc:
    def __init__(self, mma, load, store, barrier):
        self.mma = mma
        self.load = load
        self.store = store
        self.barrier = barrier
        self.in_fifo: list[dict] = []
        self.state: str = "IDLE"
        self._wait_target = None  # (bar_id, expected_phase) | None
        # Registered output drives (pulse / latched as noted).
        self.init_en: int = 0
        self.init_bar_id: int = 0
        self.init_count: int = 0
        self.mma_start: int = 0
        self.mma_args: dict = {}
        self.load_issue_en: int = 0
        self.load_args: dict = {}
        self.store_issue_en: int = 0
        self.store_args: dict = {}

    def push(self, instr: dict) -> None:
        self.in_fifo.append(instr)

    def push_program(self, program: list[dict]) -> None:
        self.in_fifo.extend(program)

    def _strip_op(self, instr: dict) -> dict:
        return {k: v for k, v in instr.items() if k != "op"}

    def tick(self) -> None:
        # All drive pulses default to 0; dispatch may set some this tick.
        self.init_en = 0
        self.mma_start = 0
        self.load_issue_en = 0
        self.store_issue_en = 0

        # Handle WAIT release (combinational query against barrier registered state).
        if self.state == "WAITING_FOR_WAIT_DONE":
            bar_id, expected = self._wait_target
            if self.barrier.wait_query(bar_id, expected):
                self.state = "IDLE"
                self._wait_target = None

        # Handle STORE completion.
        if self.state == "WAITING_FOR_STORE_DONE":
            if self.store.done:
                self.state = "IDLE"

        # Dispatch next instruction if idle and FIFO non-empty.
        if self.state == "IDLE" and self.in_fifo:
            instr = self.in_fifo.pop(0)
            op = instr["op"]
            if op == OP_BAR_INIT:
                self.init_en = 1
                self.init_bar_id = instr["bar_id"]
                self.init_count = instr["count"]
            elif op == OP_LOAD:
                self.load_issue_en = 1
                self.load_args = self._strip_op(instr)
            elif op == OP_MMA:
                self.mma_start = 1
                self.mma_args = self._strip_op(instr)
            elif op == OP_STORE:
                self.store_issue_en = 1
                self.store_args = self._strip_op(instr)
                self.state = "WAITING_FOR_STORE_DONE"
            elif op == OP_WAIT:
                self._wait_target = (instr["bar_id"], instr["expected_phase"])
                self.state = "WAITING_FOR_WAIT_DONE"
            else:
                assert False, f"unknown opcode {op}"

    @property
    def idle(self) -> int:
        return 1 if (self.state == "IDLE" and not self.in_fifo) else 0
