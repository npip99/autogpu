"""
reset_seq — power-on reset sequencer and on-chip memory scrubber.

PURPOSE
    On real silicon, flip-flops and SRAM cells power up in indeterminate (X)
    states. The `initial begin ... end` blocks we use during simulation to
    zero memory contents are simulation-only and don't exist in synthesized
    hardware. This module replaces those `initial` blocks with a real reset
    sequence: hold the rest of the chip in reset while we walk every on-chip
    SRAM and write zeros, then release the pipeline.

    GMEM is OFF-CHIP and is NOT scrubbed by this module. GMEM is part of the
    TB environment (represents external DRAM); only on-chip memories (SMEM,
    TMEM, plus any future fpnew pipeline regs) are scrubbed here.

SCRUB DESIGN CHOICE
    SMEM has 16 banks of 128 dwords each (NUM_WORDS_PER_BANK = SMEM_BYTES /
    NUM_BANKS / 4 = 8192 / 16 / 4 = 128 dwords/bank, 2048 dwords / 8192B
    total). The scrub port drives all 16 banks in parallel via a separate
    write port that's not the LOAD_WR port (LOAD_WR only writes 4 banks per
    cycle). Each scrub cycle writes one per-bank word index across all 16
    banks. Total scrub depth = NUM_WORDS_PER_BANK = 128 cycles. After the
    final cycle, every dword in every bank has been written to 0.

    TMEM is composed of flip-flops (one per MAC cell × TMEM_SLOTS). FFs
    accept unlimited parallel writes, so a single pulse of `tmem_scrub_en`
    clears all MMA_M * MMA_N * TMEM_SLOTS cells in one cycle.

    Because SMEM is the bottleneck, the FSM is sized to SMEM's scrub depth.
    TMEM is asserted once during the first scrub cycle and that's it — the
    sequencer keeps `tmem_scrub_en=0` for the remaining cycles.

CONFIG
    The scrub depth (number of per-bank word indices to walk) is derived
    from SMEM_BYTES at construction time, matching the SMEM banking model.

INPUTS (sampled at tick start)
    reset_in : 1-bit — external reset pin. Active-high. Assumed already
               synchronized to clk (async handling is a later phase). Held
               high re-arms the sequencer.

OUTPUTS (registered)
    chip_in_reset   : 1-bit — high while either reset_in is held or the
                      scrub is in progress. Drives the `reset` input of
                      every other on-chip module.
    smem_scrub_en   : 1-bit — high during scrub cycles to gate SMEM's
                      scrub-write port.
    smem_scrub_addr : log2(NUM_WORDS_PER_BANK)-bit — per-bank word index for
                      the parallel scrub write. Walks 0..NUM_WORDS_PER_BANK-1.
    tmem_scrub_en   : 1-bit — pulses high for exactly the first scrub cycle
                      after reset_in deasserts. Drives a parallel clear of
                      all TMEM cells.
    scrub_done      : 1-bit — convenience output, equal to ~chip_in_reset.
                      High once the scrub has completed at least once and
                      reset_in is not asserted.

INTERNAL STATE
    phase       : enum {S_RESET, S_SCRUB, S_RUN}
    scrub_addr  : int — current per-bank word index, 0..NUM_WORDS_PER_BANK-1
    first_scrub : bool — track whether this is the first scrub cycle (used
                  to pulse tmem_scrub_en for exactly one cycle).

BEHAVIOR (per tick, two-phase)
    sample : capture reset_in.

    commit :
        if reset_in == 1:
            phase <= S_RESET; scrub_addr <= 0; first_scrub <= 1
            chip_in_reset <= 1; smem_scrub_en <= 0; smem_scrub_addr <= 0
            tmem_scrub_en <= 0; scrub_done <= 0
            return

        # reset_in == 0 from here on.
        if phase == S_RESET:
            # Begin scrub on next cycle.
            phase <= S_SCRUB; scrub_addr <= 0; first_scrub <= 1
            chip_in_reset <= 1; smem_scrub_en <= 1; smem_scrub_addr <= 0
            tmem_scrub_en <= 1; first_scrub <= 0; scrub_done <= 0
        elif phase == S_SCRUB:
            if scrub_addr == NUM_WORDS_PER_BANK - 1:
                # Final scrub cycle: drive last addr, then transition to RUN.
                phase <= S_RUN
                chip_in_reset <= 0; smem_scrub_en <= 0; smem_scrub_addr <= 0
                tmem_scrub_en <= 0; scrub_done <= 1
            else:
                scrub_addr <= scrub_addr + 1
                chip_in_reset <= 1; smem_scrub_en <= 1
                smem_scrub_addr <= scrub_addr + 1
                tmem_scrub_en <= 0
        elif phase == S_RUN:
            chip_in_reset <= 0; smem_scrub_en <= 0; smem_scrub_addr <= 0
            tmem_scrub_en <= 0; scrub_done <= 1

INVARIANTS
    - smem_scrub_addr in [0, NUM_WORDS_PER_BANK) whenever smem_scrub_en=1.
    - chip_in_reset=1 whenever smem_scrub_en=1 (the rest of the chip is
      held in reset for the duration of the scrub).
    - tmem_scrub_en pulses for at most one cycle per reset event.
    - scrub_done == !chip_in_reset on any cycle after the FIRST scrub
      completion.
    - reset_in re-asserting at any point restarts the FSM from S_RESET and
      resets scrub_addr to 0 — partially-scrubbed memory may be left
      non-zero, which is fine: the next scrub will re-zero it.

HANDSHAKE
    Pin-level reset (reset_in) is the only input. The output `chip_in_reset`
    is the "system reset" seen by every on-chip module. Consumers see:
        reset_in deasserts at cycle T
        chip_in_reset stays 1 through cycles T..T+NUM_WORDS_PER_BANK
        chip_in_reset goes 0 at cycle T+NUM_WORDS_PER_BANK+1

    During the scrub window, the SMEM scrub port is driven each cycle; the
    TMEM scrub pulse is driven only on the first scrub cycle. Other inputs
    to SMEM/TMEM are silently ignored — chip_in_reset gates the upstream
    engines, so no LOAD_WR / MMA_RD / etc. requests appear during scrub.

TEST CASES (pymodel/tests/test_reset_seq.py)
    1. holds_in_reset_during_scrub — chip_in_reset stays high for the
       entire scrub window after reset_in deasserts.
    2. releases_after_scrub_complete — chip_in_reset goes low after
       NUM_WORDS_PER_BANK scrub cycles; scrub_done rises.
    3. reset_in_reasserts_during_scrub — driving reset_in high mid-scrub
       restarts the FSM at scrub_addr=0.
    4. scrub_writes_each_smem_addr_exactly_once — observed smem_scrub_addr
       sequence is 0, 1, 2, ..., NUM_WORDS_PER_BANK-1.
    5. tmem_scrub_one_cycle — tmem_scrub_en is high for exactly one cycle
       across an entire reset event.
"""

