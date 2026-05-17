// fp8_encode.sv -- combinational fp32 IEEE 754 bits -> fp8 e4m3 byte.
//
// Mirrors golden.fp8.encode_e4m3, which is the project's canonical encoder.
//
//   - NaN (fp32 exp=0xFF, mant!=0)         -> 0x7F (positive) / 0xFF (negative)
//   - Inf OR |v| >= 448 (e4m3 max normal)  -> 0x7E / 0xFE (saturate)
//   - 0 / true fp32 subnormal              -> 0x00 / 0x80 (signed zero)
//   - otherwise: rounded e4m3 byte
//
// Rounding semantics:
//   golden.fp8.encode_e4m3 uses np.argmin over the 127 positive
//   magnitudes; on exact ties it returns the lowest index, which is
//   the smaller-magnitude representable. That is "round to nearest,
//   ties toward zero" -- NOT IEEE-754 round-to-nearest-even. We match
//   golden exactly so cocotb tests (bit-exact vs golden) pass.
//
//   Random fp32 inputs almost never land exactly on the midpoint
//   between two e4m3 representable values, so RNE vs ties-toward-zero
//   only differ on a small set of constructed inputs.
//
// Implementation: pure combinational bit-twiddling (no `real`, no LUT).
//   1. Decompose fp32 -> sign / fp_exp (biased) / fp_mant (23 bits).
//   2. Handle NaN / Inf / zero / fp32-subnormal early outs.
//   3. e_unb = fp_exp - 127. Branch by e_unb range:
//        e_unb >= 9   : saturate (value >= 512 >> 448).
//        -6 <= e_unb <= 8 : normal e4m3 path; mantissa = round(fp_mant[22:0] / 2^20).
//        -9 <= e_unb <= -7: subnormal e4m3 path; mantissa derived from sig24.
//        e_unb <= -10 : value <= 2^-10 = 0.5 * e4m3 LSB -> rounds to 0.
//                       (ties-toward-zero: exactly 2^-10 also rounds to 0.)
//   4. Rounding (ties-toward-zero):
//        Keep the bit immediately below the LSB ("half_bit") and the OR
//        of all bits further below ("sticky_bit"). round_up =
//        half_bit && sticky_bit.

