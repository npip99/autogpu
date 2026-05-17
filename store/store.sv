// store.sv — STORE engine: drains a TMEM slot to GMEM.
//
// SV implementation of pymodel.store.Store. See pymodel/store.py for the
// canonical spec; this module must match it cycle-by-cycle.
//
// SYNC MODEL
//   STORE is synchronous in v1: cmdproc holds issue_en until done pulses.
//   We ignore re-issues while busy. (Cmdproc is responsible for not issuing
//   another STORE while this one is busy, but we defensively ignore.)
//
// PIPELINE
//   Cycle 0 (busy=0, issue_en=1):
//     - latch saved (slot, gmem_ptr, dtype)
//     - drive tmem.store_rd_en + slot
//     - busy <= 1, state <= WAIT_RD
//   Cycle 1 (state=WAIT_RD):
//     - tmem.store_rd_valid==1 in this cycle
//     - capture tmem.store_rd_tile into tile_buf
//     - (if dtype==1) encode each fp32 element to fp8 byte → bytes_buf
//       (if dtype==0) bytes_buf = tile_buf bit-pattern verbatim (LE)
//     - bytes_written <= 0, state <= DRAIN
//   Cycle 2..K (state=DRAIN, K = total_bytes / BEAT_BYTES):
//     - gmem.wr_en = 1, wr_addr = gmem_ptr + bytes_written
//     - wr_data = bytes_buf[bytes_written*8 +: BEAT_BYTES*8]
//     - bytes_written <= bytes_written + BEAT_BYTES
//     - when bytes_written + BEAT_BYTES == total_bytes:
//         done <= 1, busy <= 0, state <= IDLE
//
// TILE PACKING (consumed from TMEM):
//   See pymodel/tmem.py §"RTL TILE PACKING CONVENTION". Element [i][j] at bit
//   ((i*MMA_N + j)*32) +: 32 of store_rd_tile; IEEE 754 fp32 LSB-first.
//
// OUTPUT LAYOUT IN GMEM:
//   Row-major. Byte for element (i, j) at gmem_ptr + (i*MMA_N + j) * elem_size,
//   where elem_size = 4 (dtype=0, fp32 LE) or 1 (dtype=1, fp8 e4m3).
//
// FP8 ENCODE (dtype=1):
//   Mirrors golden.fp8.encode_e4m3 — round-to-nearest by argmin over the 127
//   positive e4m3 magnitudes; sign bit OR'd in; saturate to ±max_normal for
//   |v| >= 448; NaN → 0x7F / 0xFF. Implementation manually decodes the fp32
//   bit-pattern into a `real` (fp64) via fp32_bits_to_real (Verilator does
//   not support $bitstoshortreal / shortreal). decode_pos_e4m3 produces each
//   of the 127 positive magnitudes on the fly inside the argmin loop —
//   simulation-only and slow (~30 ms per fp8 STORE of a 32x32 tile) but
//   functionally exact vs the pymodel. A synthesizable rewrite would be
//   combinational bit-twiddling — single cycle per element.

