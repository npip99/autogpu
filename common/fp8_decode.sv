// fp8_decode.sv -- combinational fp8 e4m3 byte -> fp32 IEEE 754 bits.
//
// Mirrors golden.fp8.decode_e4m3:
//   bits [7]    = sign
//   bits [6:3]  = exp_field (4-bit, bias 7)
//   bits [2:0]  = mantissa (3-bit)
//
//   NaN       (exp=0xF, mant=0x7)            -> fp32 qNaN  (0x7FC00000 / 0xFFC00000)
//   Subnormal (exp=0,   mant!=0)             -> mant * 2^-9 (with sign)
//   Zero      (exp=0,   mant=0)              -> +/- 0
//   Normal    otherwise                      -> (-1)^s * (1 + mant/8) * 2^(exp-7)
//
// Pure combinational. No `real` is used.

module fp8_decode (
    input  logic [7:0]  fp8,
    output logic [31:0] fp32
);

    logic        sign;
    logic [3:0]  exp_field;
    logic [2:0]  mant;
    logic        is_nan;
    logic        is_zero;
    logic        is_subnormal;
    logic [7:0]  fp32_exp;
    logic [22:0] fp32_mant;
    logic [31:0] fp32_normal;
    logic [31:0] fp32_subnormal;
    // Subnormal normalization helpers.
    logic [2:0]  sub_mant;
    logic [4:0]  sub_lz;          // leading zeroes in sub_mant (0..3)
    logic [4:0]  sub_shift;       // amount to shift left to normalize
    logic signed [9:0] sub_exp;   // unbiased exponent of the subnormal value
    logic [2:0]  sub_mant_norm;   // mantissa after normalization

    assign sign      = fp8[7];
    assign exp_field = fp8[6:3];
    assign mant      = fp8[2:0];

    assign is_nan       = (exp_field == 4'hF) && (mant == 3'h7);
    assign is_zero      = (exp_field == 4'h0) && (mant == 3'h0);
    assign is_subnormal = (exp_field == 4'h0) && (mant != 3'h0);

    // --- Normal path ---------------------------------------------------
    //   fp32 unbiased exponent = (exp_field - 7), fp32 biased = (exp_field + 120)
    //   fp32 mantissa = mant << (23 - 3) = mant << 20
    assign fp32_exp    = {4'b0, exp_field} + 8'd120;
    assign fp32_mant   = {mant, 20'd0};
    assign fp32_normal = {sign, fp32_exp, fp32_mant};

    // --- Subnormal path ------------------------------------------------
    //   e4m3 subnormal value = mant * 2^-9, mant in {1..7}.
    //   Normalize by finding leading-zero count lz of the 3-bit mant
    //   (lz in {0, 1, 2}):
    //     lz=0 (mant=4..7): value = mant * 2^-9 = (mant/4) * 2^-7
    //                       so (1 + frac) * 2^-7, frac = (mant&3)/4
    //                       => fp32 biased exp = 127 - 7 = 120
    //                          fp32 mantissa[22:21] = mant[1:0]
    //     lz=1 (mant=2..3): value = (mant/2) * 2^-8 = (1 + frac) * 2^-8,
    //                       frac = (mant&1)/2
    //                       => fp32 biased exp = 119
    //                          fp32 mantissa[22] = mant[0]
    //     lz=2 (mant=1):    value = 1 * 2^-9 = 1.0 * 2^-9
    //                       => fp32 biased exp = 118, mantissa = 0
    //   General rule: biased_exp = 120 - lz, mantissa = mant << (21 + lz)
    //                  with the implicit leading 1 dropped.
    assign sub_mant = mant;
    always_comb begin
        casez (sub_mant)
            3'b1??:  sub_lz = 5'd0;
            3'b01?:  sub_lz = 5'd1;
            3'b001:  sub_lz = 5'd2;
            default: sub_lz = 5'd0;
        endcase
    end
    // Shift mantissa left so the leading 1 lands at bit 2. After the
    // shift, bits [1:0] are the fp32 mantissa fractional part (placed at
    // fp32_mant[22:21]); bit 2 is the implicit leading 1 (dropped).
    assign sub_shift = sub_lz;
    logic [5:0] sub_shifted;
    assign sub_shifted = {3'd0, sub_mant} << sub_shift;
    assign sub_mant_norm = sub_shifted[2:0];

    logic [7:0] sub_bias_exp;
    assign sub_bias_exp   = 8'd120 - {3'd0, sub_lz};
    // Place the 2 fractional bits at positions [22:21]; lower bits zero.
    assign fp32_subnormal = {sign, sub_bias_exp, sub_mant_norm[1:0], 21'd0};

    // --- Mux -----------------------------------------------------------
    always_comb begin
        if (is_nan) begin
            // Canonical qNaN (sign-preserved).
            fp32 = {sign, 8'hFF, 1'b1, 22'd0};
        end else if (is_zero) begin
            fp32 = {sign, 31'd0};
        end else if (is_subnormal) begin
            fp32 = fp32_subnormal;
        end else begin
            fp32 = fp32_normal;
        end
    end

endmodule
