"""Tests for pymodel.cmdproc (integrated via Sim, since cmdproc wires to all engines)."""

from config import SMEM_TILE_BASE
from pymodel.cmdproc import BAR_INIT, LOAD, MMA, STORE, WAIT
from pymodel.sim import Sim


def test_initial_idle():
    sim = Sim()
    assert sim.is_idle()


def test_bar_init_only():
    """A program that just inits a barrier should idle quickly."""
    sim = Sim()
    sim.load_program([BAR_INIT(0, 2)])
    sim.run_until_idle(max_cycles=10)
    assert sim.barrier.bars[0].expected == 2
    assert sim.barrier.bars[0].pending == 2


def test_load_wait_completes():
    """Push a single LOAD that signals barrier; WAIT should release."""
    sim = Sim()
    sim.load_gmem(0, b"\xab" * 1024)
    sim.load_program([
        BAR_INIT(0, 1),
        LOAD(0, gmem_ptr=0, smem_ptr=SMEM_TILE_BASE, bytes_n=1024),
        WAIT(0, expected_phase=0),
    ])
    sim.run_until_idle()
    assert sim.smem.dump(SMEM_TILE_BASE, 1024) == b"\xab" * 1024


def test_store_sync_stalls_then_completes():
    """STORE holds cmdproc until store.done."""
    sim = Sim()
    # Pre-fill TMEM via back-door
    import numpy as np
    from config import MMA_M, MMA_N
    tile = np.arange(MMA_M * MMA_N, dtype=np.float32).reshape(MMA_M, MMA_N)
    sim.tmem.set_slot(0, tile)
    sim.load_program([STORE(tmem_slot=0, gmem_ptr=0, dtype=0)])
    sim.run_until_idle()
    expected = bytes(np.ascontiguousarray(tile.astype("<f4")).tobytes())
    assert sim.gmem.dump(0, len(expected)) == expected


def test_two_loads_async_advance():
    """Two LOADs back-to-back should both complete before WAIT releases."""
    sim = Sim()
    sim.load_gmem(0, b"\x01" * 512)
    sim.load_gmem(512, b"\x02" * 512)
    sim.load_program([
        BAR_INIT(0, 2),
        LOAD(0, 0, SMEM_TILE_BASE, 512),
        LOAD(0, 512, SMEM_TILE_BASE + 512, 512),
        WAIT(0, expected_phase=0),
    ])
    sim.run_until_idle()
    assert sim.smem.dump(SMEM_TILE_BASE, 512) == b"\x01" * 512
    assert sim.smem.dump(SMEM_TILE_BASE + 512, 512) == b"\x02" * 512