module store #(
    parameter int MMA_M      = 32,
    parameter int MMA_N      = 32,
    parameter int BEAT_BYTES = 16
) (
    input  logic                          clk,
    input  logic                          reset,

    // Issue
    input  logic                          issue_en,
    input  logic [31:0]                   tmem_slot,
    input  logic [31:0]                   gmem_ptr,
    input  logic                          dtype,    // 0 = fp32, 1 = fp8

    // From TMEM STORE_RD port
    input  logic [MMA_M*MMA_N*32-1:0]     store_rd_tile,
    input  logic                          store_rd_valid,

    // To TMEM STORE_RD port
    output logic                          store_rd_en,
    output logic [31:0]                   store_rd_slot,

    // To GMEM write port
    output logic                          wr_en,
    output logic [31:0]                   wr_addr,
    output logic [BEAT_BYTES*8-1:0]       wr_data,

    // Status
    output logic                          busy,
    output logic                          done
);

    // ------------------------------------------------------------------
    // Derived sizes
    // ------------------------------------------------------------------
    localparam int NUM_ELEMS    = MMA_M * MMA_N;
    localparam int TILE_BYTES   = NUM_ELEMS * 4;  // fp32 path
    localparam int FP8_BYTES    = NUM_ELEMS;      // fp8 path
    localparam int MAX_BYTES    = TILE_BYTES;     // buf sized to fp32
    localparam int FP32_BEATS   = TILE_BYTES / BEAT_BYTES;
    localparam int FP8_BEATS    = FP8_BYTES  / BEAT_BYTES;

    // ------------------------------------------------------------------
    // FSM
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        S_IDLE   = 2'd0,
        S_WAIT_RD = 2'd1,
        S_DRAIN  = 2'd2
    } state_t;

    state_t state;

    // Latched operands.
    logic [31:0] saved_gmem_ptr;
    logic        saved_dtype;

    // Drain buffer (sized to max output: TILE_BYTES bytes = TILE_BYTES*8 bits).
    /* verilator lint_off WIDTHCONCAT */
    logic [MAX_BYTES*8-1:0] bytes_buf;
    /* verilator lint_on WIDTHCONCAT */

    // Total bytes for this op (selected by dtype).
    logic [31:0] total_bytes;
    logic [31:0] bytes_written;

    // ------------------------------------------------------------------
    // fp8 e4m3 encode (simulation-only): mirrors golden.fp8.encode_e4m3.
    //
    // Strategy: precompute the 127 positive-magnitude e4m3 decoded values as
    // `real` (fp64) lazily inside the encode call. For each input fp32 word,
    // manually decode sign/exp/mantissa into a `real`, take |v|, handle NaN /
    // saturation, otherwise argmin |LUT[i] - |v||.
    //
    // We use `real` (fp64) throughout — Verilator only supports `real`, not
    // `shortreal`, and the pymodel computes diffs in float64 too, so this
    // matches numerically.
    // ------------------------------------------------------------------
    // E4M3 constants.
    localparam real E4M3_MAX = 448.0;  // exp=15, mant=110 (binary)
    localparam logic [7:0] NAN_CODE_POS = 8'h7F;
    localparam logic [7:0] NAN_CODE_NEG = 8'hFF;
    localparam logic [7:0] MAX_NORM_POS = 8'h7E;
    localparam logic [7:0] MAX_NORM_NEG = 8'hFE;

    // Convert a 32-bit IEEE 754 fp32 bit pattern to a `real` (fp64). Handles
    // sign, normal (exp in [1, 254]), subnormal (exp == 0), and zero. Infinities
    // and NaN are returned as a large finite value here (the caller's saturation
    // path / NaN path screens them first).
    function automatic real fp32_bits_to_real(input logic [31:0] bits);
        logic        s;
        logic [7:0]  e;
        logic [22:0] m;
        real         val, mantf, scale;
        int          k;
        begin
            s = bits[31];
            e = bits[30:23];
            m = bits[22:0];

            if (e == 8'd0) begin
                // Subnormal (or zero): val = m * 2^(1 - 127 - 23) = m * 2^-149.
                mantf = real'(int'({9'd0, m}));  // m as a real, 0..(2^23 - 1)
                // Scale = 2^-149.
                scale = 1.0;
                for (k = 0; k < 149; k++) scale = scale / 2.0;
                val = mantf * scale;
            end else begin
                // Normal: val = (1 + m/2^23) * 2^(e - 127).
                mantf = 1.0 + (real'(int'({9'd0, m})) / 8388608.0);  // 2^23
                // Multiply by 2^(e - 127). e in [1, 254] -> shift -126..+127.
                if (int'(e) >= 127) begin
                    val = mantf;
                    for (k = 0; k < int'(e) - 127; k++) val = val * 2.0;
                end else begin
                    val = mantf;
                    for (k = 0; k < 127 - int'(e); k++) val = val / 2.0;
                end
            end

            if (s) val = -val;
            return val;
        end
    endfunction

    // Decode a single e4m3 code (0x00..0x7E, positive magnitudes only) to
    // a positive `real` magnitude. Returns 0.0 for code 0x00. Matches
    // golden.fp8.decode_e4m3 for sign=0, non-NaN codes.
    function automatic real decode_pos_e4m3(input logic [6:0] code);
        logic [3:0] exp_field;
        logic [2:0] mant;
        real        v;
        int         i;
        begin
            exp_field = code[6:3];
            mant      = code[2:0];

            if (exp_field == 4'd0) begin
                // Subnormal (or zero): v = mant * 2^(1 - bias - 3) = mant * 2^-9.
                v = real'(int'(mant)) * (1.0 / 512.0);  // 2^-9
            end else begin
                // Normal: v = (1 + mant/8) * 2^(exp - 7).
                v = 1.0 + (real'(int'(mant)) / 8.0);
                // Multiply by 2^(exp - 7). exp ranges 1..15 -> shift -6..+8.
                if (int'(exp_field) >= 7) begin
                    for (i = 0; i < int'(exp_field) - 7; i++) v = v * 2.0;
                end else begin
                    for (i = 0; i < 7 - int'(exp_field); i++) v = v / 2.0;
                end
            end
            return v;
        end
    endfunction

    // Encode one fp32 bit-pattern to an e4m3 byte. Mirrors golden.fp8.encode_e4m3.
    function automatic logic [7:0] encode_e4m3(input logic [31:0] bits);
        logic        sign_bit;
        logic [7:0]  exp_field;
        logic [22:0] mant_field;
        real         abs_v, best_diff, diff, lut_v;
        int          best_idx, k;
        logic [7:0]  result;
        begin
            sign_bit   = bits[31];
            exp_field  = bits[30:23];
            mant_field = bits[22:0];

            // NaN: exp=0xFF and mantissa != 0.
            if (exp_field == 8'hFF && mant_field != 23'd0) begin
                return sign_bit ? NAN_CODE_NEG : NAN_CODE_POS;
            end

            // |v| from bit pattern with sign cleared.
            abs_v = fp32_bits_to_real({1'b0, bits[30:0]});

            // Saturation.
            if (abs_v >= E4M3_MAX) begin
                return sign_bit ? MAX_NORM_NEG : MAX_NORM_POS;
            end

            // Argmin over the 127 positive codes (0x00..0x7E inclusive).
            best_idx  = 0;
            best_diff = decode_pos_e4m3(7'd0) - abs_v;
            if (best_diff < 0.0) best_diff = -best_diff;
            for (k = 1; k < 127; k++) begin
                lut_v = decode_pos_e4m3(k[6:0]);
                diff  = lut_v - abs_v;
                if (diff < 0.0) diff = -diff;
                if (diff < best_diff) begin
                    best_diff = diff;
                    best_idx  = k;
                end
            end

            result = sign_bit ? (8'h80 | best_idx[7:0]) : best_idx[7:0];
            return result;
        end
    endfunction

    // ------------------------------------------------------------------
    // Pack the tile into bytes_buf based on dtype.
    // For dtype==0 (fp32): bytes_buf[ (i*MMA_N+j)*32 +: 32 ] = tile[i][j] bits.
    //   This matches the TMEM packing exactly, so we can just assign the whole
    //   packed vector.
    // For dtype==1 (fp8): bytes_buf[ (i*MMA_N+j)*8 +: 8 ] = encode_e4m3(tile[i][j]).
    // ------------------------------------------------------------------
    function automatic logic [MAX_BYTES*8-1:0] pack_bytes(
        input logic [MMA_M*MMA_N*32-1:0] tile,
        input logic                       d
    );
        logic [MAX_BYTES*8-1:0] out;
        int idx;
        logic [31:0] elem_bits;
        logic [7:0]  fp8_byte;
        begin
            /* verilator lint_off WIDTHCONCAT */
            out = '0;
            /* verilator lint_on WIDTHCONCAT */
            if (d == 1'b0) begin
                // fp32 path: copy verbatim (low NUM_ELEMS*32 bits used).
                for (idx = 0; idx < NUM_ELEMS; idx++) begin
                    elem_bits = tile[idx*32 +: 32];
                    out[idx*32 +: 32] = elem_bits;
                end
            end else begin
                // fp8 path: encode each element to a single byte (low NUM_ELEMS*8 bits).
                for (idx = 0; idx < NUM_ELEMS; idx++) begin
                    elem_bits = tile[idx*32 +: 32];
                    fp8_byte  = encode_e4m3(elem_bits);
                    out[idx*8 +: 8] = fp8_byte;
                end
            end
            return out;
        end
    endfunction

    // ------------------------------------------------------------------
    // Sequential logic.
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (reset) begin
            state          <= S_IDLE;
            busy           <= 1'b0;
            done           <= 1'b0;
            store_rd_en    <= 1'b0;
            store_rd_slot  <= 32'd0;
            wr_en          <= 1'b0;
            wr_addr        <= 32'd0;
            wr_data        <= '0;
            saved_gmem_ptr <= 32'd0;
            saved_dtype    <= 1'b0;
            /* verilator lint_off WIDTHCONCAT */
            bytes_buf      <= '0;
            /* verilator lint_on WIDTHCONCAT */
            total_bytes    <= 32'd0;
            bytes_written  <= 32'd0;
        end else begin
            // Defaults (cleared every cycle; set below if active).
            done          <= 1'b0;
            store_rd_en   <= 1'b0;
            store_rd_slot <= 32'd0;
            wr_en         <= 1'b0;
            wr_addr       <= 32'd0;
            wr_data       <= '0;

            unique case (state)
                S_IDLE: begin
                    if (issue_en) begin
                        // Latch and issue tmem read.
                        saved_gmem_ptr <= gmem_ptr;
                        saved_dtype    <= dtype;
                        store_rd_en    <= 1'b1;
                        store_rd_slot  <= tmem_slot;
                        total_bytes    <= dtype ? FP8_BYTES : TILE_BYTES;
                        bytes_written  <= 32'd0;
                        busy           <= 1'b1;
                        state          <= S_WAIT_RD;
                    end
                end

                S_WAIT_RD: begin
                    // Expect store_rd_valid this cycle.
                    if (store_rd_valid) begin
                        bytes_buf <= pack_bytes(store_rd_tile, saved_dtype);
                        state     <= S_DRAIN;
                    end
                    // (If store_rd_valid is 0 here, we just wait — but per spec
                    // it's always 1 the cycle after store_rd_en, so this is
                    // belt-and-suspenders.)
                end

                S_DRAIN: begin
                    // Emit one BEAT_BYTES write.
                    wr_en   <= 1'b1;
                    wr_addr <= saved_gmem_ptr + bytes_written;
                    wr_data <= bytes_buf[bytes_written*8 +: BEAT_BYTES*8];

                    if (bytes_written + BEAT_BYTES >= total_bytes) begin
                        // Last beat: pulse done, return to idle.
                        done          <= 1'b1;
                        busy          <= 1'b0;
                        state         <= S_IDLE;
                        bytes_written <= 32'd0;
                    end else begin
                        bytes_written <= bytes_written + BEAT_BYTES;
                    end
                end

                default: begin
                    state <= S_IDLE;
                end
            endcase
        end
    end

endmodule
