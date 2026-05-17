// mma.sv -- broadcast MAC grid: smem x smem -> tmem accumulator.
//
// SV implementation of pymodel.mma.MMA. See pymodel/mma.py for the canonical
// spec; this module's RESULT (the fp32 tile written into TMEM) must match
// the pymodel for the same operand bytes. The exact cycle on which `done`
// pulses differs from the pymodel: the pymodel uses back-door SMEM/TMEM
// access (zero-latency) and pulses done MMA_K+1 cycles after start; this
// RTL goes through the registered SMEM/TMEM port handshakes (1-cycle read
// latency on each port -> 2-cycle producer-to-consumer pipeline delay) and
// so pulses done a small fixed number of cycles later. The cocotb TB
// validates correctness of the final tile against golden.matmul_reference,
// not the precise cycle count.
//
// fp8 decoder: simulation-only. Mirrors golden.fp8.decode_e4m3:
//   - sign  = bit[7]
//   - exp   = bits[6:3]
//   - mant  = bits[2:0]
//   - exp==0xF and mant==0x7 -> NaN
//   - exp==0 and mant!=0     -> subnormal: sign * mant * 2^-9
//   - exp==0 and mant==0     -> signed zero
//   - otherwise              -> normal: sign * (1 + mant/8) * 2^(exp - 7)
//
// We use Verilator `real` (IEEE 754 fp64) for the multiply-accumulate
// internally, then convert each accumulator element back to fp32 IEEE 754
// bits when packing the writeback tile. This sim-only construct (NOT
// synthesizable) is acceptable for Phase 4 -- matches the approach used
// in store.sv.
//
// MAC grid: an MMA_M x MMA_N array of `real` accumulators (`acc[i][j]`).
// Each compute cycle, decode A_col (MMA_M fp8 bytes) and B_row (MMA_N fp8
// bytes) to fp32-precision values and add the outer product into acc.
//
// Pipeline (registered drives, ~MMA_K + a-few-extra cycles latency):
//   On start: latch operands, busy<=1, issue read of col 0 on the SMEM
//   ports, and (if accum=1) issue a TMEM READ on slot=d_slot. While
//   issue_k<MMA_K we continue issuing reads (col 1, 2, ...). On each cycle
//   where rd_a_valid && rd_b_valid we accumulate; on the first such cycle,
//   if accum=1 we also fold the TMEM rd_tile into acc.

