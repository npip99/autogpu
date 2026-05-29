"""
cocotb testbench for smem.sv.

Drives smem.sv and pymodel.smem.SMEM in lockstep and asserts equality of
every registered output (rd_a_data, rd_a_valid, rd_b_data, rd_b_valid)
every cycle.

Byte packing convention (must match smem.sv):
    wr_data is BEAT_BYTES bytes packed little-endian (byte 0 in low 8 bits).
    rd_a_data is MMA_M bytes packed the same way; rd_b_data is MMA_N bytes.
    In Python: int.from_bytes(buf, "little") and int.to_bytes(n, "little").

Tests:
  1. test_directed_load_then_read_a — backdoor-load a tile, drive MMA_RD_A
     to read the first MMA_M bytes, verify exact match next cycle.
  2. test_random_vs_pymodel — ~500 random cycles, compare every output to
     pymodel cycle-by-cycle via common.tb_utils.step_and_compare.
"""

import random

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly

from common.tb_utils import start_clock, reset, step_and_compare
from config import BEAT_BYTES, MMA_M, MMA_N, SMEM_BYTES, SMEM_TILE_BASE
from pymodel.smem import SMEM


def _bytes_to_int(b: bytes) -> int:
    """Little-endian within a beat: byte 0 -> low 8 bits."""
    return int.from_bytes(b, "little")


def _int_to_bytes(n: int, nbytes: int) -> bytes:
    return int(n).to_bytes(nbytes, "little")


class SMEMAdapter:
    """Wraps pymodel.SMEM so step_and_compare sees ints on both sides.

    - wr_data arrives from inputs as an int (matching SV); we convert to
      bytes (BEAT_BYTES long) before forwarding to pymodel.tick.
    - rd_a_data / rd_b_data are exposed as ints matching the SV packing.
    - 'reset' kwarg (used by the harness) is dropped — pymodel has no reset.
    """

    def __init__(self):
        self._s = SMEM()

    def tick(self, **kwargs):
        kwargs.pop("reset", None)
        if "wr_data" in kwargs and not isinstance(kwargs["wr_data"], (bytes, bytearray)):
            kwargs["wr_data"] = _int_to_bytes(kwargs["wr_data"], BEAT_BYTES)
        self._s.tick(**kwargs)

    # Back-door for the directed test.
    def load(self, addr: int, data: bytes) -> None:
        self._s.load(addr, data)

    @property
    def rd_a_data(self) -> int:
        return _bytes_to_int(self._s.rd_a_data)

    @property
    def rd_a_valid(self) -> int:
        return int(self._s.rd_a_valid)

    @property
    def rd_b_data(self) -> int:
        return _bytes_to_int(self._s.rd_b_data)

    @property
    def rd_b_valid(self) -> int:
        return int(self._s.rd_b_valid)

    # Combinational stall outputs (per-port).
    @property
    def load_wr_stall_out(self) -> int:
        return int(self._s.load_wr_stall_out)

    @property
    def mma_rd_a_stall_out(self) -> int:
        return int(self._s.mma_rd_a_stall_out)

    @property
    def mma_rd_b_stall_out(self) -> int:
        return int(self._s.mma_rd_b_stall_out)


# Region-partition convention (post-B1):
#   region 0 = addr 0..4095   → banks 0-7  → OPERAND A
#   region 1 = addr 4096..8191 → banks 8-15 → OPERAND B
# rd_a only reads region 0; rd_b only reads region 1. LOAD writes to
# whichever region the destination address falls in. Random tests must
# respect this so the OR-tree-free read path returns valid data.
_REGION_SIZE = SMEM_BYTES // 2  # 2 regions of 4 KB each (8 KB SMEM, 16 banks)


def _rand_wr_addr(rng: random.Random, region: int = 0) -> int:
    """Random BEAT_BYTES-aligned address inside one of the 4 SMEM regions.
    Default region 0 (A's region); pass region=1 for B's region.
    """
    base = region * _REGION_SIZE
    lo = max(SMEM_TILE_BASE, base) // BEAT_BYTES
    hi = (base + _REGION_SIZE - 1) // BEAT_BYTES
    return rng.randint(lo, hi) * BEAT_BYTES


def _rand_rd_a_addr(rng: random.Random) -> int:
    """Random MMA_M-aligned address inside region 0 (A region)."""
    lo = max(SMEM_TILE_BASE, 0) // MMA_M
    hi = (_REGION_SIZE - 1) // MMA_M
    return rng.randint(lo, hi) * MMA_M


def _rand_rd_b_addr(rng: random.Random) -> int:
    """Random MMA_N-aligned address inside region 1 (B region)."""
    lo = _REGION_SIZE // MMA_N
    hi = (2 * _REGION_SIZE - 1) // MMA_N
    return rng.randint(lo, hi) * MMA_N


