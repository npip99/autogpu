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
// Datapath: fully synthesizable. Each accumulator element acc[i][j] is a
// 32-bit fp32 word. fp8 operands are decoded by `fp8_decode` (combinational,
// see common/fp8_decode.sv) into fp32 bits. Each MAC is an `fp32_fma`
// (combinational, see common/fp32_fma.sv, which wraps CVFPU fpnew_fma in
// FP32, FMADD, RNE, NumPipeRegs=0). On a valid MAC cycle, the FMA computes
// a*b + acc_old, and the result is latched into acc on the next clock edge.
//
// MAC grid: MMA_M x MMA_N fp32_fma instances + decoders. For MMA_M=MMA_N=32
// the design has 1024 FMAs and 64 fp8 decoders (32 A-side + 32 B-side).
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

    // Issue/accumulate counters. issue_k is a debug aid — tracks how
    // many reads have been issued for the current tile; it isn't on any
    // functional path (the FSM advances via accum_k and the rd_a/rd_b
    // address counters).
    /* verilator lint_off UNUSEDSIGNAL */
    logic [31:0] issue_k;
    /* verilator lint_on UNUSEDSIGNAL */
    logic [31:0] accum_k;

    logic        accum_initialized;

    // Internal accumulator: MMA_M x MMA_N fp32 words.
    logic [31:0] acc [MMA_M][MMA_N];

    // ---- TMEM seed latch (for accum=1 path) ----
    logic                            tmem_seed_valid;
    logic [MMA_M*MMA_N*32-1:0]       tmem_seed_tile;

    // ---- Bank-conflict stash + inflight tracking ----
    logic                  pa_valid;
    logic [MMA_M*8-1:0]    pa_data;
    logic                  pb_valid;
    logic [MMA_N*8-1:0]    pb_data;
    logic                  a_inflight;
    logic                  b_inflight;
    logic [31:0]           cur_collect_k;

    // -------------------------------------------------------------------
    // Combinational decode/FMA datapath
    // -------------------------------------------------------------------
    //
    // a_data_now / b_data_now hold the operand bytes for THIS cycle's
    // accumulation (either freshly arrived from SMEM, or stashed from a
    // prior cycle). We expose them combinationally below.
    //
    // a_dec[i] / b_dec[j] : fp32 bits decoded from a_data_now[i] /
    // b_data_now[j].
    // acc_addend[i][j]    : addend fed to FMA(i,j). On the first
    //                       accumulate (`!accum_initialized`) this is
    //                       the seed (TMEM tile if accum=1, else 0);
    //                       otherwise it's the current acc register.
    // fma_out[i][j]       : combinational FMA result a*b + addend.

    // Combinational helper wires (set below in the FSM logic).
    logic [MMA_M*8-1:0] a_data_now;
    logic [MMA_N*8-1:0] b_data_now;
    logic               accumulate_now;
    logic [MMA_M*MMA_N*32-1:0] seed_tile_now;

    // Decoder wires.
    logic [31:0] a_dec [MMA_M];
    logic [31:0] b_dec [MMA_N];

    genvar gi, gj;
    generate
        for (gi = 0; gi < MMA_M; gi++) begin : gen_dec_a
            fp8_decode u_dec_a (
                .fp8  (a_data_now[gi*8 +: 8]),
                .fp32 (a_dec[gi])
            );
        end
        for (gj = 0; gj < MMA_N; gj++) begin : gen_dec_b
            fp8_decode u_dec_b (
                .fp8  (b_data_now[gj*8 +: 8]),
                .fp32 (b_dec[gj])
            );
        end
    endgenerate

    // FMA grid.
    logic [31:0] fma_out [MMA_M][MMA_N];
    logic [31:0] acc_addend [MMA_M][MMA_N];

    generate
        for (gi = 0; gi < MMA_M; gi++) begin : gen_fma_row
            for (gj = 0; gj < MMA_N; gj++) begin : gen_fma_col
                // On the first accumulate (accum=1 path) we fold the TMEM
                // seed tile into the FMA's addend. Otherwise, the addend
                // is the prior acc register. For accum=0 (acc starts as
                // zero), we still use acc directly -- it's zeroed on
                // start.
                assign acc_addend[gi][gj] =
                    (accumulate_now && !accum_initialized && saved_accum)
                        ? seed_tile_now[((gi*MMA_N) + gj)*32 +: 32]
                        : acc[gi][gj];

                fp32_fma u_fma (
                    .a      (a_dec[gi]),
                    .b      (b_dec[gj]),
                    .c      (acc_addend[gi][gj]),
                    .result (fma_out[gi][gj])
                );
            end
        end
    endgenerate

    // -------------------------------------------------------------------
    // Pack acc[i][j] into mma_write_tile.
    // Convention: element [i][j] -> bits [(i*MMA_N + j)*32 +: 32].
    // -------------------------------------------------------------------
    function automatic logic [MMA_M*MMA_N*32-1:0] pack_acc;
        /* verilator lint_off WIDTHCONCAT */
        logic [MMA_M*MMA_N*32-1:0] out;
        /* verilator lint_on WIDTHCONCAT */
        int i, j;
        begin
            out = TILE_ZERO;
            for (i = 0; i < MMA_M; i++) begin
                for (j = 0; j < MMA_N; j++) begin
                    out[((i*MMA_N) + j)*32 +: 32] = acc[i][j];
                end
            end
            return out;
        end
    endfunction

    // -------------------------------------------------------------------
    // Sequential logic.
    // -------------------------------------------------------------------
    integer i, j;

    // Default values for combinational helpers when not accumulating.
    always_comb begin
        // Override below in the FSM block (a_data_now / b_data_now /
        // accumulate_now / seed_tile_now). Default to safe zeros.
        a_data_now     = '0;
        b_data_now     = '0;
        accumulate_now = 1'b0;
        seed_tile_now  = TILE_ZERO;

        if (state == S_COMPUTE) begin
            // Capture rd_*_valid (data arriving THIS cycle).
            automatic logic a_arrives = rd_a_valid;
            automatic logic b_arrives = rd_b_valid;
            automatic logic next_pa = pa_valid || a_arrives;
            automatic logic next_pb = pb_valid || b_arrives;
            accumulate_now = next_pa && next_pb;
            a_data_now = pa_valid ? pa_data : rd_a_data;
            b_data_now = pb_valid ? pb_data : rd_b_data;
            seed_tile_now = tmem_seed_valid ? tmem_seed_tile : mma_rd_tile;
        end
    end

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
                    acc[i][j] <= 32'd0;
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
                                    acc[i][j] <= 32'd0;
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
                    // See header comment + accumulate_now / a_data_now /
                    // b_data_now / seed_tile_now in the always_comb above.

                    automatic logic a_arrives = rd_a_valid;
                    automatic logic b_arrives = rd_b_valid;
                    automatic logic next_pa = pa_valid || a_arrives;
                    automatic logic next_pb = pb_valid || b_arrives;
                    // (accumulate_now / a_data_now / b_data_now are the same
                    // values as in the always_comb block above; we recompute
                    // them as local automatics for clarity.)

                    automatic logic a_just_success =
                        rd_a_en && !rd_a_stall_in;
                    automatic logic b_just_success =
                        rd_b_en && !rd_b_stall_in;
                    automatic logic a_inflight_after =
                        (a_inflight && !a_arrives) || a_just_success;
                    automatic logic b_inflight_after =
                        (b_inflight && !b_arrives) || b_just_success;

                    automatic logic [31:0] next_collect_k =
                        accumulate_now ? cur_collect_k + 32'd1 : cur_collect_k;
                    automatic logic pa_after = accumulate_now ? 1'b0 : next_pa;
                    automatic logic pb_after = accumulate_now ? 1'b0 : next_pb;
                    automatic logic a_inflight_after2 =
                        accumulate_now ? 1'b0 : a_inflight_after;
                    automatic logic b_inflight_after2 =
                        accumulate_now ? 1'b0 : b_inflight_after;

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

                    // ---- ACCUMULATE (latch FMA outputs) ----
                    if (accumulate_now) begin
                        for (i = 0; i < MMA_M; i++) begin
                            for (j = 0; j < MMA_N; j++) begin
                                acc[i][j] <= fma_out[i][j];
                            end
                        end
                        accum_initialized <= 1'b1;

                        if (accum_k + 32'd1 == MMA_K) begin
                            state <= S_WRITEBACK;
                        end
                        accum_k <= accum_k + 32'd1;
                        issue_k <= cur_collect_k + 32'd1;
                    end

                    // ---- TMEM seed latch ----
                    if (mma_rd_valid && !tmem_seed_valid) begin
                        tmem_seed_valid <= 1'b1;
                        tmem_seed_tile  <= mma_rd_tile;
                    end
                end

                S_WRITEBACK: begin
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