module mma #(
    parameter int MMA_M = 32,
    parameter int MMA_N = 32,
    parameter int MMA_K = 32
) (
    input  logic                              clk,
    input  logic                              reset,

    // Issue interface
    input  logic                              start,
    input  logic [31:0]                       a_smem_offset,
    input  logic [31:0]                       b_smem_offset,
    input  logic [31:0]                       d_tmem_slot,
    input  logic                              accum,
    input  logic [31:0]                       bar_id,

    // From SMEM MMA_RD_A
    input  logic [MMA_M*8-1:0]                rd_a_data,
    input  logic                              rd_a_valid,
    // From SMEM MMA_RD_B
    input  logic [MMA_N*8-1:0]                rd_b_data,
    input  logic                              rd_b_valid,
    // From TMEM MMA_PORT
    input  logic [MMA_M*MMA_N*32-1:0]         mma_rd_tile,
    input  logic                              mma_rd_valid,

    // To SMEM MMA_RD_A
    output logic                              rd_a_en,
    output logic [31:0]                       rd_a_addr,
    // To SMEM MMA_RD_B
    output logic                              rd_b_en,
    output logic [31:0]                       rd_b_addr,
    // To TMEM MMA_PORT
    output logic [1:0]                        mma_op,        // 0=NONE, 1=READ, 2=WRITE
    output logic [31:0]                       mma_slot,
    output logic [MMA_M*MMA_N*32-1:0]         mma_write_tile,

    // To barrier
    output logic                              arrive_en,
    output logic [31:0]                       arrive_bar_id,

    // Status
    output logic                              busy,
    output logic                              done
);

    // -------------------------------------------------------------------
    // TMEM op codes (must match pymodel.tmem.MMAOp).
    // -------------------------------------------------------------------
    localparam logic [1:0] OP_NONE  = 2'd0;
    localparam logic [1:0] OP_READ  = 2'd1;
    localparam logic [1:0] OP_WRITE = 2'd2;

    // Zero constant for the packed tile (silence Verilator concat warning).
    /* verilator lint_off WIDTHCONCAT */
    localparam logic [MMA_M*MMA_N*32-1:0] TILE_ZERO = {(MMA_M*MMA_N*32){1'b0}};
    /* verilator lint_on WIDTHCONCAT */

    // -------------------------------------------------------------------
    // FSM
    // -------------------------------------------------------------------
    typedef enum logic [1:0] {
        S_IDLE      = 2'd0,
        S_COMPUTE   = 2'd1,
        S_WRITEBACK = 2'd2
    } state_t;
    state_t state;

    // Latched operands.
    logic [31:0] saved_a_off;
    logic [31:0] saved_b_off;
    logic [31:0] saved_d_slot;
    logic        saved_accum;
    logic [31:0] saved_bar_id;

    // Issue/accumulate counters.
    logic [31:0] issue_k;  // next column to issue a read for (0..MMA_K)
    logic [31:0] accum_k;  // number of columns accumulated so far (0..MMA_K)

    // True after we've consumed the initial TMEM rd_tile (accum=1) or after
    // S_IDLE has zeroed acc (accum=0). Either way, subsequent accumulations
    // simply add into acc.
    logic        accum_initialized;

    // Internal accumulator: a `real` grid (simulation-only).
    real acc [MMA_M][MMA_N];

    // -------------------------------------------------------------------
    // Convert a 32-bit IEEE 754 fp32 bit pattern to a `real`. Mirrors the
    // helper in store.sv. Handles sign, normal (exp in [1, 254]), subnormal
    // (exp == 0), and zero. (Inf / NaN paths are not exercised here -- TMEM
    // tiles we read back from the accumulator are products of fp8 decoded
    // values multiplied by fp8 decoded values; values stay well in range.)
    // -------------------------------------------------------------------
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
                // Subnormal (or zero): val = m * 2^-149.
                mantf = real'(int'({9'd0, m}));
                scale = 1.0;
                for (k = 0; k < 149; k++) scale = scale / 2.0;
                val = mantf * scale;
            end else begin
                // Normal: val = (1 + m/2^23) * 2^(e - 127).
                mantf = 1.0 + (real'(int'({9'd0, m})) / 8388608.0);
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

    // -------------------------------------------------------------------
    // Convert a `real` to a 32-bit IEEE 754 fp32 bit pattern. Round-to-
    // nearest-even (matching numpy's default fp32 rounding).
    //
    // Strategy: split sign and magnitude, find the unbiased exponent via
    // ilog2-style normalization, compute the 23-bit mantissa with round-
    // to-nearest-even, pack { sign, exp+127, mant }. Subnormals (|v| <
    // 2^-126) and overflow saturation are handled too.
    // -------------------------------------------------------------------
    function automatic logic [31:0] real_to_fp32_bits(input real v);
        logic        sign_bit;
        real         av;
        int          exp_unbiased;
        real         mantf;
        real         mant_scaled;
        logic [23:0] mant_round;       // includes the implicit leading 1
        logic [22:0] mant_field;
        logic [7:0]  exp_field;
        logic [31:0] out;
        int          k;
        real         lo, hi;
        real         frac;
        logic        round_up;
        begin
            // Zero (and -0).
            if (v == 0.0) begin
                sign_bit = (1.0 / v < 0.0) ? 1'b1 : 1'b0;  // distinguish -0
                return {sign_bit, 31'd0};
            end

            // Split sign.
            sign_bit = (v < 0.0) ? 1'b1 : 1'b0;
            av       = sign_bit ? -v : v;

            // Find unbiased exponent E such that 1.0 <= av * 2^-E < 2.0.
            // Use a loop on doubling/halving (slow but cycle-free in sim).
            exp_unbiased = 0;
            mantf = av;
            // Bring mantf into [1, 2).
            if (mantf >= 2.0) begin
                while (mantf >= 2.0 && exp_unbiased < 130) begin
                    mantf = mantf / 2.0;
                    exp_unbiased = exp_unbiased + 1;
                end
            end else if (mantf < 1.0) begin
                while (mantf < 1.0 && exp_unbiased > -150) begin
                    mantf = mantf * 2.0;
                    exp_unbiased = exp_unbiased - 1;
                end
            end

            // Overflow saturation: clamp to fp32 max magnitude (~3.4028e38).
            if (exp_unbiased > 127) begin
                exp_field  = 8'd254;
                mant_field = 23'h7FFFFF;
                return {sign_bit, exp_field, mant_field};
            end

            // Subnormal range: exp_unbiased < -126. Use exp_field = 0 and
            // compute the 23-bit mantissa as round-to-nearest-even of
            // av * 2^149 (which puts the LSB at 2^-149).
            if (exp_unbiased < -126) begin
                exp_field = 8'd0;
                // Scale av to integer-with-bits-below precision: av * 2^149.
                mant_scaled = av;
                for (k = 0; k < 149; k++) mant_scaled = mant_scaled * 2.0;
                // Round-to-nearest-even.
                {mant_round, round_up} = '0;
                mant_round = 24'(longint'(mant_scaled));  // truncate
                frac = mant_scaled - real'(mant_round);
                if (frac > 0.5) begin
                    mant_round = mant_round + 24'd1;
                end else if (frac == 0.5) begin
                    if (mant_round[0]) mant_round = mant_round + 24'd1;
                end
                if (mant_round[23]) begin
                    // Rounding produced a normal number (mant 2^23 = 1.0 * 2^-126).
                    exp_field  = 8'd1;
                    mant_field = 23'd0;
                end else begin
                    mant_field = mant_round[22:0];
                end
                return {sign_bit, exp_field, mant_field};
            end

            // Normal: mantf in [1, 2). Subtract 1 to get the fractional
            // mantissa in [0, 1), then scale by 2^23 and round-to-nearest-even.
            mant_scaled = (mantf - 1.0) * 8388608.0;  // 2^23
            mant_round = 24'(longint'(mant_scaled));  // truncate (lower 23 bits hold the fraction)
            frac = mant_scaled - real'(mant_round);
            if (frac > 0.5) begin
                mant_round = mant_round + 24'd1;
            end else if (frac == 0.5) begin
                if (mant_round[0]) mant_round = mant_round + 24'd1;
            end
            // If rounding overflowed 23 bits, bump the exponent.
            if (mant_round[23]) begin
                exp_unbiased = exp_unbiased + 1;
                mant_round   = 24'd0;
                if (exp_unbiased > 127) begin
                    exp_field  = 8'd254;
                    mant_field = 23'h7FFFFF;
                    return {sign_bit, exp_field, mant_field};
                end
            end
            exp_field  = 8'(exp_unbiased + 127);
            mant_field = mant_round[22:0];
            out = {sign_bit, exp_field, mant_field};
            return out;
        end
    endfunction

    // -------------------------------------------------------------------
    // fp8 e4m3 decoder (mirrors golden.fp8.decode_e4m3). Returns a `real`.
    // -------------------------------------------------------------------
    function automatic real decode_e4m3(input logic [7:0] b);
        logic        sign_bit;
        logic [3:0]  exp_field;
        logic [2:0]  mant;
        real         s;
        real         v;
        int          i;
        begin
            sign_bit  = b[7];
            exp_field = b[6:3];
            mant      = b[2:0];
            s         = sign_bit ? -1.0 : 1.0;

            // NaN: exp=0xF, mant=0x7. We never actually expect a NaN here
            // (generate() produces only finite values), but mirror the
            // golden behavior. Return a deterministic NaN-ish sentinel.
            if (exp_field == 4'hF && mant == 3'h7) begin
                return s * (0.0 / 0.0);
            end

            if (exp_field == 4'd0) begin
                if (mant == 3'd0) begin
                    return sign_bit ? -0.0 : 0.0;
                end else begin
                    v = real'(int'(mant)) * (1.0 / 512.0);  // 2^-9
                    return s * v;
                end
            end

            // Normal: s * (1 + mant/8) * 2^(exp - 7).
            v = 1.0 + (real'(int'(mant)) / 8.0);
            if (int'(exp_field) >= 7) begin
                for (i = 0; i < int'(exp_field) - 7; i++) v = v * 2.0;
            end else begin
                for (i = 0; i < 7 - int'(exp_field); i++) v = v / 2.0;
            end
            return s * v;
        end
    endfunction

    // -------------------------------------------------------------------
    // Pack acc[i][j] into mma_write_tile.
    // Convention: element [i][j] -> bits [(i*MMA_N + j)*32 +: 32], fp32 LSB.
    // -------------------------------------------------------------------
    function automatic logic [MMA_M*MMA_N*32-1:0] pack_acc;
        /* verilator lint_off WIDTHCONCAT */
        logic [MMA_M*MMA_N*32-1:0] out;
        /* verilator lint_on WIDTHCONCAT */
        int i, j;
        logic [31:0] bits;
        begin
            out = TILE_ZERO;
            for (i = 0; i < MMA_M; i++) begin
                for (j = 0; j < MMA_N; j++) begin
                    bits = real_to_fp32_bits(acc[i][j]);
                    out[((i*MMA_N) + j)*32 +: 32] = bits;
                end
            end
            return out;
        end
    endfunction

    // -------------------------------------------------------------------
    // Sequential logic.
    // -------------------------------------------------------------------
    integer i, j;

    always_ff @(posedge clk) begin
        if (reset) begin
            state             <= S_IDLE;
            busy              <= 1'b0;
            done              <= 1'b0;
            rd_a_en           <= 1'b0;
            rd_a_addr         <= 32'd0;
            rd_b_en           <= 1'b0;
            rd_b_addr         <= 32'd0;
            mma_op            <= OP_NONE;
            mma_slot          <= 32'd0;
            mma_write_tile    <= TILE_ZERO;
            arrive_en         <= 1'b0;
            arrive_bar_id     <= 32'd0;
            saved_a_off       <= 32'd0;
            saved_b_off       <= 32'd0;
            saved_d_slot      <= 32'd0;
            saved_accum       <= 1'b0;
            saved_bar_id      <= 32'd0;
            issue_k           <= 32'd0;
            accum_k           <= 32'd0;
            accum_initialized <= 1'b0;
            for (i = 0; i < MMA_M; i++) begin
                for (j = 0; j < MMA_N; j++) begin
                    acc[i][j] <= 0.0;
                end
            end
        end else begin
            // Default per-cycle drives (cleared each tick; set below if active).
            done           <= 1'b0;
            arrive_en      <= 1'b0;
            arrive_bar_id  <= 32'd0;
            rd_a_en        <= 1'b0;
            rd_a_addr      <= 32'd0;
            rd_b_en        <= 1'b0;
            rd_b_addr      <= 32'd0;
            mma_op         <= OP_NONE;
            mma_slot       <= 32'd0;
            mma_write_tile <= TILE_ZERO;

            unique case (state)
                S_IDLE: begin
                    if (start) begin
                        // Latch operands.
                        saved_a_off  <= a_smem_offset;
                        saved_b_off  <= b_smem_offset;
                        saved_d_slot <= d_tmem_slot;
                        saved_accum  <= accum;
                        saved_bar_id <= bar_id;

                        // Zero the internal accumulator on accum=0. On accum=1
                        // we wait for the TMEM read to land and overwrite acc.
                        if (!accum) begin
                            for (i = 0; i < MMA_M; i++) begin
                                for (j = 0; j < MMA_N; j++) begin
                                    acc[i][j] <= 0.0;
                                end
                            end
                            accum_initialized <= 1'b1;
                        end else begin
                            mma_op            <= OP_READ;
                            mma_slot          <= d_tmem_slot;
                            accum_initialized <= 1'b0;
                        end

                        // Issue first SMEM reads (column 0).
                        rd_a_en   <= 1'b1;
                        rd_a_addr <= a_smem_offset;
                        rd_b_en   <= 1'b1;
                        rd_b_addr <= b_smem_offset;

                        busy    <= 1'b1;
                        state   <= S_COMPUTE;
                        issue_k <= 32'd1;
                        accum_k <= 32'd0;
                    end
                end

                S_COMPUTE: begin
                    // Keep issuing SMEM reads until we've issued all MMA_K columns.
                    if (issue_k < MMA_K) begin
                        rd_a_en   <= 1'b1;
                        rd_a_addr <= saved_a_off + issue_k * MMA_M;
                        rd_b_en   <= 1'b1;
                        rd_b_addr <= saved_b_off + issue_k * MMA_N;
                        issue_k   <= issue_k + 32'd1;
                    end

                    // Accumulate when both SMEM ports are valid this cycle.
                    if (rd_a_valid && rd_b_valid) begin
                        if (!accum_initialized) begin
                            // First valid cycle on the accum=1 path: capture
                            // the TMEM rd_tile as the seed, then add the
                            // first outer product on top.
                            for (i = 0; i < MMA_M; i++) begin
                                for (j = 0; j < MMA_N; j++) begin
                                    acc[i][j] <= fp32_bits_to_real(
                                        mma_rd_tile[((i*MMA_N) + j)*32 +: 32]
                                    ) + decode_e4m3(rd_a_data[i*8 +: 8])
                                        * decode_e4m3(rd_b_data[j*8 +: 8]);
                                end
                            end
                            accum_initialized <= 1'b1;
                        end else begin
                            for (i = 0; i < MMA_M; i++) begin
                                for (j = 0; j < MMA_N; j++) begin
                                    acc[i][j] <= acc[i][j]
                                        + decode_e4m3(rd_a_data[i*8 +: 8])
                                          * decode_e4m3(rd_b_data[j*8 +: 8]);
                                end
                            end
                        end

                        if (accum_k + 32'd1 == MMA_K) begin
                            // Last accumulation this cycle; writeback next cycle
                            // (when `acc` reflects the just-NBA'd final value).
                            state   <= S_WRITEBACK;
                        end
                        accum_k <= accum_k + 32'd1;
                    end
                end

                S_WRITEBACK: begin
                    // Commit the final tile to TMEM, arrive on barrier, pulse done.
                    mma_op         <= OP_WRITE;
                    mma_slot       <= saved_d_slot;
                    mma_write_tile <= pack_acc();

                    arrive_en      <= 1'b1;
                    arrive_bar_id  <= saved_bar_id;

                    done           <= 1'b1;
                    busy           <= 1'b0;
                    state          <= S_IDLE;

                    issue_k        <= 32'd0;
                    accum_k        <= 32'd0;
                end

                default: begin
                    state <= S_IDLE;
                end
            endcase
        end
    end

endmodule