module fp8_encode (
    input  logic [31:0] fp32,
    output logic [7:0]  fp8
);

    // ---- Decompose fp32 -----------------------------------------------
    logic        sign_bit;
    logic [7:0]  fp_exp;
    logic [22:0] fp_mant;

    assign sign_bit = fp32[31];
    assign fp_exp   = fp32[30:23];
    assign fp_mant  = fp32[22:0];

    // ---- Special cases ------------------------------------------------
    logic is_nan;
    logic is_inf;
    logic fp_zero_or_sub;   // fp32 exp == 0 -> magnitude < 2^-126, round to 0.

    assign is_nan         = (fp_exp == 8'hFF) && (fp_mant != 23'd0);
    assign is_inf         = (fp_exp == 8'hFF) && (fp_mant == 23'd0);
    assign fp_zero_or_sub = (fp_exp == 8'h00);

    // ---- Working values -----------------------------------------------
    logic signed [9:0] e_unb;
    logic [23:0]       sig24;   // {1'b1, fp_mant} -- full 24-bit significand.

    assign e_unb = $signed({2'b00, fp_exp}) - 10'sd127;
    assign sig24 = {1'b1, fp_mant};

    // ---- Normal-range path (target e4m3 normal, exp_field 1..15) ----
    //   value = sig24 * 2^(e_unb - 23)
    //   e4m3 normal repr:   value = (1 + m3/8) * 2^(exp_field - 7)
    //                       = sig24_e4m3 * 2^(exp_field - 7 - 3)
    //                     where sig24_e4m3 = 8 + m3 (4 bits).
    //   ⇒ sig24_e4m3 = sig24 / 2^20 with proper rounding (drop 20 LSBs).
    //   ⇒ exp_field  = e_unb + 7.
    //
    //   Range of "in-range" exp_field: [1, 15] -> e_unb in [-6, 8].
    //   For e_unb = 8: max value = (1 + 6/8) * 256 = 448 -> max_normal.
    //                  Values 448 <= v < 512 saturate; exactly 448
    //                  saturates per golden (>= 448 condition) -- but
    //                  448 itself encodes as max_normal regardless.
    //                  Note: a borderline v in (448, 512) would map to
    //                  m3=7 with this normal path (NaN code); we
    //                  intercept it via the saturation check below.

    // Normal-path rounding: drop low 20 bits of fp_mant (the 23-bit
    // fractional part). The high 3 bits become m3; bits below the
    // high 3 are rounding bits.
    //   m3_raw     = fp_mant[22:20]
    //   half_bit_n = fp_mant[19]
    //   sticky_n   = | fp_mant[18:0]
    //   round_up_n = half_bit_n && sticky_n
    //   m3_rnd     = m3_raw + round_up_n   (4 bits to detect overflow)
    logic [2:0] m3_raw;
    logic       half_bit_n;
    logic       sticky_n;
    logic       round_up_n;
    logic [3:0] m3_rnd;       // up to 4 bits if overflow into "8"

    assign m3_raw      = fp_mant[22:20];
    assign half_bit_n  = fp_mant[19];
    assign sticky_n    = |fp_mant[18:0];
    assign round_up_n  = half_bit_n && sticky_n;
    assign m3_rnd      = {1'b0, m3_raw} + {3'd0, round_up_n};

    // If m3_rnd overflows to 8, bump exp_field. (Only happens when
    // m3_raw = 7 and rounding rounds up -- which here requires
    // sticky_n=1, so fp_mant[19:0] > 0x80000.)
    logic       m3_overflow_n;
    logic [3:0] norm_exp_field;
    logic [2:0] norm_m3;
    assign m3_overflow_n  = m3_rnd[3];
    // e_unb in normal-path range is [-6, 8]. Add 7 (or 8 on mant overflow)
    // to get the e4m3 exp_field. We compute in a wide signed register and
    // narrow afterwards; saturation logic intercepts the overflow case.
    logic signed [9:0] norm_exp_signed;
    assign norm_exp_signed = e_unb + (m3_overflow_n ? 10'sd8 : 10'sd7);
    assign norm_exp_field = norm_exp_signed[3:0];
    assign norm_m3        = m3_overflow_n ? 3'd0 : m3_rnd[2:0];

    // Saturation: e_unb >= 9 always saturates. e_unb == 8 with rounded
    // mantissa >= 7 (corresponds to v >= 480 pre-round) also saturates --
    // BUT golden saturates at v >= 448, which corresponds to a slightly
    // wider window. We catch that via the dedicated 448 check below.
    //
    // Specifically: at e_unb=8, sig24 = 1.mmm... * 2^23. For v=448,
    // sig24 = 1.110_0000_..._0 * 2^23 = 0xE00000. We saturate when
    // sig24 >= 0xE00000 at e_unb=8 (i.e., when fp_mant[22:20] >= 6 AND
    // (m3==6 case: only saturate if v > 448, i.e., low bits > 0)).
    //
    // The cleanest test: after computing norm_m3 and norm_exp_field,
    // saturate if norm_exp_field > 15 or (norm_exp_field == 15 AND
    // norm_m3 >= 7).

    // ---- Subnormal-range path (-9 <= e_unb <= -7) --------------------
    //   value = sig24 * 2^(e_unb - 23)
    //   target: m3 * 2^-9 with m3 in [1, 7]
    //   ⇒ m3 = value * 2^9 = sig24 * 2^(e_unb - 14)
    //
    //   For e_unb = -7: m3 = sig24 >> 21
    //                   half_bit = sig24[20], sticky = | sig24[19:0]
    //   For e_unb = -8: m3 = sig24 >> 22
    //                   half_bit = sig24[21], sticky = | sig24[20:0]
    //   For e_unb = -9: m3 = sig24 >> 23 = 1 (always)
    //                   half_bit = sig24[22], sticky = | sig24[21:0]
    //                   (since bit 22 is part of the 1.0 .. <2.0 range,
    //                    this rounds 1.x * 2^-9 to either 1 or 2.)
    //
    //   m3 may round up to 8, which means the value crossed into
    //   normal-e4m3 territory -> emit normal exp_field=1, m3=0.

    // Compute right-shift amount sh_sub for the subnormal path:
    //   sh_sub = 14 - e_unb  (positive: 21..23)
    // half-bit position = sh_sub - 1
    // sticky-bits = sig24 & ((1 << (sh_sub - 1)) - 1)
    logic [4:0] sh_sub;
    // sh_sub = 14 - e_unb, valid in [21, 23] when e_unb in [-9, -7].
    logic signed [9:0] sh_sub_signed;
    assign sh_sub_signed = 10'sd14 - e_unb;
    assign sh_sub = sh_sub_signed[4:0];
    // sh_sub here is 21, 22, or 23.

    logic [23:0] sig_shifted_sub;
    logic        half_bit_s;
    logic        sticky_s;
    logic [23:0] half_mask_sub;
    logic [23:0] low_mask_sub;

    assign sig_shifted_sub = sig24 >> sh_sub;
    assign half_mask_sub   = 24'd1 << (sh_sub - 5'd1);
    assign low_mask_sub    = half_mask_sub - 24'd1;
    assign half_bit_s      = |(sig24 & half_mask_sub);
    assign sticky_s        = |(sig24 & low_mask_sub);

    logic       round_up_s;
    logic [3:0] m3_sub_rnd;    // up to 4 bits to detect overflow
    assign round_up_s = half_bit_s && sticky_s;
    assign m3_sub_rnd = {1'b0, sig_shifted_sub[2:0]} + {3'd0, round_up_s};

    // If overflow to 8, promote to normal exp_field=1, m3=0.
    logic       sub_overflow;
    logic [3:0] sub_exp_field;
    logic [2:0] sub_m3;
    assign sub_overflow  = m3_sub_rnd[3];
    assign sub_exp_field = sub_overflow ? 4'd1 : 4'd0;
    assign sub_m3        = sub_overflow ? 3'd0 : m3_sub_rnd[2:0];

    // ---- Sub-subnormal range (e_unb <= -10): round to 0 or to 1 ----
    //   value = sig24 * 2^(e_unb - 23). e4m3 LSB = 2^-9. Half-LSB = 2^-10.
    //   For e_unb = -10: value = sig24 * 2^-33 = (1.m) * 2^-10
    //                    sig24 always >= 2^23 so value in [2^-10, 2^-9).
    //                    Strictly above half-LSB iff fp_mant != 0 (then
    //                    sticky=1). half_bit=1 always (since value >=
    //                    2^-10). With ties-toward-zero, round up to
    //                    subnormal_1 iff fp_mant != 0.
    //   For e_unb <= -11: value < 2^-10 strictly -> rounds to 0.
    logic round_to_one_lt10;
    assign round_to_one_lt10 = (e_unb == -10'sd10) && (fp_mant != 23'd0);

    // ---- Saturation check (matches golden's |v| >= 448 semantics) ---
    //   sig24 >= 0xE00000 AND e_unb == 8                      -> saturate
    //   e_unb >= 9                                            -> saturate
    //   is_inf                                                -> saturate
    //   normal-path overflow (norm_exp_field >= 16, or
    //                         norm_exp_field == 15 && m3 == 7) -> saturate
    //
    // The norm-path overflow check folds in the sig24 >= 0xE00000
    // case: at e_unb = 8, sig24 = 0xE00000 (= v=448) yields m3=6 with
    // no rounding, so we'd encode 0x7E (max_normal) NOT saturate.
    // golden's `>= 448` condition encodes v=448 as max_normal anyway,
    // so the result matches. For v > 448 at e_unb=8: sig24 >
    // 0xE00000, which after rounding could give m3=7 (NaN-code) or
    // overflow to m3=0, exp=16 -- both of which the saturation check
    // catches.
    //   Saturation cases (all assume non-NaN, non-fp32-subnormal):
    //     - is_inf                           : saturate
    //     - e_unb >= 9 (i.e. v >= 512)       : saturate
    //     - e_unb == 8 AND m3_rnd >= 7       : saturate
    //       (includes mantissa-overflow into "8" too; m3_rnd is 4 bits
    //        so m3_rnd >= 7 covers both 7 and 8.)
    logic saturate;
    assign saturate = is_inf
                   || (e_unb >= 10'sd9)
                   || ((e_unb == 10'sd8) && (m3_rnd >= 4'd7));

    // ---- Compose the magnitude (low 7 bits of fp8 byte) -------------
    logic [3:0] exp_field;
    logic [2:0] m3;
    logic [6:0] mag;

    always_comb begin
        if (saturate) begin
            // 0x7E = exp_field=15, m3=6 -> v=448
            exp_field = 4'd15;
            m3        = 3'd6;
        end else if (e_unb >= -10'sd6) begin
            // Normal path
            exp_field = norm_exp_field;
            m3        = norm_m3;
        end else if (e_unb >= -10'sd9) begin
            // Subnormal path
            exp_field = sub_exp_field;
            m3        = sub_m3;
        end else if (round_to_one_lt10) begin
            // Round up to smallest subnormal (m3=1).
            exp_field = 4'd0;
            m3        = 3'd1;
        end else begin
            // Round to zero.
            exp_field = 4'd0;
            m3        = 3'd0;
        end
        mag = {exp_field, m3};
    end

    // ---- Final byte ---------------------------------------------------
    always_comb begin
        if (is_nan) begin
            fp8 = sign_bit ? 8'hFF : 8'h7F;
        end else if (fp_zero_or_sub) begin
            // fp32 +/-0 or fp32 subnormal (always rounds to e4m3 zero).
            fp8 = {sign_bit, 7'd0};
        end else begin
            fp8 = {sign_bit, mag};
        end
    end

endmodule
