"""
fp8 — encode/decode fp8 e4m3 ↔ fp32 (numpy).

PURPOSE
    Single source of truth for fp8 e4m3 numeric semantics. Used by:
      - golden.matmul_reference (to build expected outputs)
      - pymodel.mma            (to multiply fp8 inputs)
      - pymodel.store          (for fp32→fp8 conversion path)
      - RTL test benches       (golden comparisons in Phase 4)

fp8 E4M3 FORMAT
    bit:  [7]    [6:3]      [2:0]
          sign   4-bit exp  3-bit mantissa
    bias: 7
    representable range: ~ ±448
    no infinity. NaN encoded as exp=1111, mantissa=111 (positive and negative).
    subnormals: exp=0000 with non-zero mantissa.

PUBLIC API (to be implemented)
    encode_e4m3(x: np.ndarray[fp32]) -> np.ndarray[uint8]
        Element-wise fp32 → fp8 e4m3 byte.
        Rounding: round-to-nearest-even.
        Overflow: saturate to ±max_normal (NOT to NaN).
        NaN input → NaN output.

    decode_e4m3(b: np.ndarray[uint8]) -> np.ndarray[fp32]
        Element-wise fp8 byte → fp32.
        NaN encoding → np.nan.

INVARIANTS
    - decode(encode(x)) preserves x for any x exactly representable in e4m3.
    - encode is idempotent under decode→encode for representable values.
    - shapes pass through; arrays of any rank supported.

TEST CASES (in pymodel/tests/test_fp8.py)
    1. Round-trip: for every representable value in e4m3 (256 codes minus
       NaN duplicates), decode→encode is identity.
    2. Saturation: encode(1e10) → max_normal (positive); encode(-1e10) → -max_normal.
    3. NaN: encode(np.nan) → 0x7F or 0xFF (NaN code); decode of NaN code → np.nan.
    4. Zero: encode(0.0) → 0x00; encode(-0.0) → 0x80.
    5. Subnormals: smallest positive subnormal round-trips.
    6. Shape preservation: 2D and 3D arrays.
"""

# Implementation goes here.