def _overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return not (a1 <= b0 or b1 <= a0)


def _backdoor_write_word(smem, bank: int, word: int, value: int) -> None:
    """As of the B1 smem_bank refactor, each bank is a `smem_bank` instance
    (`u_bank`) wrapping an `sram_1rw` (`u_sram`). A backdoor word write
    must update the operational SRAM storage AND the parallel `bank_mem`
    shadow used by backdoor reads.

    The operational storage path depends on which sram_1rw impl is active:
      - FF impl (default):       `u_bank.u_sram.mem[w]`
      - sky130 macro wrapper:    `u_bank.u_sram.u_macro.mem[w]`
    """
    sram = smem.gen_banks[bank].u_bank.u_sram
    masked = value & 0xFFFFFFFF
    if hasattr(sram, "mem"):
        sram.mem[word].value = masked
    else:
        sram.u_macro.mem[word].value = masked
    smem.bank_mem[bank][word].value = masked


def _backdoor_load_dut(dut, addr: int, data: bytes) -> None:
    """Write `data` bytes into the banked storage. Gathers byte updates
    per (bank, word) before issuing writes — cocotb hierarchical writes
    are NBA, so successive read-modify-write of the same word would race.
    """
    NUM_BANKS = 16

    # Region-partitioned bank decode (post-B1 refactor):
    #   bank = {addr[13:12], addr[4:2]}, word = addr[11:5]
    def _bank_of(byte_addr: int) -> int:
        # bank = {addr[12], addr[4:2]}  (1-bit region + 3-bit bank-within-region)
        return ((byte_addr >> 9) & 0x8) | ((byte_addr >> 2) & 0x7)
    def _word_of(byte_addr: int) -> int:
        return (byte_addr >> 5) & 0x7F

    # Pre-read existing words for any (bank, word) we'll touch.
    word_cache: dict[tuple[int, int], int] = {}
    for i in range(len(data)):
        byte_addr = addr + i
        bank = _bank_of(byte_addr)
        word = _word_of(byte_addr)
        key = (bank, word)
        if key not in word_cache:
            word_cache[key] = int(dut.bank_mem[bank][word].value)

    # Apply byte updates in the cache.
    for i, byte in enumerate(data):
        byte_addr = addr + i
        bank = _bank_of(byte_addr)
        word = _word_of(byte_addr)
        byte_in_dw = byte_addr & 3
        v = word_cache[(bank, word)]
        v &= ~(0xFF << (byte_in_dw * 8))
        v |= (int(byte) & 0xFF) << (byte_in_dw * 8)
        word_cache[(bank, word)] = v

    # Write one word at a time (to both shadow and sram).
    for (bank, word), v in word_cache.items():
        _backdoor_write_word(dut, bank, word, v)


@cocotb.test()
async def test_scrub_clears_all_banks(dut):
    """Drive the scrub port for NUM_WORDS_PER_BANK cycles; verify every dword
    in every bank is zero (replaces the old `initial` zero-init).
    """
    await start_clock(dut)
    # Safe defaults; we'll drive scrub_en below.
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_a_en.value = 0
    dut.rd_a_addr.value = 0
    dut.rd_b_en.value = 0
    dut.rd_b_addr.value = 0
    dut.scrub_en.value = 0
    dut.scrub_addr.value = 0
    await reset(dut)

    # Poison every bank-word with a non-zero pattern via back-door so we can
    # detect the scrub actually doing work. Writes go to both the sram_1rw
    # storage and the shadow.
    NUM_BANKS = 16
    NUM_WORDS_PER_BANK = SMEM_BYTES // NUM_BANKS // 4
    for b in range(NUM_BANKS):
        for w in range(NUM_WORDS_PER_BANK):
            _backdoor_write_word(dut, b, w, 0xDEADBEEF)

    # Wait one cycle for the back-door NBAs to commit.
    await RisingEdge(dut.clk)

    # Drive the scrub port for NUM_WORDS_PER_BANK cycles, walking scrub_addr.
    for w in range(NUM_WORDS_PER_BANK):
        dut.scrub_en.value = 1
        dut.scrub_addr.value = w
        await RisingEdge(dut.clk)
    dut.scrub_en.value = 0
    dut.scrub_addr.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()

    # Every dword in every bank must be 0 now.
    for b in range(NUM_BANKS):
        for w in range(NUM_WORDS_PER_BANK):
            v = int(dut.bank_mem[b][w].value)
            assert v == 0, f"bank[{b}][{w}] != 0 after scrub: 0x{v:08x}"


