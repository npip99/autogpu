# Architecture

Minimal fp8 matmul accelerator, B200-style. Three memory spaces, three execution engines, async issue with mbarrier completion. No general-purpose compute, no branches, no register file.

See `ISA.md` for the instruction set. This doc describes the *hardware*: what modules exist, how data flows, and the per-module spec convention.

## 1. System block diagram

```
                 +-----------------+
        instr -> | command FIFO    |
        stream   +-------+---------+
                         |
                    pop  v
                 +-----------------+        +--------------+
                 | command proc    |<-----> |   barriers   |
                 | (decode +       |  WAIT  | (atomic on   |
                 |  dispatch only) |  query | smem region) |
                 +--+----+-----+---+        +------+-------+
                    |    |     |                   ^
              start | +args                        | arrive
                    v    v     v                   | add_tx / sub_tx
                 +----+ +-------------+ +-----+    |
                 |LOAD| |compute_array| |STORE|----+
                 +-+--+ +------+------+ +--+--+
                   |       |    ^drain    |
                   |       |    | stream  |
        gmem rd /  |  smem rd*2 +---------+
        smem wr    |       (broadcast K)  | gmem wr
                   v       v              v
        +-----+ +------+ +-----+
        |GMEM | | SMEM | |GMEM |
        +-----+ +------+ +-----+
                  ^
                  | barrier region (first NUM_BARRIERS*16 B)
                  | + operand tiles (rest of SMEM)
```

The arrows are dedicated point-to-point paths. There is no shared bus — LOAD's gmem-read and compute_array's smem-read can happen in the same cycle.

Phase 7h dissolved the old separate `tmem` module: TMEM storage is now per-(i, j) flops inside the 1024 `mac_tmem_cell` leaves of `compute_array`. STORE consumes the drain *stream* (one fp32 element per cycle) rather than reading a wide tile in one shot.

## 2. Memory spaces

| Space | Owner | Width | Latency |
|-------|-------|-------|---------|
| GMEM  | external (TB-model in pymodel; off-chip DRAM in real HW) | byte-addressed | 1 cycle in pymodel |
| SMEM  | on-chip, multi-bank | byte-addressed within bank, banks span MMA_K bytes | 1 cycle read, 1 cycle write |
| TMEM  | distributed across `compute_array`'s 1024 `mac_tmem_cell` leaves (flops) | one slot = MMA_M × MMA_N fp32 cells | per-cell RMW each K-step; drained as a one-element-per-cycle stream |

SMEM's first `NUM_BARRIERS * 16` bytes are reserved for mbarrier objects. The rest holds operand tiles.

## 3. Execution engines

Each engine has the same shape: takes a `start` pulse + an operand bundle, runs N cycles, asserts `done` and (optionally) signals the barrier hardware. Engines never talk to each other directly — only through SMEM, TMEM, GMEM, and the barrier unit.

- **LOAD** — DMA gmem → smem. On `start`: `bar.tx_pending += bytes`. On `done`: `bar.tx_pending -= bytes`, `bar.pending -= 1`.
- **compute_array** — broadcast MAC grid (1024 `mac_tmem_cell` leaves), smem × smem → per-cell TMEM. On `done`: `bar.pending -= 1`. Takes K cycles for a single tile. Exposes the legacy `mma_start` / `mma_busy` / `mma_done` ports so the cmdproc interface is unchanged.
- **STORE** — drains a TMEM slot from `compute_array` (one fp32 element per cycle, row-major) → gmem, optional fp32→fp8 conversion. Synchronous v1: cmdproc stalls until `done`.

## 4. Async issue model

The command processor pops an instruction per cycle (when not stalled) and issues to the matching engine. Issue is **fire-and-forget** for LOAD and MMA — the engine takes ownership of the work, the cmdproc moves on. WAIT is the only instruction that stalls the front-end.

Completion signaling goes through mbarriers:

