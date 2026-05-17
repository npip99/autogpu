"""
cmdproc — instruction memory + decoder + dispatcher. Front-end with ALU.

PURPOSE
    Holds a linear instruction memory (imem) and a program counter (pc).
    Dispatches engine instructions (BAR_INIT, LOAD, MMA, STORE, WAIT) and
    runs a small in-cmdproc CPU (8 GPRs + ALU + branches) for address
    computation, loop control, and conditional accum flags.

INSTRUCTION REPRESENTATION
    Pydantic models — see ``Instr`` union below. Helper builders BAR_INIT,
    LOAD, MMA, STORE, WAIT, REPEAT, END, SET_REG, ADDI, ADD, SUB, AND, BRZ,
    BRNZ, JMP construct typed instances. Field validation is done by Pydantic
    at construction time.

OPERAND TYPES (address fields)
    int                    — pure immediate (current behavior)
    IterAddr(base, stride) — base + iter_reg * stride at dispatch
    RegRef(reg)            — value of register r{reg}
    RegOff(base, reg)      — base + r{reg}

    Address fields on LOAD/MMA/STORE accept any of the four. MMA's accum
    accepts (int | IterNonzero); WAIT's expected_phase is plain int (host
    can compute it via the ALU and pass via register-relative form in v2).

REGISTERS
    8 general-purpose 32-bit registers (r0..r7). All start at 0. ALU
    operations are 32-bit unsigned (wrap on overflow). Used for loop
    counters, address pointers, scratch values.

CONTROL FLOW
    REPEAT N / END — convenience counted loop. cmdproc maintains a loop
        stack; iter_reg points at the innermost active loop. Operands can
        reference iter_reg via IterAddr/IterNonzero.
    BRZ ra, offset / BRNZ ra, offset / JMP offset — relative jumps. offset
        is signed, added to current PC (so offset=0 stays in place, offset=1
        falls through, offset=-3 jumps back 3 instructions).

INPUTS (sampled at tick start)
    push(instr)            — testbench appends to imem
    barrier.wait_done      — combinational query result
    {load,mma,store}.{busy,done,accept}

OUTPUTS (registered)
    idle                   — pc >= len(imem) AND state == IDLE
    barrier.init_*         — pulse on BAR_INIT
    barrier.query_*        — driven while WAITING_FOR_WAIT_DONE
    {load,mma,store}.{issue_en/start} + args  — pulse on dispatch

INTERNAL STATE
    imem        : list[Instr]
    pc          : int
    state       : { IDLE, WAITING_FOR_WAIT_DONE, WAITING_FOR_STORE_DONE }
    iter_reg    : int — innermost loop's counter
    loop_stack  : list[{body_start, iter_max, parent_iter}]
    regs        : list[int] of length 8, each 32-bit

BEHAVIOR (per tick)
    1. Service stall states (WAIT release, STORE done).
    2. If IDLE and pc < len(imem): dispatch instr at pc.
       - Engine ops resolve operands and set drive signals; pc += 1.
       - Control flow ops manipulate pc and (for REPEAT/END) loop_stack.
       - ALU ops update regs; pc += 1 (or jump for branches).

INVARIANTS
    - One engine command dispatched per cycle at most.
    - Nested REPEAT/END supported via loop_stack. iter_reg always refers to
      innermost active loop. On END exit, iter_reg restores parent's value.
    - Branches use signed PC-relative offsets. offset=0 is a self-loop.
    - REPEAT 0 skips body (scan to matching END).
    - All ALU is 32-bit wrap-on-overflow.

TEST CASES (pymodel/tests/test_cmdproc.py)
    Existing tests run unchanged — helper functions return pydantic objects
    with the same field semantics as the old dicts.
    New tests:
      - test_repeat_basic           : REPEAT N + END loops body N times
      - test_repeat_zero_count      : REPEAT 0 skips body
      - test_repeat_with_iter_addr  : LOAD inside REPEAT with iter-aware ptr
      - test_alu_set_reg_addi       : register file + immediate add
      - test_branch_brz_brnz        : conditional + unconditional jumps
      - test_k_loop_via_alu         : K-loop using SET_REG/ADDI/BRNZ (no REPEAT)
      - test_nested_repeat          : REPEAT inside REPEAT preserves outer iter

Implementation NOTES:
    Tick order in the sim harness: cmdproc.tick() runs FIRST each cycle and
    reads engine.busy/done/etc. from the PREVIOUS cycle's commit (registered).
    1-cycle observation latency on engine completion / barrier flips —
    architecturally correct.

    Backward compat: helper functions BAR_INIT/LOAD/MMA/STORE/WAIT keep their
    pre-Pydantic call signatures and now return Pydantic models. Existing
    pymodel + cocotb tests don't need updating.
"""