@cocotb.test()
async def test_directed_load_then_read_a(dut):
    """Backdoor-load a tile; drive MMA_RD_A; verify exact match."""
    await start_clock(dut)
    # Drive safe defaults before reset so X's don't propagate.
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_a_en.value = 0
    dut.rd_a_addr.value = 0
    dut.rd_b_en.value = 0
    dut.rd_b_addr.value = 0
    dut.scrub_en.value = 0
    dut.scrub_addr.value = 0
    await reset(dut)

    # Zero bank storage (`initial` blocks only run at sim startup; if this
    # test ran a second time on the same sim, contents could be stale).
    NUM_BANKS = 16
    NUM_WORDS_PER_BANK = SMEM_BYTES // NUM_BANKS // 4
    for b in range(NUM_BANKS):
        for w in range(NUM_WORDS_PER_BANK):
            _backdoor_write_word(dut, b, w, 0)

    # Build a deterministic tile of A_TILE_BYTES bytes at SMEM_TILE_BASE.
    # We'll read the first MMA_M bytes (one "column" worth in the spec's terms).
    tile_bytes = bytes((i + 1) & 0xFF for i in range(MMA_M))
    expected_int = _bytes_to_int(tile_bytes)
    addr = SMEM_TILE_BASE
    _backdoor_load_dut(dut, addr, tile_bytes)

    # Cycle T0: issue MMA_RD_A at addr.
    dut.rd_a_en.value = 1
    dut.rd_a_addr.value = addr
    await RisingEdge(dut.clk)

    # Cycle T1: drain. rd_a_valid should be 1 and rd_a_data should match.
    dut.rd_a_en.value = 0
    dut.rd_a_addr.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()

    sv_valid = int(dut.rd_a_valid.value)
    sv_data = int(dut.rd_a_data.value)
    assert sv_valid == 1, f"expected rd_a_valid=1, got {sv_valid}"
    assert sv_data == expected_int, (
        f"rd_a_data mismatch: sv=0x{sv_data:0{MMA_M*2}x} "
        f"expected=0x{expected_int:0{MMA_M*2}x}"
    )


@cocotb.test()
async def test_random_vs_pymodel(dut):
    """Drive ~500 random cycles; compare every registered output to pymodel."""
    await start_clock(dut)
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_a_en.value = 0
    dut.rd_a_addr.value = 0
    dut.rd_b_en.value = 0
    dut.rd_b_addr.value = 0
    dut.scrub_en.value = 0
    dut.scrub_addr.value = 0
    await reset(dut)

    # Zero bank storage to match pymodel's fresh state (reset preserves
    # memory).
    NUM_BANKS = 16
    NUM_WORDS_PER_BANK = SMEM_BYTES // NUM_BANKS // 4
    for b in range(NUM_BANKS):
        for w in range(NUM_WORDS_PER_BANK):
            _backdoor_write_word(dut, b, w, 0)

    py = SMEMAdapter()
    rng = random.Random(0xBEEF)

    outputs = [
        "rd_a_data", "rd_a_valid", "rd_b_data", "rd_b_valid",
        "load_wr_stall_out", "mma_rd_a_stall_out", "mma_rd_b_stall_out",
    ]

    for cycle in range(500):
        wr_en = rng.randint(0, 1)
        rd_a_en = rng.randint(0, 1)
        rd_b_en = rng.randint(0, 1)

        # 50/50 between region 0 (A's region, conflicts with rd_a) and
        # region 1 (B's region, conflicts with rd_b).
        wr_addr = _rand_wr_addr(rng, rng.randint(0, 1)) if wr_en else 0
        rd_a_addr = _rand_rd_a_addr(rng) if rd_a_en else 0
        rd_b_addr = _rand_rd_b_addr(rng) if rd_b_en else 0

        # Avoid the spec-illegal same-cycle wr_en + rd_*_en with OVERLAPPING
        # byte ranges (pymodel asserts). Drop the write if it overlaps either
        # active read this cycle.
        if wr_en and rd_a_en and _overlap(
            wr_addr, wr_addr + BEAT_BYTES, rd_a_addr, rd_a_addr + MMA_M
        ):
            wr_en = 0
            wr_addr = 0
        if wr_en and rd_b_en and _overlap(
            wr_addr, wr_addr + BEAT_BYTES, rd_b_addr, rd_b_addr + MMA_N
        ):
            wr_en = 0
            wr_addr = 0

        wr_data = rng.getrandbits(BEAT_BYTES * 8) if wr_en else 0

        inputs = {
            "reset": 0,
            "wr_en": wr_en,
            "wr_addr": wr_addr,
            "wr_data": wr_data,
            "rd_a_en": rd_a_en,
            "rd_a_addr": rd_a_addr,
            "rd_b_en": rd_b_en,
            "rd_b_addr": rd_b_addr,
        }

        await step_and_compare(dut, py, inputs, outputs)


