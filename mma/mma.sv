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
    // Combinational stall signals from SMEM (high if the read driven LAST
    // cycle was rejected due to bank conflict; mma must re-drive next).
    input  logic                              rd_a_stall_in,
    input  logic                              rd_b_stall_in,
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

    // ---- TMEM seed latch (for accum=1 path) ----
    //
    // tmem.sv asserts mma_rd_valid for exactly ONE cycle after the read
    // request lands. With bank conflicts on the SMEM, the FIRST
    // accumulate can land later than mma_rd_valid pulses — so we latch
    // the tile when valid and consume the latched copy on the first
    // accumulate.
    logic                            tmem_seed_valid;
    logic [MMA_M*MMA_N*32-1:0]       tmem_seed_tile;

    // ---- Bank-conflict stash + inflight tracking ----
    //
    // SMEM has fixed priority LOAD_WR > RD_A > RD_B. When RD_A / RD_B target
    // the same 8-bank group, RD_B stalls every cycle until RD_A backs off.
    // The two ports thus complete their reads on different cycles. We
    // stash whichever port's data arrives first and hold it until the
    // second arrives; then both feed the same K-column accumulation.
    //
    //   cur_collect_k : column we're currently collecting reads for
    //                   (this is the column whose pa_data + pb_data will
    //                   accumulate next).
    //   pa_valid / pa_data : stashed rd_a_data for cur_collect_k
    //   pb_valid / pb_data : stashed rd_b_data for cur_collect_k
    //   a_inflight : 1 iff we drove rd_a_en for cur_collect_k and SMEM
    //                accepted it (not stalled). The corresponding
    //                rd_a_valid will arrive ONE cycle later (smem's drain).
    //                Drops to 0 when rd_a_valid is observed (stash).
    //   b_inflight : analogous.
    //
    // Issue logic: drive rd_a_en for cur_collect_k iff (!pa_valid &&
    // !a_inflight). Same for B. When both pa_valid && pb_valid (or
    // arrive this cycle), accumulate and advance.
    logic                  pa_valid;
    logic [MMA_M*8-1:0]    pa_data;
    logic                  pb_valid;
    logic [MMA_N*8-1:0]    pb_data;
    logic                  a_inflight;
    logic                  b_inflight;
    logic [31:0]           cur_collect_k;

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
            pa_valid          <= 1'b0;
            pa_data           <= '0;
            pb_valid          <= 1'b0;
            pb_data           <= '0;
            a_inflight        <= 1'b0;
            b_inflight        <= 1'b0;
            cur_collect_k     <= 32'd0;
            tmem_seed_valid   <= 1'b0;
            tmem_seed_tile    <= TILE_ZERO;
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

                        // Clear stash/inflight for fresh op. We're about
                        // to drive rd_*_en for col 0 — that's accounted for
                        // by stamping a_inflight/b_inflight on the next
                        // cycle's edge (see S_COMPUTE inflight update).
                        pa_valid       <= 1'b0;
                        pa_data        <= '0;
                        pb_valid       <= 1'b0;
                        pb_data        <= '0;
                        a_inflight     <= 1'b0;
                        b_inflight     <= 1'b0;
                        cur_collect_k  <= 32'd0;
                        tmem_seed_valid <= 1'b0;
                        tmem_seed_tile  <= TILE_ZERO;

                        busy    <= 1'b1;
                        state   <= S_COMPUTE;
                        issue_k <= 32'd1;
                        accum_k <= 32'd0;
                    end
                end

                S_COMPUTE: begin
                    // ----------------------------------------------------
                    // Pipeline (bank-conflict-aware):
                    //
                    //   Per column K (cur_collect_k), we need both rd_a and
                    //   rd_b data. Each drive of rd_*_en at cycle T:
                    //     - if SMEM accepted (!rd_*_stall_in seen at T+1)
                    //       → data drains at edge T+2 (rd_*_valid=1 at T+2).
                    //     - if SMEM stalled (rd_*_stall_in=1 at T+1)
                    //       → no data; must redrive.
                    //
                    //   We track per-port:
                    //     - inflight (1 = SMEM captured but data not yet
                    //       drained this side)
                    //     - stash    (1 = data has arrived & is held)
                    //   Issue:   drive rd_*_en iff (!stash && !inflight).
                    //   Inflight update: set at edge T iff
                    //     "previous-cycle drive succeeded" =
                    //     rd_*_en (own reg) && !rd_*_stall_in.
                    //   Stash:   on rd_*_valid, stash data, clear inflight.
                    //   Accumulate: when both stashes are full (or one
                    //   stashed + the other arriving this cycle), do MAC,
                    //   clear stashes, advance cur_collect_k. The same
                    //   cycle, drive the NEXT column's reads (issue_k).
                    // ----------------------------------------------------

                    // Capture rd_*_valid (data arriving THIS cycle).
                    automatic logic a_arrives = rd_a_valid;
                    automatic logic b_arrives = rd_b_valid;

                    // After this cycle's arrivals, will we have BOTH?
                    automatic logic next_pa = pa_valid || a_arrives;
                    automatic logic next_pb = pb_valid || b_arrives;
                    automatic logic accumulate_now = next_pa && next_pb;

                    // Data fields used in the accumulate (this cycle).
                    automatic logic [MMA_M*8-1:0] a_data_now =
                        pa_valid ? pa_data : rd_a_data;
                    automatic logic [MMA_N*8-1:0] b_data_now =
                        pb_valid ? pb_data : rd_b_data;

                    // Compute new inflight state (this cycle):
                    //   inflight_next = (inflight && !arrived) || just_issued_success
                    //   where "just_issued_success" = our previous-cycle
                    //   drive of rd_*_en succeeded (not stalled).
                    automatic logic a_just_success =
                        rd_a_en && !rd_a_stall_in;
                    automatic logic b_just_success =
                        rd_b_en && !rd_b_stall_in;
                    automatic logic a_inflight_after =
                        (a_inflight && !a_arrives) || a_just_success;
                    automatic logic b_inflight_after =
                        (b_inflight && !b_arrives) || b_just_success;

                    // ---- ISSUE: drive rd_*_en for the column we're collecting.
                    // After accumulate, "current collect" rolls over to the
                    // next column.
                    automatic logic [31:0] next_collect_k =
                        accumulate_now ? cur_collect_k + 32'd1 : cur_collect_k;
                    // After accumulate, stash clears.
                    automatic logic pa_after = accumulate_now ? 1'b0 : next_pa;
                    automatic logic pb_after = accumulate_now ? 1'b0 : next_pb;
                    // After accumulate, inflight also clears (any drained
                    // data was consumed in the MAC).
                    automatic logic a_inflight_after2 =
                        accumulate_now ? 1'b0 : a_inflight_after;
                    automatic logic b_inflight_after2 =
                        accumulate_now ? 1'b0 : b_inflight_after;

                    // Now decide what to drive THIS cycle (NBA → visible
                    // to SMEM next edge). Only drive if we don't have data
                    // and no read is already in flight, AND next_collect_k
                    // is still within MMA_K range.
                    if (next_collect_k < MMA_K) begin
                        if (!pa_after && !a_inflight_after2) begin
                            rd_a_en   <= 1'b1;
                            rd_a_addr <= saved_a_off + next_collect_k * MMA_M;
                        end
                        if (!pb_after && !b_inflight_after2) begin
                            rd_b_en   <= 1'b1;
                            rd_b_addr <= saved_b_off + next_collect_k * MMA_N;
                        end
                    end

                    // ---- STATE COMMIT ----
                    if (accumulate_now) begin
                        pa_valid <= 1'b0;
                        pa_data  <= '0;
                        pb_valid <= 1'b0;
                        pb_data  <= '0;
                        cur_collect_k <= cur_collect_k + 32'd1;
                        // Inflight clears on accumulate (consumed both reads).
                        a_inflight <= 1'b0;
                        b_inflight <= 1'b0;
                    end else begin
                        if (a_arrives && !pa_valid) begin
                            pa_valid <= 1'b1;
                            pa_data  <= rd_a_data;
                        end
                        if (b_arrives && !pb_valid) begin
                            pb_valid <= 1'b1;
                            pb_data  <= rd_b_data;
                        end
                        a_inflight <= a_inflight_after;
                        b_inflight <= b_inflight_after;
                    end

                    // Track when a new read is issued by this cycle's
                    // NBA — that's accounted for as a NEXT-cycle
                    // a_just_success (when we observe our own rd_a_en==1
                    // and check rd_a_stall_in). The inflight register is
                    // updated above based on the PRIOR drive's outcome.
                    // For "this cycle, did we drive?" we'd set inflight
                    // on the FOLLOWING cycle; this is the conventional
                    // "rd_*_en (own reg) && !rd_*_stall_in" pattern.

                    // ---- ACCUMULATE ----
                    if (accumulate_now) begin
                        // Use the latched seed (or the current cycle's
                        // mma_rd_tile if it just arrived and we haven't
                        // latched yet — defensive in case mma_rd_valid and
                        // accumulate_now coincide on the very first cycle).
                        automatic logic [MMA_M*MMA_N*32-1:0] seed_tile =
                            tmem_seed_valid ? tmem_seed_tile : mma_rd_tile;
                        if (!accum_initialized) begin
                            for (i = 0; i < MMA_M; i++) begin
                                for (j = 0; j < MMA_N; j++) begin
                                    acc[i][j] <= fp32_bits_to_real(
                                        seed_tile[((i*MMA_N) + j)*32 +: 32]
                                    ) + decode_e4m3(a_data_now[i*8 +: 8])
                                        * decode_e4m3(b_data_now[j*8 +: 8]);
                                end
                            end
                            accum_initialized <= 1'b1;
                        end else begin
                            for (i = 0; i < MMA_M; i++) begin
                                for (j = 0; j < MMA_N; j++) begin
                                    acc[i][j] <= acc[i][j]
                                        + decode_e4m3(a_data_now[i*8 +: 8])
                                          * decode_e4m3(b_data_now[j*8 +: 8]);
                                end
                            end
                        end

                        if (accum_k + 32'd1 == MMA_K) begin
                            state <= S_WRITEBACK;
                        end
                        accum_k <= accum_k + 32'd1;

                        // issue_k now mirrors next_collect_k for clarity
                        // (we don't actually consult issue_k anymore —
                        // next_collect_k drives issuance). Keep it
                        // updated for any external observation.
                        issue_k <= cur_collect_k + 32'd1;
                    end

                    // ---- TMEM seed latch ----
                    // mma_rd_valid pulses one cycle only; with bank
                    // conflicts the first accumulate may land later, so
                    // we latch the tile while it's valid.
                    if (mma_rd_valid && !tmem_seed_valid) begin
                        tmem_seed_valid <= 1'b1;
                        tmem_seed_tile  <= mma_rd_tile;
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