```
issue           run            complete
  v              v                v
LOAD --[tx+=N]--+----[bytes flowing]----+--[tx-=N, pending-=1]--
                                              v
                                            flip if pending==0 && tx==0
```

WAIT(bar, expected_phase) blocks the front-end until `bar.phase != expected_phase`.

## 5. Cycle model (for pymodel)

Each module exposes a `tick()` method invoked once per simulated clock cycle. Within a tick:

- A module's outputs reflect its state **after** the rising edge.
- Other modules sample those outputs on the **next** cycle (registered, like real RTL).
- Same-cycle combinational chains are not modeled — every signal crosses a register boundary.

Order of module ticks within a cycle is fixed by the sim harness; it doesn't affect semantics as long as everyone sees registered values.

## 6. Module spec convention

Every module folder contains a spec written in this format. The format is the per-module equivalent of "load_en + bus_out gates the register" — every wire named, every state element listed, every behavior stated as a rule.

```
<module name> — <one-line purpose>

INPUTS (sampled at tick start)
    <name> : <width / type> — <meaning>

OUTPUTS (valid after tick)
    <name> : <width / type> — <meaning>

INTERNAL STATE
    <name> : <type> = <initial> — <meaning>

BEHAVIOR (per tick)
    1. <rule>
    2. <rule>
    ...

INVARIANTS
    - <statement that always holds>

HANDSHAKE
    <start/done pulse semantics if applicable>

TEST CASES (in tests/test_<sub>.py)
    1. <scenario> → <expected outcome>
    ...
```

If a module's behavior section needs more than ~10 rules, the module is doing too much and should be split.

## 7. Module map

| Folder | Module | Role |
|--------|--------|------|
| `golden/` | `fp8.py`, `matmul_reference.py` | Reference encode/decode + numpy matmul |
| `pymodel/` | `gmem`, `smem` | Memory models (TMEM is distributed inside `compute_array` post-Phase-7h) |
| `pymodel/` | `mac_tmem_cell`, `compute_array` | Leaf MAC cell + the 1024-cell broadcast array (replaces old `mma` + `tmem`) |
| `pymodel/` | `load`, `store` | DMA execution engines |
| `pymodel/` | `barrier` | mbarrier state machine |
| `pymodel/` | `cmdproc` | Front-end FIFO + dispatcher (its `Mma` instruction class is unchanged) |
| `pymodel/` | `reset_seq` | Scrubs on-chip memories before deasserting `chip_in_reset` |
| `pymodel/` | `sim` | Top-level harness (clock loop, wiring) |
| `pymodel/tests/` | `test_*.py` | Per-module unit tests + e2e |
| `common/` | `tb_utils.py` | Shared Python TB helpers (start_clock, reset, step_and_compare) |
| `<sub>/` | `<sub>.sv` + `tb/test_<sub>.py` + `Makefile` | RTL + cocotb test vs pymodel |
| `load/`, `store/` | also have `<sub>_tb_top.sv` | Wrapper that instantiates the engine + its dependent memories/barrier so cocotb can drive the full sub-system |
| `top/chip_top.sv` + `top/tb/chip_tb_top.sv` | — | **Top-level integration**: chip boundary + behavioral DRAM TB, runs the end-to-end matmul kernel |

The `common/pkg.sv` + `interfaces.sv` originally planned for shared SV types weren't needed in practice — modules use plain `parameter` port lists and a wrapper-SV-per-engine pattern instead. The `top/` folder is reserved for a future synthesizable wrapper (clean external pins, instruction-fetch interface, memory-controller interface) but isn't required for the current Verilator-driven simulation.

## 8. Parameters

All sizes derive from `config.py`. The implementer should never hardcode 32/16/etc — read from the config module so M/N/K can change without touching module code.

In SV, parameters flow via `-G` Verilator flags emitted from each module's Makefile (which runs `python3 -c "from config import ..."` to pull values out of `config.py`).
