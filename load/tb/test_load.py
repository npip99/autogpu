"""
cocotb testbench for load.sv (LOAD DMA engine).

Drives load.sv + gmem + smem + barrier (instantiated by load_tb_top.sv) and
verifies that:
  - LOAD copies the right bytes from gmem to smem.
  - Barrier accounting matches pymodel: add_tx on accept, sub_tx + arrive on
    completion, tx_pending back to 0 afterwards.
  - The engine-contract signals (busy, done, accept, barrier pulses) match
    pymodel.load.Load cycle-by-cycle for random command sequences.

Tests:
  1. test_single_load           — one LOAD; verify smem + barrier.
  2. test_two_loads_queued      — two LOADs accepted back-to-back; verify both.
  3. test_random_vs_pymodel     — 8 random sequences; side-by-side compare
                                  every cycle of (busy, done, accept,
                                  add_tx_*, sub_tx_*, arrive_*).
"""

import random

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep

from common.tb_utils import start_clock, reset
from config import BEAT_BYTES, NUM_BARRIERS, SMEM_BYTES, SMEM_TILE_BASE
from pymodel.gmem import GMEM as PyGMEM
from pymodel.load import Load as PyLoad
from pymodel.smem import SMEM as PySMEM


# Pipeline drain margin: number of extra cycles to wait after busy=0 before
# checking smem contents. With 1-cycle gmem latency + NBA crossing between
# load and gmem (and load and smem), the last smem.mem update lands a few
# cycles after busy=0. 10 cycles is comfortably safe.
PIPELINE_DRAIN_CYCLES = 10


# --- Input defaults: drive these BEFORE reset so X's don't propagate. ---
INPUT_DEFAULTS = {
    "issue_en":        0,
    "gmem_ptr":        0,
    "smem_ptr":        0,
    "bytes_n":         0,
    "bar_id":          0,
    "bar_init_en":     0,
    "bar_init_bar_id": 0,
    "bar_init_count":  0,
}


def _drive_defaults(dut) -> None:
    for name, val in INPUT_DEFAULTS.items():
        getattr(dut, name).value = val


def _drive(dut, inputs: dict) -> None:
    merged = dict(INPUT_DEFAULTS)
    merged.update(inputs)
    for name, val in merged.items():
        getattr(dut, name).value = val


def _backdoor_gmem_write(dut, addr: int, data: bytes) -> None:
    """Write bytes directly into u_gmem.mem via hierarchical access.

    Avoids needing to drive the gmem write port for many cycles to preload a
    pattern. This is a TB-only convenience.
    """
    for i, b in enumerate(data):
        dut.u_gmem.mem[addr + i].value = b


def _backdoor_smem_read(dut, addr: int, n: int) -> bytes:
    """Read bytes directly out of u_smem.mem via hierarchical access."""
    out = bytearray(n)
    for i in range(n):
        out[i] = int(dut.u_smem.mem[addr + i].value)
    return bytes(out)


def _bar_pending(dut, bar_id: int) -> int:
    return (int(dut.bars_pending.value) >> (bar_id * 16)) & 0xFFFF


def _bar_tx_pending(dut, bar_id: int) -> int:
    return (int(dut.bars_tx_pending.value) >> (bar_id * 32)) & 0xFFFFFFFF


def _bar_phase(dut, bar_id: int) -> int:
    return (int(dut.bars_phase.value) >> bar_id) & 1


def _pat(base: int, length: int) -> bytes:
    """Deterministic byte pattern used for gmem preload."""
    return bytes((base + i) & 0xFF for i in range(length))


async def _bar_init(dut, bar_id: int, count: int) -> None:
    """One-cycle pulse of bar_init_en. Issues INIT for `bar_id` with `count`."""
    _drive(dut, {
        "bar_init_en":     1,
        "bar_init_bar_id": bar_id,
        "bar_init_count":  count,
    })
    await RisingEdge(dut.clk)
    _drive_defaults(dut)


async def _wait_until_idle(dut, max_cycles: int = 4096) -> int:
    """Tick until dut.busy == 0 (then drain pipeline a bit). Returns total cycles."""
    cycles = 0
    while True:
        await RisingEdge(dut.clk)
        cycles += 1
        await ReadOnly()
        if int(dut.busy.value) == 0:
            await NextTimeStep()
            break
        await NextTimeStep()
        if cycles >= max_cycles:
            raise AssertionError(f"LOAD did not become idle in {max_cycles} cycles")
    # Extra pipeline drain so the last smem writes land in mem.
    for _ in range(PIPELINE_DRAIN_CYCLES):
        await RisingEdge(dut.clk)
        cycles += 1
    return cycles