# ---------------------------------------------------------------------------
# Bank-conflict random test.
# Drives addresses biased toward bank conflicts so the stall logic is heavily
# exercised. Pymodel and SV must match cycle-by-cycle on outputs + stalls.
# ---------------------------------------------------------------------------


def _rand_in_group(rng: random.Random, group: int, align: int, width: int) -> int:
    """Return a random `align`-aligned addr inside SMEM region `group`.

    Post-B1, an SMEM "group" is one of the 4 hardware regions (4 KB each,
    8 banks each, picked by addr[13:12]).
    group ∈ {0,1,2,3}. Conflicts now happen when two ports target the same
    region. Picks any aligned address in the region.
    """
    base = group * _REGION_SIZE
    lo = base // align
    hi = (base + _REGION_SIZE - 1) // align
    addr = rng.randint(lo, hi) * align
    while addr + width > base + _REGION_SIZE:
        addr -= align
    return addr


@cocotb.test()
async def test_bank_conflict_random(dut):
    """Drive 3-port traffic with frequent bank-group collisions; compare to pymodel."""
    await start_clock(dut)
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_a_en.value = 0
    dut.rd_a_addr.value = 0
    dut.rd_b_en.value = 0
    dut.rd_b_addr.value = 0
    dut.scrub_en.value = 0
    dut.scrub_addr.value = 0
    await reset(dut)

    # Reset clears the registered outputs and pending state but does NOT
    # clear bank contents (preserves memory across resets — matches the
    # gmem/tmem convention). Since the previous test left random data
    # behind, we zero contents explicitly so both pymodel and DUT start
    # with the same all-zero memory.
    NUM_BANKS = 16
    NUM_WORDS_PER_BANK = SMEM_BYTES // NUM_BANKS // 4
    for b in range(NUM_BANKS):
        for w in range(NUM_WORDS_PER_BANK):
            _backdoor_write_word(dut, b, w, 0)

    py = SMEMAdapter()
    rng = random.Random(0xC0FFEE)

    outputs = [
        "rd_a_data", "rd_a_valid", "rd_b_data", "rd_b_valid",
        "load_wr_stall_out", "mma_rd_a_stall_out", "mma_rd_b_stall_out",
    ]

    for cycle in range(500):
        wr_en = rng.randint(0, 1)
        rd_a_en = rng.randint(0, 1)
        rd_b_en = rng.randint(0, 1)

        # Post-B1 region-partitioned smem: rd_a is locked to region 0,
        # rd_b to region 1. Conflicts now happen when LOAD's wr_addr lands
        # in the same region as one of the reads.
        # ~70% of cycles: force wr_addr into rd_a's or rd_b's region to
        # provoke a conflict.
        if rng.random() < 0.7:
            wr_region = rng.randint(0, 1)
            wr_addr   = _rand_in_group(rng, wr_region, BEAT_BYTES, BEAT_BYTES) if wr_en else 0
            rd_a_addr = _rand_in_group(rng, 0, MMA_M, MMA_M) if rd_a_en else 0
            rd_b_addr = _rand_in_group(rng, 1, MMA_N, MMA_N) if rd_b_en else 0
        else:
            wr_addr   = _rand_wr_addr(rng, rng.randint(0, 1)) if wr_en else 0
            rd_a_addr = _rand_rd_a_addr(rng) if rd_a_en else 0
            rd_b_addr = _rand_rd_b_addr(rng) if rd_b_en else 0

        # Pymodel still asserts on BYTE-overlap (a strict subset of bank
        # conflict). Drop the write if its byte range overlaps either read.
        if wr_en and rd_a_en and _overlap(
            wr_addr, wr_addr + BEAT_BYTES, rd_a_addr, rd_a_addr + MMA_M
        ):
            wr_en = 0
            wr_addr = 0
        if wr_en and rd_b_en and _overlap(
            wr_addr, wr_addr + BEAT_BYTES, rd_b_addr, rd_b_addr + MMA_N
        ):
            wr_en = 0
            wr_addr = 0

        wr_data = rng.getrandbits(BEAT_BYTES * 8) if wr_en else 0

        inputs = {
            "reset": 0,
            "wr_en": wr_en,
            "wr_addr": wr_addr,
            "wr_data": wr_data,
            "rd_a_en": rd_a_en,
            "rd_a_addr": rd_a_addr,
            "rd_b_en": rd_b_en,
            "rd_b_addr": rd_b_addr,
        }
        await step_and_compare(dut, py, inputs, outputs)
