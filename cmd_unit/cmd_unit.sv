// cmd_unit.sv -- compute_array control: K-loop FSM + drain pulse.
//
// Phase 7i-7: skew buffers moved out to skew_lane macros (one per row + one
// per col, instantiated by compute_array). cmd_unit no longer holds those
// 21k FFs — it is now just the K-loop sequencer and drain-pulse generator.
//
// Outputs that compute_array routes to skew_lanes:
//   - push_now (broadcast)
//   - push_a_bytes[M*8] : each a-side lane[i] picks its byte
//   - push_b_bytes[N*8] : each b-side lane[j] picks its byte
//   - push_slot, push_accum : broadcast
//
// Outputs to chip-level (cmdproc / STORE):
//   - mma_busy, mma_done, arrive_en, arrive_bar_id
//   - rd_a_en, rd_a_addr, rd_b_en, rd_b_addr
//   - drain_busy, drain_done, drain_row_valid, drain_row_idx, drain_last
//   - drain_en_o, drain_slot_to_cells (broadcast to cells)

module cmd_unit #(
    parameter int MMA_M   = 32,
    parameter int MMA_N   = 32,
    parameter int MMA_K   = 32,
    parameter int N_SLOTS = 4
) (
    input  logic                          clk,
    input  logic                          reset,

    // ---- Issue from cmdproc ----
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

    // ---- SMEM read interface ----
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

    // ---- Drain issue ----
    input  logic                          drain_issue,
    input  logic [$clog2(N_SLOTS)-1:0]    drain_slot,
    output logic                          drain_busy,
    output logic                          drain_done,
    output logic                          drain_row_valid,
    output logic [$clog2(MMA_M)-1:0]      drain_row_idx,
    output logic                          drain_last,

    // ---- Push outputs to skew_lanes ----
    output logic                          push_now_o,
    output logic [MMA_M*8-1:0]            push_a_bytes,
    output logic [MMA_N*8-1:0]            push_b_bytes,
    output logic [$clog2(N_SLOTS)-1:0]    push_slot_o,
    output logic                          push_accum_o,

    // ---- Drain control to cells (broadcast) ----
    output logic                          drain_en_o,
    output logic [$clog2(N_SLOTS)-1:0]    drain_slot_to_cells
);

    localparam int SLOT_W = $clog2(N_SLOTS);
    localparam int WAVE_DRAIN_CYCLES = MMA_M + MMA_N - 2;

    // ------------------------------------------------------------------
    // K-loop FSM
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        S_IDLE       = 2'd0,
        S_COMPUTE    = 2'd1,
        S_WAVE_DRAIN = 2'd2
    } mma_state_t;

    mma_state_t state;

    logic [31:0] saved_a_off;
    logic [31:0] saved_b_off;
    logic [31:0] saved_a_stride;
    logic [31:0] saved_b_stride;
    logic [SLOT_W-1:0] saved_slot;
    logic        saved_accum;
    logic [31:0] saved_bar_id;

    logic                 pa_valid;
    logic [MMA_M*8-1:0]   pa_data;
    logic                 pb_valid;
    logic [MMA_N*8-1:0]   pb_data;
    logic                 a_inflight;
    logic                 b_inflight;
    logic [31:0]          cur_collect_k;
    logic                 accum_done;
    logic                 pending_done;
    logic [31:0]          wave_cnt;

    // ------------------------------------------------------------------
    // K-loop combinational outputs
    // ------------------------------------------------------------------
    logic                next_rd_a_en;
    logic [31:0]         next_rd_a_addr;
    logic                next_rd_b_en;
    logic [31:0]         next_rd_b_addr;
    logic                push_now;
    logic [MMA_M*8-1:0]  push_a_data;
    logic [MMA_N*8-1:0]  push_b_data;
    logic [SLOT_W-1:0]   push_slot;
    logic                push_accum;

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
        push_now    = 1'b0;
        push_a_data = '0;
        push_b_data = '0;
        push_slot   = '0;
        push_accum  = 1'b0;

        next_rd_a_en   = 1'b0;
        next_rd_a_addr = 32'd0;
        next_rd_b_en   = 1'b0;
        next_rd_b_addr = 32'd0;

        a_arrives = 1'b0;
        b_arrives = 1'b0;
        next_pa = 1'b0;
        next_pb = 1'b0;
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
                    push_now    = 1'b1;
                    push_a_data = a_data_now;
                    push_b_data = b_data_now;
                    push_slot   = saved_slot;
                    push_accum  = accum_done ? 1'b1 : saved_accum;
                end
            end
            S_WAVE_DRAIN: ;
            default: ;
        endcase
    end

    // Expose push payload to skew_lanes.
    assign push_now_o   = push_now;
    assign push_a_bytes = push_a_data;
    assign push_b_bytes = push_b_data;
    assign push_slot_o  = push_slot;
    assign push_accum_o = push_accum;

    // ------------------------------------------------------------------
    // Drain FSM (single-pulse broadcast + M-cycle output stream)
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        D_IDLE   = 2'd0,
        D_PULSE  = 2'd1,
        D_STREAM = 2'd2,
        D_DONE   = 2'd3
    } drain_state_t;

    drain_state_t drain_state;
    logic [SLOT_W-1:0]            drain_saved_slot;
    logic [$clog2(MMA_M+1)-1:0]   drain_count;

    assign drain_slot_to_cells = drain_saved_slot;

    logic drain_en_reg;
    assign drain_en_o = drain_en_reg;

    logic                       drain_row_valid_reg;
    logic [$clog2(MMA_M)-1:0]   drain_row_idx_reg;
    logic                       drain_last_reg;
    assign drain_row_valid = drain_row_valid_reg;
    assign drain_row_idx   = drain_row_idx_reg;
    assign drain_last      = drain_last_reg;

    // ------------------------------------------------------------------
    // Sequential
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
            wave_cnt        <= '0;

            drain_state         <= D_IDLE;
            drain_busy          <= 1'b0;
            drain_done          <= 1'b0;
            drain_saved_slot    <= '0;
            drain_count         <= '0;
            drain_en_reg        <= 1'b0;
            drain_row_valid_reg <= 1'b0;
            drain_row_idx_reg   <= '0;
            drain_last_reg      <= 1'b0;
        end else begin
            mma_done      <= 1'b0;
            arrive_en     <= 1'b0;
            arrive_bar_id <= 32'd0;
            drain_done    <= 1'b0;

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
                            state    <= S_WAVE_DRAIN;
                            wave_cnt <= WAVE_DRAIN_CYCLES[31:0];
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
                S_WAVE_DRAIN: begin
                    if (wave_cnt == 0) begin
                        pending_done <= 1'b1;
                        state        <= S_IDLE;
                    end else begin
                        wave_cnt <= wave_cnt - 1;
                    end
                end
                default: ;
            endcase

            if (pending_done) begin
                mma_done      <= 1'b1;
                arrive_en     <= 1'b1;
                arrive_bar_id <= saved_bar_id;
                pending_done  <= 1'b0;
                mma_busy      <= 1'b0;
                accum_done    <= 1'b0;
            end

            // Drain FSM
            drain_en_reg        <= 1'b0;
            drain_row_valid_reg <= 1'b0;
            drain_last_reg      <= 1'b0;

            unique case (drain_state)
                D_IDLE: begin
                    if (drain_issue) begin
                        drain_saved_slot <= drain_slot;
                        drain_count      <= '0;
                        drain_state      <= D_PULSE;
                        drain_busy       <= 1'b1;
                        drain_en_reg     <= 1'b1;
                    end
                end
                D_PULSE: begin
                    drain_state         <= D_STREAM;
                    drain_count         <= '0;
                    drain_row_valid_reg <= 1'b1;
                    drain_row_idx_reg   <= '0;
                    drain_last_reg      <= (MMA_M == 1);
                end
                D_STREAM: begin
                    if (drain_count + 1 < MMA_M[$clog2(MMA_M+1)-1:0]) begin
                        drain_count         <= drain_count + 1;
                        drain_row_valid_reg <= 1'b1;
                        drain_row_idx_reg   <= drain_count + 1;
                        drain_last_reg      <= (drain_count + 2 == MMA_M);
                    end else begin
                        drain_state <= D_DONE;
                    end
                end
                D_DONE: begin
                    drain_state <= D_IDLE;
                    drain_busy  <= 1'b0;
                    drain_done  <= 1'b1;
                end
                default: ;
            endcase
        end
    end

endmodule
