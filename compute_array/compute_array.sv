// compute_array.sv -- MMA_M x MMA_N grid of mac_tmem_cell with K-loop sequencer
//                     and row-by-row drain mux.
//
// Phase 7h-2 integration target. See pymodel/compute_array.py for the
// canonical spec; this module must match it cycle-by-cycle.
//
// Structure:
//   - 1024 (MMA_M x MMA_N) mac_tmem_cell leaves on a broadcast network.
//   - K-loop FSM: lifts the pa/pb cross-stall protocol from mma.sv. Issues
//     SMEM rd_a / rd_b reads and drives `compute` on the cell array when
//     both halves of column k have arrived.
//   - Drain mux: one row per cycle. drain_en is fanned out only to row R
//     of cells; drain_data on those cells appears one cycle later and is
//     packed into drain_row_data. drain_last pulses on the final row;
//     drain_done pulses one cycle after drain_last.
//
// Per-cell `accum`: on the FIRST compute of a matmul, drives saved_accum
// (so accum=0 zero-initializes, accum=1 reads prior storage); thereafter
// always 1.
//
// `mma_done` pulses ONE CYCLE AFTER the last compute fires (no separate
// writeback step, since storage commits per-cell on the compute cycle).
// This matches the pymodel's pulse latency.

module compute_array #(
    parameter int MMA_M   = 32,
    parameter int MMA_N   = 32,
    parameter int MMA_K   = 32,
    parameter int N_SLOTS = 4
) (
    input  logic                          clk,
    input  logic                          reset,

    // ---- Issue from cmdproc: matmul ----
    input  logic                          mma_issue,
    input  logic [$clog2(N_SLOTS)-1:0]    mma_slot,
    input  logic                          mma_accum,
    input  logic [31:0]                   mma_bar_id,
    input  logic [31:0]                   issue_a_off,
    input  logic [31:0]                   issue_b_off,
    input  logic [31:0]                   issue_a_stride,
    input  logic [31:0]                   issue_b_stride,
    output logic                          mma_busy,
    output logic                          mma_done,
    output logic                          arrive_en,
    output logic [31:0]                   arrive_bar_id,

    // ---- SMEM operand read ports ----
    output logic                          rd_a_en,
    output logic [31:0]                   rd_a_addr,
    input  logic [MMA_M*8-1:0]            rd_a_data,
    input  logic                          rd_a_valid,
    input  logic                          rd_a_stall_in,
    output logic                          rd_b_en,
    output logic [31:0]                   rd_b_addr,
    input  logic [MMA_N*8-1:0]            rd_b_data,
    input  logic                          rd_b_valid,
    input  logic                          rd_b_stall_in,

    // ---- Drain issue (= old STORE-from-TMEM) ----
    input  logic                          drain_issue,
    input  logic [$clog2(N_SLOTS)-1:0]    drain_slot,
    output logic                          drain_busy,
    output logic                          drain_done,

    // ---- Drain output stream (row-by-row) ----
    output logic                          drain_row_valid,
    output logic [MMA_N*32-1:0]           drain_row_data,
    output logic [$clog2(MMA_M)-1:0]      drain_row_idx,
    output logic                          drain_last,

    // ---- Scrub from reset_seq ----
    input  logic                          scrub_en
);

    // ------------------------------------------------------------------
    // K-loop FSM
    // ------------------------------------------------------------------
    typedef enum logic [0:0] {
        S_IDLE    = 1'b0,
        S_COMPUTE = 1'b1
    } mma_state_t;

    mma_state_t state;

    // Latched issue operands.
    logic [31:0] saved_a_off;
    logic [31:0] saved_b_off;
    logic [31:0] saved_a_stride;
    logic [31:0] saved_b_stride;
    logic [$clog2(N_SLOTS)-1:0] saved_slot;
    logic        saved_accum;
    logic [31:0] saved_bar_id;

    // Pending-arrival cross-stall stash (from mma.sv).
    logic                 pa_valid;
    logic [MMA_M*8-1:0]   pa_data;
    logic                 pb_valid;
    logic [MMA_N*8-1:0]   pb_data;
    logic                 a_inflight;
    logic                 b_inflight;
    logic [31:0]          cur_collect_k;
    logic                 accum_done;
    logic                 pending_done;

    // ------------------------------------------------------------------
    // Drain FSM
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        D_IDLE       = 2'd0,
        D_ISSUE      = 2'd1,
        D_DRAIN_LAST = 2'd2
    } drain_state_t;

    drain_state_t drain_state;

    logic [$clog2(N_SLOTS)-1:0] drain_saved_slot;
    // drain_next_row sized to MMA_M+1 so it can hold the "done" value
    // (== MMA_M) without overflow.
    logic [$clog2(MMA_M+1)-1:0] drain_next_row;

    // ---- Drain pipeline ----
    // Two registered stages tracking row R after its drain_en was driven:
    //   stage_a (s1)  : drain_en was asserted on row R LAST cycle. The cell
    //                   has captured its drain_pending, but cell.drain_data
    //                   is still 0 (the cell commits storage[slot] only on
    //                   the NEXT posedge).
    //   stage_b (s2)  : drain_en was asserted on row R TWO cycles ago. The
    //                   cell has committed drain_data <= storage[slot] at
    //                   the previous posedge, so cell.drain_data is the
    //                   correct value during THIS cycle. We sample it and
    //                   register drain_row_* on the NEXT posedge.
    // The mac_tmem_cell has a 1-cycle drain latency (drain_en at T →
    // drain_data valid at T+1's edge → readable during cycle T+1). To
    // pack and register the row, we need a second cycle (the cell.drain_data
    // is a registered output, not combinational), so total latency from
    // drain_en assertion to drain_row_valid pulse is 2 cycles.
    logic                       s1_valid;
    logic [$clog2(MMA_M)-1:0]   s1_row;
    logic                       s1_last;
    logic                       s2_valid;
    logic [$clog2(MMA_M)-1:0]   s2_row;
    logic                       s2_last;

    // ------------------------------------------------------------------
    // Broadcast inputs (combinational, fed to all cells).
    // ------------------------------------------------------------------
    logic                       bcast_compute;
    logic [MMA_M*8-1:0]         bcast_a_bytes;
    logic [MMA_N*8-1:0]         bcast_b_bytes;
    logic [$clog2(N_SLOTS)-1:0] bcast_slot;
    logic                       bcast_accum;

    // Drain broadcast: drain_en is asserted only on cells in row R.
    logic                       drain_issue_now;
    logic [$clog2(MMA_M)-1:0]   drain_issue_row;
    logic [$clog2(N_SLOTS)-1:0] bcast_drain_slot;

    // ------------------------------------------------------------------
    // SMEM read issue computation (combinational; goes to next-cycle reg).
    // ------------------------------------------------------------------
    logic                next_rd_a_en;
    logic [31:0]         next_rd_a_addr;
    logic                next_rd_b_en;
    logic [31:0]         next_rd_b_addr;

    // ------------------------------------------------------------------
    // FMA / decode helper wires from the broadcast network into cells.
    // ------------------------------------------------------------------
    logic [31:0]                drain_data [MMA_M][MMA_N];

    genvar gi, gj;
    generate
        for (gi = 0; gi < MMA_M; gi++) begin : gen_row
            for (gj = 0; gj < MMA_N; gj++) begin : gen_col
                logic        cell_drain_en;
                assign cell_drain_en = drain_issue_now &&
                                       (drain_issue_row == gi[$clog2(MMA_M)-1:0]);

                mac_tmem_cell #(
                    .N_SLOTS (N_SLOTS)
                ) u_cell (
                    .clk        (clk),
                    .reset      (reset),
                    .compute    (bcast_compute),
                    .a          (bcast_a_bytes[gi*8 +: 8]),
                    .b          (bcast_b_bytes[gj*8 +: 8]),
                    .slot       (bcast_slot),
                    .accum      (bcast_accum),
                    .drain_en   (cell_drain_en),
                    .drain_slot (bcast_drain_slot),
                    .drain_data (drain_data[gi][gj]),
                    // Init port unused for now; pymodel uses backdoor only.
                    .init_en    (1'b0),
                    .init_slot  ('0),
                    .init_data  (32'd0),
                    .scrub_en   (scrub_en)
                );
            end
        end
    endgenerate

    // ------------------------------------------------------------------
    // Combinational logic for K-loop next-state and broadcast outputs.
    // ------------------------------------------------------------------
    // Locals declared at module scope so Yosys doesn't infer latches.
    logic a_arrives;
    logic b_arrives;
    logic next_pa;
    logic next_pb;
    logic accumulate_now;
    logic [MMA_M*8-1:0] a_data_now;
    logic [MMA_N*8-1:0] b_data_now;
    logic a_just_success;
    logic b_just_success;
    logic a_inflight_after;
    logic b_inflight_after;
    logic [31:0] next_collect_k_comb;
    logic pa_after;
    logic pb_after;
    logic a_inflight_after2;
    logic b_inflight_after2;

    always_comb begin
        // Defaults.
        bcast_compute = 1'b0;
        bcast_a_bytes = '0;
        bcast_b_bytes = '0;
        bcast_slot    = '0;
        bcast_accum   = 1'b0;
        next_rd_a_en   = 1'b0;
        next_rd_a_addr = 32'd0;
        next_rd_b_en   = 1'b0;
        next_rd_b_addr = 32'd0;

        a_arrives = 1'b0;
        b_arrives = 1'b0;
        next_pa   = 1'b0;
        next_pb   = 1'b0;
        accumulate_now = 1'b0;
        a_data_now = '0;
        b_data_now = '0;
        a_just_success = 1'b0;
        b_just_success = 1'b0;
        a_inflight_after = 1'b0;
        b_inflight_after = 1'b0;
        next_collect_k_comb = 32'd0;
        pa_after = 1'b0;
        pb_after = 1'b0;
        a_inflight_after2 = 1'b0;
        b_inflight_after2 = 1'b0;

        unique case (state)
            S_IDLE: begin
                if (mma_issue) begin
                    next_rd_a_en   = 1'b1;
                    next_rd_a_addr = issue_a_off;
                    next_rd_b_en   = 1'b1;
                    next_rd_b_addr = issue_b_off;
                end
            end

            S_COMPUTE: begin
                a_arrives = rd_a_valid;
                b_arrives = rd_b_valid;
                next_pa   = pa_valid || a_arrives;
                next_pb   = pb_valid || b_arrives;
                accumulate_now = next_pa && next_pb;
                a_data_now = pa_valid ? pa_data : rd_a_data;
                b_data_now = pb_valid ? pb_data : rd_b_data;

                a_just_success = rd_a_en && !rd_a_stall_in;
                b_just_success = rd_b_en && !rd_b_stall_in;
                a_inflight_after = (a_inflight && !a_arrives) || a_just_success;
                b_inflight_after = (b_inflight && !b_arrives) || b_just_success;

                next_collect_k_comb = accumulate_now ?
                                      (cur_collect_k + 32'd1) : cur_collect_k;
                pa_after = accumulate_now ? 1'b0 : next_pa;
                pb_after = accumulate_now ? 1'b0 : next_pb;
                a_inflight_after2 = accumulate_now ? 1'b0 : a_inflight_after;
                b_inflight_after2 = accumulate_now ? 1'b0 : b_inflight_after;

                if (next_collect_k_comb < MMA_K) begin
                    if (!pa_after && !a_inflight_after2) begin
                        next_rd_a_en   = 1'b1;
                        next_rd_a_addr = saved_a_off
                                       + next_collect_k_comb * saved_a_stride;
                    end
                    if (!pb_after && !b_inflight_after2) begin
                        next_rd_b_en   = 1'b1;
                        next_rd_b_addr = saved_b_off
                                       + next_collect_k_comb * saved_b_stride;
                    end
                end

                if (accumulate_now) begin
                    bcast_compute = 1'b1;
                    bcast_a_bytes = a_data_now;
                    bcast_b_bytes = b_data_now;
                    bcast_slot    = saved_slot;
                    bcast_accum   = accum_done ? 1'b1 : saved_accum;
                end
            end
            default: ;
        endcase
    end

    // ------------------------------------------------------------------
    // Drain FSM combinational outputs (this cycle's drain_en row).
    // ------------------------------------------------------------------
    always_comb begin
        drain_issue_now = 1'b0;
        drain_issue_row = '0;
        bcast_drain_slot = drain_saved_slot;

        if (drain_state == D_ISSUE) begin
            if (drain_next_row < MMA_M[$clog2(MMA_M+1)-1:0]) begin
                drain_issue_now = 1'b1;
                drain_issue_row = drain_next_row[$clog2(MMA_M)-1:0];
            end
        end
    end

    // ------------------------------------------------------------------
    // Drain row outputs (combinational from stage-2 of the drain pipeline).
    //
    // Drain pipeline stages and timing:
    //   Cycle C   : compute_array drives drain_en=1 on row R (combinational
    //               from drain_state == D_ISSUE && drain_next_row < MMA_M).
    //   Cycle C+1 : cell.drain_pending_valid is now 1, but cell.drain_data
    //               is still the old value (commit happens at next edge).
    //               compute_array's s1_valid is high this cycle, marking
    //               "row R was issued last cycle". We don't sample yet.
    //   Cycle C+2 : cell.drain_data has committed storage[slot] during the
    //               edge entering this cycle, so it is now visible
    //               combinationally. s2_valid (= prior s1_valid) is high.
    //               drain_row_* are driven combinationally from s2_* and
    //               cell.drain_data[s2_row][*].
    //
    // Driving drain_row_* combinationally from s2_* matches the
    // pymodel's read-cell-after-tick semantics — registering them would
    // sample cell.drain_data one edge too early (when it's still 0).
    // ------------------------------------------------------------------
    always_comb begin
        drain_row_valid = s2_valid;
        drain_row_idx   = s2_row;
        drain_last      = s2_valid && s2_last;
        drain_row_data  = '0;
        if (s2_valid) begin
            for (int j = 0; j < MMA_N; j++) begin
                drain_row_data[j*32 +: 32] = drain_data[s2_row][j];
            end
        end
    end

    // ------------------------------------------------------------------
    // Sequential logic
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (reset) begin
            state           <= S_IDLE;
            mma_busy        <= 1'b0;
            mma_done        <= 1'b0;
            arrive_en       <= 1'b0;
            arrive_bar_id   <= 32'd0;
            rd_a_en         <= 1'b0;
            rd_a_addr       <= 32'd0;
            rd_b_en         <= 1'b0;
            rd_b_addr       <= 32'd0;
            saved_a_off     <= 32'd0;
            saved_b_off     <= 32'd0;
            saved_a_stride  <= 32'd0;
            saved_b_stride  <= 32'd0;
            saved_slot      <= '0;
            saved_accum     <= 1'b0;
            saved_bar_id    <= 32'd0;
            pa_valid        <= 1'b0;
            pa_data         <= '0;
            pb_valid        <= 1'b0;
            pb_data         <= '0;
            a_inflight      <= 1'b0;
            b_inflight      <= 1'b0;
            cur_collect_k   <= 32'd0;
            accum_done      <= 1'b0;
            pending_done    <= 1'b0;

            drain_state        <= D_IDLE;
            drain_busy         <= 1'b0;
            drain_done         <= 1'b0;
            drain_saved_slot   <= '0;
            drain_next_row     <= '0;
            s1_valid           <= 1'b0;
            s1_row             <= '0;
            s1_last            <= 1'b0;
            s2_valid           <= 1'b0;
            s2_row             <= '0;
            s2_last            <= 1'b0;
        end else begin
            // Default per-cycle pulse outputs (clear; conditionally set below).
            mma_done      <= 1'b0;
            arrive_en     <= 1'b0;
            arrive_bar_id <= 32'd0;
            drain_done    <= 1'b0;

            // ---- K-loop sequential ----
            rd_a_en   <= next_rd_a_en;
            rd_a_addr <= next_rd_a_addr;
            rd_b_en   <= next_rd_b_en;
            rd_b_addr <= next_rd_b_addr;

            unique case (state)
                S_IDLE: begin
                    if (mma_issue) begin
                        saved_a_off    <= issue_a_off;
                        saved_b_off    <= issue_b_off;
                        saved_a_stride <= issue_a_stride;
                        saved_b_stride <= issue_b_stride;
                        saved_slot     <= mma_slot;
                        saved_accum    <= mma_accum;
                        saved_bar_id   <= mma_bar_id;
                        state          <= S_COMPUTE;
                        mma_busy       <= 1'b1;
                        pa_valid       <= 1'b0;
                        pa_data        <= '0;
                        pb_valid       <= 1'b0;
                        pb_data        <= '0;
                        a_inflight     <= 1'b0;
                        b_inflight     <= 1'b0;
                        cur_collect_k  <= 32'd0;
                        accum_done     <= 1'b0;
                    end
                end

                S_COMPUTE: begin
                    if (accumulate_now) begin
                        pa_valid       <= 1'b0;
                        pa_data        <= '0;
                        pb_valid       <= 1'b0;
                        pb_data        <= '0;
                        a_inflight     <= 1'b0;
                        b_inflight     <= 1'b0;
                        cur_collect_k  <= next_collect_k_comb;
                        accum_done     <= 1'b1;
                        if (next_collect_k_comb == MMA_K) begin
                            pending_done <= 1'b1;
                        end
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
                end
                default: ;
            endcase

            // ---- Done pulse latency: pulse one cycle after pending_done set ----
            if (pending_done) begin
                mma_done      <= 1'b1;
                arrive_en     <= 1'b1;
                arrive_bar_id <= saved_bar_id;
                pending_done  <= 1'b0;
                state         <= S_IDLE;
                mma_busy      <= 1'b0;
                accum_done    <= 1'b0;
            end

            // ---- Drain FSM ----
            //
            // Pipeline:
            //   stage 1 (s1_valid) : drain_en was driven on row R this cycle;
            //                        cell will commit drain_pending at next edge.
            //   stage 2 (s2_valid) : cell.drain_data is being committed at the
            //                        edge ENTERING this cycle, so during this
            //                        cycle drain_data is valid and ready to
            //                        sample combinationally.
            //   commit             : at this edge we register drain_row_*
            //                        from s2.
            //
            // Total: 2 cycles from drain_en assertion to drain_row_valid pulse.
            //
            // (mac_tmem_cell has 1 cycle of drain latency by itself; the second
            // cycle is needed because cell.drain_data is a registered output,
            // not combinational, so we can't sample it on the same edge it
            // commits — we must wait one more cycle.)

            // Default stage 1/2: clear them so they only stay high when fresh.
            s1_valid <= 1'b0;
            s2_valid <= 1'b0;

            unique case (drain_state)
                D_IDLE: begin
                    if (drain_issue) begin
                        drain_saved_slot <= drain_slot;
                        drain_next_row   <= '0;
                        drain_state      <= D_ISSUE;
                        drain_busy       <= 1'b1;
                    end
                end

                D_ISSUE: begin
                    // Advance pipeline: this cycle's issued row enters
                    // stage 1; last cycle's stage 1 enters stage 2.
                    if (drain_issue_now) begin
                        s1_valid <= 1'b1;
                        s1_row   <= drain_issue_row;
                        // Use full-width comparison to avoid bit-truncation
                        // surprises with `MMA_M - 1` when MMA_M is a power
                        // of two (MMA_M[clog2(MMA_M)-1:0] = 0).
                        s1_last  <= (drain_next_row == MMA_M - 1);
                        drain_next_row <= drain_next_row + 1;
                    end
                    s2_valid <= s1_valid;
                    s2_row   <= s1_row;
                    s2_last  <= s1_last;

                    // drain_done pulses the cycle AFTER drain_last (combinational
                    // drain_last fires during the cycle when s2_valid && s2_last
                    // are both registered high).
                    drain_done <= (s2_valid && s2_last);

                    // Transition out when all rows issued AND pipeline drained.
                    // The cycle after the final s2_valid && s2_last fires,
                    // s1/s2 are both zero and we can leave ISSUE.
                    if (drain_next_row >= MMA_M[$clog2(MMA_M+1)-1:0]
                        && !drain_issue_now
                        && !s1_valid
                        && !s2_valid) begin
                        drain_state <= D_DRAIN_LAST;
                    end
                end

                D_DRAIN_LAST: begin
                    drain_state <= D_IDLE;
                    drain_busy  <= 1'b0;
                end

                default: ;
            endcase
        end
    end

endmodule
