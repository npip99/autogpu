"""Tests for pymodel.cmdproc."""

# from pymodel.cmdproc import CmdProc


def test_straight_line_dispatch():
    """Push BAR.INIT, LOAD, LOAD, WAIT, MMA, WAIT, STORE; verify each engine
    receives correct issue/start signals in the right order."""
    raise NotImplementedError


def test_wait_stalls_until_done():
    """WAIT blocks dispatching subsequent instructions until barrier.wait_done."""
    raise NotImplementedError


def test_loads_async_advance():
    """Two LOADs back-to-back: cmdproc accepts both within 2 cycles without stalling."""
    raise NotImplementedError


def test_store_sync_stalls_cmdproc():
    """STORE issued; cmdproc state = WAITING_FOR_STORE_DONE until store.done pulses."""
    raise NotImplementedError


def test_idle_signal():
    """idle high exactly when FIFO empty + state IDLE + all engines not busy."""
    raise NotImplementedError
