"""Tests for pymodel.reset_seq."""

from pymodel.reset_seq import NUM_WORDS_PER_BANK, ResetPhase, ResetSeq


def _step(r: ResetSeq, reset_in: int) -> dict:
    """Advance one cycle and snapshot the registered outputs."""
    r.tick(reset_in=reset_in)
    return {
        "chip_in_reset": r.chip_in_reset,
        "smem_scrub_en": r.smem_scrub_en,
        "smem_scrub_addr": r.smem_scrub_addr,
        "tmem_scrub_en": r.tmem_scrub_en,
        "scrub_done": r.scrub_done,
        "phase": r._phase,
    }


def test_holds_in_reset_during_scrub():
    """chip_in_reset stays high for every cycle of the scrub window."""
    r = ResetSeq()

    # Hold reset_in=1 for a few cycles.
    for _ in range(3):
        out = _step(r, reset_in=1)
        assert out["chip_in_reset"] == 1
        assert out["smem_scrub_en"] == 0
        assert out["tmem_scrub_en"] == 0
        assert out["scrub_done"] == 0

    # Release reset_in. Scrub begins; chip_in_reset stays high for the
    # entire NUM_WORDS_PER_BANK-cycle scrub window.
    for cycle in range(NUM_WORDS_PER_BANK):
        out = _step(r, reset_in=0)
        assert out["chip_in_reset"] == 1, (
            f"cycle {cycle}: chip_in_reset must stay high during scrub"
        )
        assert out["smem_scrub_en"] == 1


def test_releases_after_scrub_complete():
    """chip_in_reset goes low exactly after NUM_WORDS_PER_BANK scrub cycles.

    Timing: with reset_in=1 held last cycle, the FSM is in S_RESET. The first
    cycle of reset_in=0 commits the first scrub write at addr=0. Each
    subsequent cycle commits the next addr. After NUM_WORDS_PER_BANK cycles
    in S_SCRUB (commits addr=0..depth-1), the next cycle transitions to
    S_RUN with chip_in_reset=0 and scrub_done=1.
    """
    r = ResetSeq()

    _step(r, reset_in=1)  # phase=RESET, chip_in_reset=1

    # NUM_WORDS_PER_BANK scrub cycles: each commits one bank-word index.
    for i in range(NUM_WORDS_PER_BANK):
        out = _step(r, reset_in=0)
        assert out["chip_in_reset"] == 1, f"cycle {i}: chip_in_reset must stay high"
        assert out["smem_scrub_en"] == 1
        assert out["scrub_done"] == 0
        assert out["smem_scrub_addr"] == i

    # Cycle NUM_WORDS_PER_BANK+1: transition to RUN.
    out = _step(r, reset_in=0)
    assert out["chip_in_reset"] == 0
    assert out["smem_scrub_en"] == 0
    assert out["scrub_done"] == 1
    assert out["phase"] == ResetPhase.S_RUN

    # Subsequent cycles: stay in RUN.
    for _ in range(5):
        out = _step(r, reset_in=0)
        assert out["chip_in_reset"] == 0
        assert out["scrub_done"] == 1


def test_reset_in_reasserts_during_scrub():
    """Driving reset_in high mid-scrub restarts the FSM at scrub_addr=0."""
    r = ResetSeq()

    _step(r, reset_in=1)
    # Advance partway through the scrub.
    for _ in range(NUM_WORDS_PER_BANK // 2):
        out = _step(r, reset_in=0)
        assert out["smem_scrub_en"] == 1

    # Re-assert reset_in.
    out = _step(r, reset_in=1)
    assert out["chip_in_reset"] == 1
    assert out["smem_scrub_en"] == 0
    assert out["smem_scrub_addr"] == 0
    assert out["phase"] == ResetPhase.S_RESET

    # Release. Scrub should restart at addr=0, then walk to depth-1.
    seen_addrs = []
    for _ in range(NUM_WORDS_PER_BANK):
        out = _step(r, reset_in=0)
        if out["smem_scrub_en"] == 1:
            seen_addrs.append(out["smem_scrub_addr"])

    assert seen_addrs == list(range(NUM_WORDS_PER_BANK)), (
        f"expected fresh 0..{NUM_WORDS_PER_BANK - 1}, got {seen_addrs}"
    )


def test_scrub_writes_each_smem_addr_exactly_once():
    """During one reset event, smem_scrub_addr walks 0..depth-1 in order."""
    r = ResetSeq()
    _step(r, reset_in=1)

    seen = []
    for _ in range(NUM_WORDS_PER_BANK + 5):
        out = _step(r, reset_in=0)
        if out["smem_scrub_en"] == 1:
            seen.append(out["smem_scrub_addr"])

    assert seen == list(range(NUM_WORDS_PER_BANK)), (
        f"smem_scrub_addr sequence wrong: {seen}"
    )


def test_tmem_scrub_one_cycle():
    """tmem_scrub_en pulses for exactly one cycle per reset event."""
    r = ResetSeq()
    _step(r, reset_in=1)

    pulses = []
    for _ in range(NUM_WORDS_PER_BANK + 5):
        out = _step(r, reset_in=0)
        pulses.append(out["tmem_scrub_en"])

    assert sum(pulses) == 1, f"expected exactly one tmem_scrub pulse, got {sum(pulses)}: {pulses}"
    # The pulse should land on the FIRST scrub cycle (right after reset
    # deasserts).
    assert pulses[0] == 1
    assert all(p == 0 for p in pulses[1:])


def test_second_reset_event_pulses_tmem_again():
    """A second reset event re-arms tmem_scrub_en for one cycle."""
    r = ResetSeq()
    _step(r, reset_in=1)
    # Complete the first scrub.
    for _ in range(NUM_WORDS_PER_BANK):
        _step(r, reset_in=0)

    # Re-arm with a second reset.
    _step(r, reset_in=1)
    out = _step(r, reset_in=0)
    assert out["tmem_scrub_en"] == 1
    # Next cycles: no more pulses.
    for _ in range(NUM_WORDS_PER_BANK - 1):
        out = _step(r, reset_in=0)
        assert out["tmem_scrub_en"] == 0