from typing import Union

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Literal

from config import (
    OP_BAR_INIT, OP_END, OP_LOAD, OP_MMA, OP_REPEAT, OP_STORE, OP_WAIT,
)


# ============================================================================
# Operand types
# ============================================================================

class _OpndBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IterAddr(_OpndBase):
    """Resolves to base + iter_reg * stride at dispatch time."""
    kind: Literal["iter_addr"] = "iter_addr"
    base: int
    stride: int


class IterNonzero(_OpndBase):
    """Resolves to 1 iff iter_reg != 0 (for MMA accum flag in K-loops)."""
    kind: Literal["iter_nonzero"] = "iter_nonzero"


class RegRef(_OpndBase):
    """Resolves to regs[reg] at dispatch."""
    kind: Literal["reg"] = "reg"
    reg: int = Field(ge=0, le=7)


class RegOff(_OpndBase):
    """Resolves to base + regs[reg] at dispatch."""
    kind: Literal["reg_off"] = "reg_off"
    base: int
    reg: int = Field(ge=0, le=7)


AddrOperand = Union[int, IterAddr, RegRef, RegOff]
AccumOperand = Union[int, IterNonzero]


# ============================================================================
# Instruction types (Pydantic)
# ============================================================================

class _InstrBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BarInit(_InstrBase):
    op: Literal["BAR_INIT"] = "BAR_INIT"
    bar_id: int
    count: int


class Load(_InstrBase):
    op: Literal["LOAD"] = "LOAD"
    bar_id: int
    gmem_ptr: AddrOperand
    smem_ptr: AddrOperand
    bytes_n: int


class Mma(_InstrBase):
    op: Literal["MMA"] = "MMA"
    bar_id: int
    a_smem_offset: AddrOperand
    b_smem_offset: AddrOperand
    d_tmem_slot: int
    accum: AccumOperand


class Store(_InstrBase):
    op: Literal["STORE"] = "STORE"
    tmem_slot: int
    gmem_ptr: AddrOperand
    dtype: int = 0


class Wait(_InstrBase):
    op: Literal["WAIT"] = "WAIT"
    bar_id: int
    expected_phase: int


class Repeat(_InstrBase):
    op: Literal["REPEAT"] = "REPEAT"
    count: int


class End(_InstrBase):
    op: Literal["END"] = "END"


# --- ALU instructions ---

class SetReg(_InstrBase):
    """rd = value (immediate)."""
    op: Literal["SET_REG"] = "SET_REG"
    rd: int = Field(ge=0, le=7)
    value: int


class Add(_InstrBase):
    """rd = ra + rb (32-bit wrap)."""
    op: Literal["ADD"] = "ADD"
    rd: int = Field(ge=0, le=7)
    ra: int = Field(ge=0, le=7)
    rb: int = Field(ge=0, le=7)


class AddI(_InstrBase):
    """rd = ra + imm (32-bit wrap, imm is signed)."""
    op: Literal["ADDI"] = "ADDI"
    rd: int = Field(ge=0, le=7)
    ra: int = Field(ge=0, le=7)
    imm: int


class Sub(_InstrBase):
    """rd = ra - rb (32-bit wrap)."""
    op: Literal["SUB"] = "SUB"
    rd: int = Field(ge=0, le=7)
    ra: int = Field(ge=0, le=7)
    rb: int = Field(ge=0, le=7)


class And_(_InstrBase):
    """rd = ra & rb. (Python builtin shadowing — class named And_; helper And below.)"""
    op: Literal["AND"] = "AND"
    rd: int = Field(ge=0, le=7)
    ra: int = Field(ge=0, le=7)
    rb: int = Field(ge=0, le=7)


class BrZ(_InstrBase):
    """if regs[ra] == 0: pc += offset (signed); else: pc += 1."""
    op: Literal["BRZ"] = "BRZ"
    ra: int = Field(ge=0, le=7)
    offset: int


class BrNZ(_InstrBase):
    """if regs[ra] != 0: pc += offset (signed); else: pc += 1."""
    op: Literal["BRNZ"] = "BRNZ"
    ra: int = Field(ge=0, le=7)
    offset: int


class Jmp(_InstrBase):
    """pc += offset (signed)."""
    op: Literal["JMP"] = "JMP"
    offset: int


Instr = Union[
    BarInit, Load, Mma, Store, Wait, Repeat, End,
    SetReg, Add, AddI, Sub, And_, BrZ, BrNZ, Jmp,
]


# ============================================================================
# Helper builders (backward compat + ergonomic constructors)
# ============================================================================

def BAR_INIT(bar: int, count: int) -> BarInit:
    return BarInit(bar_id=bar, count=count)


