"""
adder — 8-bit + 8-bit → 9-bit registered adder, gated by `en`.

PURPOSE
    Smallest possible module that exercises the full spec → pymodel → SV
    workflow. Throwaway experiment to validate the toolchain end-to-end.

INPUTS (sampled at tick start)
    en      : 1-bit
    a       : 8-bit unsigned
    b       : 8-bit unsigned

OUTPUTS (valid after tick, registered)
    sum     : 9-bit unsigned
    valid   : 1-bit

INTERNAL STATE
    sum_q   : 9-bit, init 0
    valid_q : 1-bit, init 0

BEHAVIOR (per tick)
    if en:
        sum_q   <= (a + b) & 0x1FF     # 9-bit width, no overflow expected since 8+8 fits
        valid_q <= 1
    else:
        sum_q   <= 0
        valid_q <= 0

    Outputs are the registered values: sum = sum_q, valid = valid_q.

INVARIANTS
    - sum < 512 (fits in 9 bits)
    - valid is 0 the cycle after en goes low

HANDSHAKE
    Combinational latency-1: drive en+a+b at cycle T → sum and valid at cycle T+1.

TEST CASES (in test_pymodel.py)
    1. en=1, a=5, b=7 → sum=12, valid=1 after tick.
    2. en=0 → sum=0, valid=0 after tick.
    3. Two consecutive en=1 ticks with different inputs → second sum reflects second pair.
    4. en=1 then en=0 → valid goes 1 → 0.
    5. Boundary: a=255, b=255 → sum=510.
"""


class Adder:
    def __init__(self):
        self.sum = 0
        self.valid = 0

    def tick(self, en: int, a: int, b: int) -> None:
        """One clock tick. Outputs reflect new registered state after return."""
        assert 0 <= a < 256, "a must be 8-bit"
        assert 0 <= b < 256, "b must be 8-bit"
        assert en in (0, 1), "en is a single bit"
        if en:
            self.sum = (a + b) & 0x1FF
            self.valid = 1
        else:
            self.sum = 0
            self.valid = 0
