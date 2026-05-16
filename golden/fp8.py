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

import numpy as np

_E4M3_BIAS = 7
_E4M3_MAX = 448.0  # exp=15, mant=110 → 256 * 1.75
_NAN_CODE_POS = 0x7F
_NAN_CODE_NEG = 0xFF
_MAX_NORMAL_CODE_POS = 0x7E
_MAX_NORMAL_CODE_NEG = 0xFE


def decode_e4m3(b: np.ndarray) -> np.ndarray:
    b = np.asarray(b, dtype=np.uint8)
    sign = ((b >> 7) & 1).astype(np.int32)
    exp_field = ((b >> 3) & 0x0F).astype(np.int32)
    mant = (b & 0x07).astype(np.int32)

    nan_mask = (exp_field == 0xF) & (mant == 0x7)
    sub_mask = (exp_field == 0) & (mant != 0)
    norm_mask = (exp_field != 0) & ~nan_mask

    s = (1.0 - 2.0 * sign).astype(np.float32)

    # Normals: s * (1 + mant/8) * 2^(exp - bias)
    norm_val = s * (1.0 + mant.astype(np.float32) / 8.0) * np.power(
        np.float32(2.0), exp_field.astype(np.float32) - _E4M3_BIAS
    )
    # Subnormals: s * (mant/8) * 2^(1-bias) = s * mant * 2^(1-bias-3) = s * mant * 2^-9
    sub_val = s * mant.astype(np.float32) * np.float32(2.0 ** (1 - _E4M3_BIAS - 3))

    # Default: signed zero based on sign bit (handles 0x00 → +0.0, 0x80 → -0.0).
    out = s * np.float32(0.0)
    out = np.where(norm_mask, norm_val, out)
    out = np.where(sub_mask, sub_val, out)
    out = np.where(nan_mask, np.float32(np.nan), out)
    return out


# Precomputed positive-magnitude code table for encode (LUT approach).
# Codes 0x00..0x7E inclusive; 0x7F is NaN and excluded.
_POS_CODES = np.arange(0x7F, dtype=np.uint8)
_POS_VALUES = decode_e4m3(_POS_CODES)  # all >= 0, monotonically nondecreasing


def encode_e4m3(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    flat = x.ravel()
    out = np.zeros(flat.shape, dtype=np.uint8)

    for i in range(flat.size):
        v = flat[i]
        if np.isnan(v):
            out[i] = _NAN_CODE_NEG if np.signbit(v) else _NAN_CODE_POS
            continue
        is_neg = bool(np.signbit(v))
        abs_v = float(abs(float(v)))
        # Explicit saturation: for huge |v|, float32 precision loses the diff
        # between adjacent LUT entries and argmin picks 0. Clamp before lookup.
        if abs_v >= _E4M3_MAX:
            out[i] = _MAX_NORMAL_CODE_NEG if is_neg else _MAX_NORMAL_CODE_POS
            continue
        diffs = np.abs(_POS_VALUES.astype(np.float64) - abs_v)
        idx = int(np.argmin(diffs))
        out[i] = (0x80 | idx) if is_neg else idx

    return out.reshape(x.shape)