def LOAD(bar: int, gmem_ptr: AddrOperand, smem_ptr: AddrOperand,
         bytes_n: int) -> Load:
    return Load(bar_id=bar, gmem_ptr=gmem_ptr, smem_ptr=smem_ptr, bytes_n=bytes_n)


def MMA(bar: int, a_smem_offset: AddrOperand, b_smem_offset: AddrOperand,
        d_tmem_slot: int, accum: AccumOperand) -> Mma:
    return Mma(bar_id=bar, a_smem_offset=a_smem_offset, b_smem_offset=b_smem_offset,
               d_tmem_slot=d_tmem_slot, accum=accum)


def STORE(tmem_slot: int, gmem_ptr: AddrOperand, dtype: int = 0) -> Store:
    return Store(tmem_slot=tmem_slot, gmem_ptr=gmem_ptr, dtype=dtype)


def WAIT(bar: int, expected_phase: int) -> Wait:
    return Wait(bar_id=bar, expected_phase=expected_phase)


def REPEAT(count: int) -> Repeat:
    return Repeat(count=count)


def END() -> End:
    return End()


def SET_REG(rd: int, value: int) -> SetReg:
    return SetReg(rd=rd, value=value)


def ADD(rd: int, ra: int, rb: int) -> Add:
    return Add(rd=rd, ra=ra, rb=rb)


def ADDI(rd: int, ra: int, imm: int) -> AddI:
    return AddI(rd=rd, ra=ra, imm=imm)


def SUB(rd: int, ra: int, rb: int) -> Sub:
    return Sub(rd=rd, ra=ra, rb=rb)


def AND(rd: int, ra: int, rb: int) -> And_:
    return And_(rd=rd, ra=ra, rb=rb)


def BRZ(ra: int, offset: int) -> BrZ:
    return BrZ(ra=ra, offset=offset)


def BRNZ(ra: int, offset: int) -> BrNZ:
    return BrNZ(ra=ra, offset=offset)


def JMP(offset: int) -> Jmp:
    return Jmp(offset=offset)


def iter_addr(base: int, stride: int) -> IterAddr:
    return IterAddr(base=base, stride=stride)


def iter_nonzero() -> IterNonzero:
    return IterNonzero()


def reg_ref(r: int) -> RegRef:
    return RegRef(reg=r)


def reg_off(base: int, r: int) -> RegOff:
    return RegOff(base=base, reg=r)


# ============================================================================
# CmdProc
# ============================================================================

_MASK32 = (1 << 32) - 1
_NUM_REGS = 8