# ---------------------------------------------------------------------------
# Test 1: single LOAD
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_single_load(dut):
    """Preload gmem with a known pattern, issue one LOAD, verify smem + barrier."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)

    # Preload gmem with a pattern.
    nbytes = 256  # 16 beats
    gmem_src = 0
    smem_dst = SMEM_TILE_BASE  # 128 (NUM_BARRIERS=8 * 16 BARRIER_BYTES = 128)
    bar = 0
    pat = _pat(0xA0, nbytes)
    _backdoor_gmem_write(dut, gmem_src, pat)

    # Init the target barrier with count=1 (the LOAD will arrive once).
    await _bar_init(dut, bar, 1)
    # tx_pending should still be 0 right after init.
    await ReadOnly()
    assert _bar_tx_pending(dut, bar) == 0, "tx_pending should start at 0 after INIT"
    await NextTimeStep()

    # Issue the LOAD command. Pulse issue_en for one cycle with operands.
    _drive(dut, {
        "issue_en": 1,
        "gmem_ptr": gmem_src,
        "smem_ptr": smem_dst,
        "bytes_n":  nbytes,
        "bar_id":   bar,
    })
    await RisingEdge(dut.clk)
    # Snapshot accept + add_tx pulse on the accept cycle.
    await ReadOnly()
    assert int(dut.accept.value)        == 1, "expected accept=1 on issue cycle"
    assert int(dut.add_tx_en.value)     == 1, "expected add_tx_en=1 on issue cycle"
    assert int(dut.add_tx_bar_id.value) == bar
    assert int(dut.add_tx_bytes.value)  == nbytes
    await NextTimeStep()

    # Stop pulsing issue_en.
    _drive_defaults(dut)

    # Run until idle + pipeline drain.
    await _wait_until_idle(dut)

    # smem should hold the same pattern at the destination offset.
    smem_bytes = _backdoor_smem_read(dut, smem_dst, nbytes)
    assert smem_bytes == pat, (
        f"smem mismatch: first 16 bytes got {smem_bytes[:16].hex()} "
        f"expected {pat[:16].hex()}"
    )

    # Barrier state: tx_pending must have returned to 0 (sub_tx fired);
    # pending was 1, arrive decremented to 0, flip → phase=1, pending reloads to 1.
    assert _bar_tx_pending(dut, bar) == 0, (
        f"tx_pending should be 0 after LOAD completes; got {_bar_tx_pending(dut, bar)}"
    )
    assert _bar_phase(dut, bar) == 1, (
        f"phase should have flipped to 1; got {_bar_phase(dut, bar)}"
    )
    # pending reloaded to expected (1).
    assert _bar_pending(dut, bar) == 1, (
        f"pending should reload to expected=1; got {_bar_pending(dut, bar)}"
    )


# ---------------------------------------------------------------------------
# Test 2: two LOADs queued
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_two_loads_queued(dut):
    """Issue two LOADs back-to-back (accept within 2 cycles); verify smem+barrier."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)

    nbytes_a = 256
    nbytes_b = 128
    gmem_a = 0
    gmem_b = 1024
    smem_a = SMEM_TILE_BASE
    smem_b = SMEM_TILE_BASE + nbytes_a
    bar = 1
    pat_a = _pat(0x10, nbytes_a)
    pat_b = _pat(0x50, nbytes_b)
    _backdoor_gmem_write(dut, gmem_a, pat_a)
    _backdoor_gmem_write(dut, gmem_b, pat_b)

    # Init barrier with count=2 (we expect 2 arrives, one per LOAD).
    await _bar_init(dut, bar, 2)

    # Cycle A: issue LOAD-A.
    _drive(dut, {
        "issue_en": 1,
        "gmem_ptr": gmem_a,
        "smem_ptr": smem_a,
        "bytes_n":  nbytes_a,
        "bar_id":   bar,
    })
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.accept.value)       == 1, "expected accept=1 on LOAD-A cycle"
    assert int(dut.add_tx_en.value)    == 1
    assert int(dut.add_tx_bytes.value) == nbytes_a
    await NextTimeStep()

    # Cycle B (back-to-back): issue LOAD-B.
    _drive(dut, {
        "issue_en": 1,
        "gmem_ptr": gmem_b,
        "smem_ptr": smem_b,
        "bytes_n":  nbytes_b,
        "bar_id":   bar,
    })
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.accept.value)       == 1, "expected accept=1 on LOAD-B cycle"
    assert int(dut.add_tx_en.value)    == 1
    assert int(dut.add_tx_bytes.value) == nbytes_b
    await NextTimeStep()

    _drive_defaults(dut)
    await _wait_until_idle(dut)

    # Verify smem contents.
    got_a = _backdoor_smem_read(dut, smem_a, nbytes_a)
    got_b = _backdoor_smem_read(dut, smem_b, nbytes_b)
    assert got_a == pat_a, f"LOAD-A smem mismatch; first 8 bytes {got_a[:8].hex()}"
    assert got_b == pat_b, f"LOAD-B smem mismatch; first 8 bytes {got_b[:8].hex()}"

    # Barrier: both LOADs sub_tx'd their bytes (tx_pending=0). Two arrives on
    # bar with count=2 → pending=0 → flip → phase=1 → pending reload to 2.
    assert _bar_tx_pending(dut, bar) == 0
    assert _bar_phase(dut, bar)      == 1
    assert _bar_pending(dut, bar)    == 2


