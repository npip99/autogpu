"""
tb_utils — shared cocotb testbench helpers.

These encapsulate the canonical compare-loop used by every Phase 4 RTL
testbench (`<sub>/tb/test_<sub>.py`). See DEVELOPMENT.md §Testing for
background, especially "Why NextTimeStep matters".

Conventions assumed:
    - dut has a `clk` signal.
    - dut has a `reset` signal (active-high, sync).
    - Port names on the SV module match attribute names on the pymodel.
      e.g. dut.sum ↔ pymodel.sum.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, NextTimeStep


async def start_clock(dut, period_ns: int = 10, signal_name: str = "clk") -> None:
    """Start a free-running clock. Returns immediately; clock runs in background."""
    cocotb.start_soon(Clock(getattr(dut, signal_name), period_ns, unit="ns").start())


async def reset(dut, signal_name: str = "reset", cycles: int = 2,
                clock_name: str = "clk") -> None:
    """Hold reset high for `cycles` clocks, then release."""
    sig = getattr(dut, signal_name)
    clk = getattr(dut, clock_name)
    sig.value = 1
    for _ in range(cycles):
        await RisingEdge(clk)
    sig.value = 0
    await RisingEdge(clk)


async def wait_until_chip_ready(dut, max_cycles: int = 1000) -> int:
    """Wait until reset_seq has finished scrubbing and chip_in_reset is low.

    Used by testbenches that go through `cmdproc_tb_top` (or any top that
    instantiates reset_seq). The external `reset` pin should already have
    been deasserted via the standard `reset()` helper — this routine just
    polls `chip_in_reset` until it drops.

    Returns the number of clock cycles waited.

    The signal is read combinationally each cycle; reset_seq drives it as
    a registered output of the top-level wrapper.
    """
    for c in range(max_cycles):
        await ReadOnly()
        if int(dut.chip_in_reset.value) == 0:
            await NextTimeStep()
            return c
        await NextTimeStep()
        await RisingEdge(dut.clk)
    raise AssertionError(
        f"chip_in_reset never went low within {max_cycles} cycles"
    )


def smem_backdoor_write_word(smem, bank: int, word: int, value: int) -> None:
    """Backdoor-write a 32-bit word into SMEM bank `bank` at per-bank index
    `word`. As of Phase 7f, SMEM banks live inside `sram_1rw` instances
    instantiated via a generate-for loop. We update BOTH the operational
    storage (gen_banks[b].u_sram.mem[word]) and the parallel `bank_mem`
    shadow exposed for backdoor reads.

    `smem` is the SMEM instance handle (e.g. dut.u_smem).
    """
    smem.gen_banks[bank].u_sram.mem[word].value = value & 0xFFFFFFFF
    smem.bank_mem[bank][word].value = value & 0xFFFFFFFF


def smem_backdoor_read_word(smem, bank: int, word: int) -> int:
    """Backdoor-read the current value of bank[bank][word]. Reads the
    shadow (which mirrors every committed write). The shadow is read-only
    from a synth perspective; here we use it as the cocotb-friendly
    observation point."""
    return int(smem.bank_mem[bank][word].value)


async def step_and_compare(dut, pymodel, inputs: dict, outputs: list) -> None:
    """One simulation cycle: drive inputs, advance clock, compare outputs to pymodel.

    Sequence:
        1. Write each (name, value) in `inputs` to dut.<name>.
        2. await RisingEdge(dut.clk).
        3. pymodel.tick(**inputs).
        4. await ReadOnly() — wait for NBAs to settle.
        5. For each name in `outputs`, assert int(dut.<name>.value) == getattr(pymodel, name).
        6. await NextTimeStep() — leave ReadOnly so the next call can write again.

    Step 6 is mandatory in cocotb 2.0: writes during ReadOnly raise RuntimeError.
    See DEVELOPMENT.md §"Why NextTimeStep matters".

    Args:
        dut:     cocotb dut handle.
        pymodel: instance with a tick(**inputs) method and attrs named for each output.
        inputs:  {sv_port_name: int_value} — same kwargs are forwarded to pymodel.tick().
        outputs: list of SV port names to compare. pymodel must have matching attrs.
    """
    for name, val in inputs.items():
        getattr(dut, name).value = val

    await RisingEdge(dut.clk)
    pymodel.tick(**inputs)

    await ReadOnly()
    for name in outputs:
        sv = int(getattr(dut, name).value)
        py = getattr(pymodel, name)
        assert sv == py, (
            f"{name} mismatch: sv={sv} py={py} inputs={inputs}"
        )
    await NextTimeStep()