class CmdProc:
    def __init__(self, mma, load, store, barrier):
        self.mma = mma
        self.load = load
        self.store = store
        self.barrier = barrier
        # Instruction memory + program counter
        self.imem: list[Instr] = []
        self.pc: int = 0
        # State machine
        self.state: str = "IDLE"
        self._wait_target = None  # (bar_id, expected_phase) | None
        # Loop state
        self.iter_reg: int = 0
        self.loop_stack: list[dict] = []
        # Register file
        self.regs: list[int] = [0] * _NUM_REGS
        # Registered output drives
        self.init_en: int = 0
        self.init_bar_id: int = 0
        self.init_count: int = 0
        self.mma_start: int = 0
        self.mma_args: dict = {}
        self.load_issue_en: int = 0
        self.load_args: dict = {}
        self.store_issue_en: int = 0
        self.store_args: dict = {}

    # ---- Program loading ----

    def push(self, instr: Instr) -> None:
        self.imem.append(instr)

    def push_program(self, program: list[Instr]) -> None:
        self.imem.extend(program)

    # ---- Operand resolution ----

    def _resolve_addr(self, operand) -> int:
        if isinstance(operand, int):
            return operand
        if isinstance(operand, IterAddr):
            return operand.base + self.iter_reg * operand.stride
        if isinstance(operand, RegRef):
            return self.regs[operand.reg]
        if isinstance(operand, RegOff):
            return operand.base + self.regs[operand.reg]
        raise TypeError(f"unknown addr operand type: {type(operand)}")

    def _resolve_accum(self, operand) -> int:
        if isinstance(operand, int):
            return operand
        if isinstance(operand, IterNonzero):
            return 1 if self.iter_reg != 0 else 0
        raise TypeError(f"unknown accum operand type: {type(operand)}")

    # ---- Loop control ----

    def _skip_to_matching_end(self) -> int:
        """Return pc just past the matching End for a 0-count Repeat at self.pc."""
        depth = 1
        p = self.pc + 1
        while p < len(self.imem):
            instr = self.imem[p]
            if isinstance(instr, Repeat):
                depth += 1
            elif isinstance(instr, End):
                depth -= 1
                if depth == 0:
                    return p + 1
            p += 1
        raise AssertionError(f"REPEAT at pc={self.pc} has no matching END")

    # ---- Main tick ----

    def tick(self) -> None:
        # Default drive pulses to 0 each tick.
        self.init_en = 0
        self.mma_start = 0
        self.load_issue_en = 0
        self.store_issue_en = 0

        # WAIT release (combinational against barrier's registered state).
        if self.state == "WAITING_FOR_WAIT_DONE":
            bar_id, expected = self._wait_target
            if self.barrier.wait_query(bar_id, expected):
                self.state = "IDLE"
                self._wait_target = None

        # STORE completion.
        if self.state == "WAITING_FOR_STORE_DONE":
            if self.store.done:
                self.state = "IDLE"

        if not (self.state == "IDLE" and self.pc < len(self.imem)):
            return

        instr = self.imem[self.pc]

        # --- Engine dispatch ---

        if isinstance(instr, BarInit):
            self.init_en = 1
            self.init_bar_id = instr.bar_id
            self.init_count = instr.count
            self.pc += 1
        elif isinstance(instr, Load):
            self.load_issue_en = 1
            self.load_args = {
                "bar_id": instr.bar_id,
                "gmem_ptr": self._resolve_addr(instr.gmem_ptr),
                "smem_ptr": self._resolve_addr(instr.smem_ptr),
                "bytes_n": instr.bytes_n,
            }
            self.pc += 1
        elif isinstance(instr, Mma):
            self.mma_start = 1
            self.mma_args = {
                "bar_id": instr.bar_id,
                "a_smem_offset": self._resolve_addr(instr.a_smem_offset),
                "b_smem_offset": self._resolve_addr(instr.b_smem_offset),
                "d_tmem_slot": instr.d_tmem_slot,
                "accum": self._resolve_accum(instr.accum),
            }
            self.pc += 1
        elif isinstance(instr, Store):
            self.store_issue_en = 1
            self.store_args = {
                "tmem_slot": instr.tmem_slot,
                "gmem_ptr": self._resolve_addr(instr.gmem_ptr),
                "dtype": instr.dtype,
            }
            self.state = "WAITING_FOR_STORE_DONE"
            self.pc += 1
        elif isinstance(instr, Wait):
            self._wait_target = (instr.bar_id, instr.expected_phase)
            self.state = "WAITING_FOR_WAIT_DONE"
            self.pc += 1

        # --- Control flow ---

        elif isinstance(instr, Repeat):
            if instr.count > 0:
                self.loop_stack.append({
                    "body_start": self.pc + 1,
                    "iter_max": instr.count,
                    "parent_iter": self.iter_reg,
                })
                self.iter_reg = 0
                self.pc += 1
            else:
                self.pc = self._skip_to_matching_end()
        elif isinstance(instr, End):
            if self.loop_stack:
                frame = self.loop_stack[-1]
                self.iter_reg += 1
                if self.iter_reg < frame["iter_max"]:
                    self.pc = frame["body_start"]
                else:
                    self.loop_stack.pop()
                    self.iter_reg = frame["parent_iter"]
                    self.pc += 1
            else:
                # Stray END — no-op.
                self.pc += 1

        # --- ALU ---

        elif isinstance(instr, SetReg):
            self.regs[instr.rd] = instr.value & _MASK32
            self.pc += 1
        elif isinstance(instr, Add):
            self.regs[instr.rd] = (self.regs[instr.ra] + self.regs[instr.rb]) & _MASK32
            self.pc += 1
        elif isinstance(instr, AddI):
            self.regs[instr.rd] = (self.regs[instr.ra] + instr.imm) & _MASK32
            self.pc += 1
        elif isinstance(instr, Sub):
            self.regs[instr.rd] = (self.regs[instr.ra] - self.regs[instr.rb]) & _MASK32
            self.pc += 1
        elif isinstance(instr, And_):
            self.regs[instr.rd] = (self.regs[instr.ra] & self.regs[instr.rb]) & _MASK32
            self.pc += 1
        elif isinstance(instr, BrZ):
            if self.regs[instr.ra] == 0:
                self.pc += instr.offset
            else:
                self.pc += 1
        elif isinstance(instr, BrNZ):
            if self.regs[instr.ra] != 0:
                self.pc += instr.offset
            else:
                self.pc += 1
        elif isinstance(instr, Jmp):
            self.pc += instr.offset

        else:
            raise TypeError(f"unknown instruction: {type(instr).__name__}")

    @property
    def idle(self) -> int:
        return 1 if (self.state == "IDLE" and self.pc >= len(self.imem)) else 0