# ---------------------------------------------------------------------------
# Test 3: random vs pymodel (cycle-by-cycle compare on engine-contract signals)
# ---------------------------------------------------------------------------

# Signals we MUST match each cycle.
_LEVEL_SIGNALS = ["busy", "done", "accept", "add_tx_en", "sub_tx_en", "arrive_en"]
# Bar id / bytes are only meaningful when their *_en is high. We still compare
# them whenever *_en matches and is high on both sides.


def _sample_dut(dut) -> dict:
    return {
        "busy":          int(dut.busy.value),
        "done":          int(dut.done.value),
        "accept":        int(dut.accept.value),
        "add_tx_en":     int(dut.add_tx_en.value),
        "add_tx_bar_id": int(dut.add_tx_bar_id.value),
        "add_tx_bytes":  int(dut.add_tx_bytes.value),
        "sub_tx_en":     int(dut.sub_tx_en.value),
        "sub_tx_bar_id": int(dut.sub_tx_bar_id.value),
        "sub_tx_bytes":  int(dut.sub_tx_bytes.value),
        "arrive_en":     int(dut.arrive_en.value),
        "arrive_bar_id": int(dut.arrive_bar_id.value),
    }


def _sample_py(py: PyLoad) -> dict:
    return {
        "busy":          int(py.busy),
        "done":          int(py.done),
        "accept":        int(py.accept),
        "add_tx_en":     int(py.add_tx_en),
        "add_tx_bar_id": int(py.add_tx_bar_id),
        "add_tx_bytes":  int(py.add_tx_bytes),
        "sub_tx_en":     int(py.sub_tx_en),
        "sub_tx_bar_id": int(py.sub_tx_bar_id),
        "sub_tx_bytes":  int(py.sub_tx_bytes),
        "arrive_en":     int(py.arrive_en),
        "arrive_bar_id": int(py.arrive_bar_id),
    }


def _compare(sv: dict, py: dict, cycle: int, inputs: dict) -> None:
    """Assert engine-contract signal equivalence between SV and pymodel."""
    for k in _LEVEL_SIGNALS:
        assert sv[k] == py[k], (
            f"cycle {cycle}: {k} mismatch: sv={sv[k]} py={py[k]} "
            f"inputs={inputs}\n  sv={sv}\n  py={py}"
        )
    # When add_tx_en is high on both, the operands must match.
    if py["add_tx_en"]:
        assert sv["add_tx_bar_id"] == py["add_tx_bar_id"], (
            f"cycle {cycle}: add_tx_bar_id mismatch sv={sv['add_tx_bar_id']} "
            f"py={py['add_tx_bar_id']}"
        )
        assert sv["add_tx_bytes"] == py["add_tx_bytes"], (
            f"cycle {cycle}: add_tx_bytes mismatch sv={sv['add_tx_bytes']} "
            f"py={py['add_tx_bytes']}"
        )
    if py["sub_tx_en"]:
        assert sv["sub_tx_bar_id"] == py["sub_tx_bar_id"], (
            f"cycle {cycle}: sub_tx_bar_id mismatch"
        )
        assert sv["sub_tx_bytes"] == py["sub_tx_bytes"], (
            f"cycle {cycle}: sub_tx_bytes mismatch"
        )
    if py["arrive_en"]:
        assert sv["arrive_bar_id"] == py["arrive_bar_id"], (
            f"cycle {cycle}: arrive_bar_id mismatch"
        )