from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from config import SMEM_BYTES


# Match SMEM's banking constants. Kept in sync with smem.sv localparams.
_NUM_BANKS = 32
_BYTES_PER_DWORD = 4
NUM_WORDS_PER_BANK = SMEM_BYTES // _NUM_BANKS // _BYTES_PER_DWORD


class ResetPhase(IntEnum):
    S_RESET = 0
    S_SCRUB = 1
    S_RUN = 2


class ResetSeqConfig(BaseModel):
    """Configuration for the reset sequencer.

    `scrub_depth` is the number of cycles required to scrub the entire
    on-chip SMEM. It equals NUM_WORDS_PER_BANK because the scrub port
    drives all 16 banks in parallel.
    """

    model_config = ConfigDict(frozen=True)

    scrub_depth: int = Field(default=NUM_WORDS_PER_BANK, gt=0)


class ResetSeq:
    def __init__(self, config: Optional[ResetSeqConfig] = None) -> None:
        self.config = config or ResetSeqConfig()
        # Internal state (post-reset defaults: held in S_RESET).
        self._phase: ResetPhase = ResetPhase.S_RESET
        self._scrub_addr: int = 0
        # Registered outputs (defaults match S_RESET behavior).
        self.chip_in_reset: int = 1
        self.smem_scrub_en: int = 0
        self.smem_scrub_addr: int = 0
        self.tmem_scrub_en: int = 0
        self.scrub_done: int = 0

    def tick(self, reset_in: int = 0) -> None:
        depth = self.config.scrub_depth

        if reset_in:
            self._phase = ResetPhase.S_RESET
            self._scrub_addr = 0
            self.chip_in_reset = 1
            self.smem_scrub_en = 0
            self.smem_scrub_addr = 0
            self.tmem_scrub_en = 0
            self.scrub_done = 0
            return

        if self._phase == ResetPhase.S_RESET:
            # Begin scrubbing. This cycle drives addr=0 for both SMEM and
            # pulses the parallel TMEM clear.
            self._phase = ResetPhase.S_SCRUB
            self._scrub_addr = 0
            self.chip_in_reset = 1
            self.smem_scrub_en = 1
            self.smem_scrub_addr = 0
            self.tmem_scrub_en = 1
            self.scrub_done = 0
        elif self._phase == ResetPhase.S_SCRUB:
            # In S_SCRUB the previous cycle drove smem_scrub_addr = self._scrub_addr
            # (committed). This cycle either advances to the next addr or
            # transitions to S_RUN if we've already written the final addr.
            if self._scrub_addr >= depth - 1:
                # We just committed scrub_addr = depth-1 last cycle. Now
                # release: chip_in_reset drops, scrub_en drops, RUN starts.
                self._phase = ResetPhase.S_RUN
                self._scrub_addr = 0
                self.chip_in_reset = 0
                self.smem_scrub_en = 0
                self.smem_scrub_addr = 0
                self.tmem_scrub_en = 0
                self.scrub_done = 1
            else:
                self._scrub_addr += 1
                self.chip_in_reset = 1
                self.smem_scrub_en = 1
                self.smem_scrub_addr = self._scrub_addr
                self.tmem_scrub_en = 0
                self.scrub_done = 0
        else:  # S_RUN
            self.chip_in_reset = 0
            self.smem_scrub_en = 0
            self.smem_scrub_addr = 0
            self.tmem_scrub_en = 0
            self.scrub_done = 1