def _random_load_seq(rng: random.Random, n_cmds: int = 3) -> list:
    """Build a random list of LOAD command operands.

    Constraints:
      * bytes_n is a multiple of BEAT_BYTES, in [BEAT_BYTES, 8*BEAT_BYTES].
      * gmem_ptr is BEAT_BYTES-aligned, fits in [0, 8192).
      * smem_ptr is BEAT_BYTES-aligned, lives in [SMEM_TILE_BASE, SMEM_BYTES).
      * Destination regions don't overlap (so we can spot-check smem at end).
      * bar_id in [0, NUM_BARRIERS).
    """
    cmds = []
    smem_cursor = SMEM_TILE_BASE
    gmem_cursor = 0
    for _ in range(n_cmds):
        beats = rng.randint(1, 8)
        nbytes = beats * BEAT_BYTES
        gmem_ptr = gmem_cursor
        smem_ptr = smem_cursor
        bar_id = rng.randrange(NUM_BARRIERS)
        cmds.append({
            "gmem_ptr": gmem_ptr,
            "smem_ptr": smem_ptr,
            "bytes_n":  nbytes,
            "bar_id":   bar_id,
        })
        gmem_cursor += nbytes
        smem_cursor += nbytes
        # Keep both cursors inside their memories.
        assert smem_cursor <= SMEM_BYTES, "smem cursor overflow in test setup"
    return cmds


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """Drive ~8 random LOAD sequences. Lockstep compare to pymodel.load.Load."""
    await start_clock(dut)
    _drive_defaults(dut)
    await reset(dut)

    # We re-seed the env between sequences but keep the same dut/pymodel.
    # Actually use a fresh dut state per sequence is hard; let's run multiple
    # sequences without resetting (FIFO drains between them) and pre-init a
    # pymodel for each sequence. To keep state aligned, we reset the dut too
    # between sequences.
    rng = random.Random(0xD0DAC8)

    N_SEQ = 8
    for seq_idx in range(N_SEQ):
        # Reset between sequences for clean state on both sides.
        if seq_idx > 0:
            _drive_defaults(dut)
            await reset(dut)

        # Fresh pymodel instances for each sequence.
        py_gmem = PyGMEM()
        py_smem = PySMEM()
        py_load = PyLoad(py_gmem, py_smem)

        cmds = _random_load_seq(rng, n_cmds=rng.randint(1, 3))

        # Preload gmem on BOTH sides with random patterns at each cmd's region.
        for c in cmds:
            pat = bytes(rng.getrandbits(8) for _ in range(c["bytes_n"]))
            _backdoor_gmem_write(dut, c["gmem_ptr"], pat)
            py_gmem.load(c["gmem_ptr"], pat)

        # Schedule when to issue each cmd. Pick a small random delay between
        # issues (0 = back-to-back, >0 = gap), bounded so we don't outrun
        # the input FIFO.
        issue_schedule = []
        cyc = 0
        for c in cmds:
            issue_schedule.append((cyc, c))
            cyc += rng.randint(1, 4)

        last_issue_cycle = issue_schedule[-1][0]
        # Run for enough cycles to drain. Worst case 8 beats per cmd at 1
        # logical cycle/beat = 8 cycles per cmd, plus some margin.
        total_cycles = last_issue_cycle + len(cmds) * 16 + 20

        next_issue_iter = iter(issue_schedule)
        next_issue = next(next_issue_iter, None)

        for cyc in range(total_cycles):
            # Build inputs for this cycle.
            if next_issue is not None and next_issue[0] == cyc:
                c = next_issue[1]
                inputs = {
                    "issue_en": 1,
                    "gmem_ptr": c["gmem_ptr"],
                    "smem_ptr": c["smem_ptr"],
                    "bytes_n":  c["bytes_n"],
                    "bar_id":   c["bar_id"],
                    "bar_init_en":     0,
                    "bar_init_bar_id": 0,
                    "bar_init_count":  0,
                }
                next_issue = next(next_issue_iter, None)
            else:
                inputs = dict(INPUT_DEFAULTS)

            _drive(dut, inputs)
            await RisingEdge(dut.clk)

            # Tick the pymodel with the engine-issue inputs only.
            py_kwargs = {
                "issue_en": inputs["issue_en"],
                "gmem_ptr": inputs["gmem_ptr"],
                "smem_ptr": inputs["smem_ptr"],
                "bytes_n":  inputs["bytes_n"],
                "bar_id":   inputs["bar_id"],
            }
            py_load.tick(**py_kwargs)

            await ReadOnly()
            sv = _sample_dut(dut)
            py = _sample_py(py_load)
            _compare(sv, py, cycle=cyc, inputs=inputs)
            await NextTimeStep()

        # After each sequence, verify dut and pymodel both ended idle.
        assert int(dut.busy.value) == 0, (
            f"seq {seq_idx}: dut still busy after {total_cycles} cycles"
        )
        assert py_load.busy == 0, f"seq {seq_idx}: pymodel still busy"
