// compute_array.sv -- MMA_M x MMA_N systolic grid of mac_tmem_cell.
//
// Phase 7i-4: full systolic refactor.
//
//   Old (Phase 7h): broadcast network. On accumulate_now the K-loop FSM
//   drove the SAME a/b bytes to all 1024 cells in the SAME cycle. Routes
//   were 16 mm long on sky130 — unbuildable. mac_array_small (the 4×4
//   synth proof) is now hardened with cell-to-cell systolic wiring;
//   compute_array follows the same pattern, scaled to MMA_M × MMA_N.
//
// Structure:
//   - Triangular row-skew buffer at the WEST edge: row i's a-byte is
//     delayed by i cycles, so a[i, k] enters cell (i, 0) at cycle k+i.
//   - Triangular col-skew buffer at the NORTH edge: col j's b-byte is
//     delayed by j cycles, so b[k, j] enters cell (0, j) at cycle k+j.
//   - (compute, slot, accum) packet travels east WITH a — also skewed
//     row-by-row.
//   - mac_tmem_cell mesh: a flows east, b flows south, both registered
//     per-cell. 1-cycle hop delay; cell (i, j) computes at cycle k+i+j.
//   - K-loop FSM: same SMEM cross-stall protocol as broadcast version,
//     but instead of broadcasting on accumulate_now, it PUSHES into the
//     skew buffers.
//   - Wave-drain counter: after the K-th push, hold pending_done for
//     M+N-2 cycles so the last K-element propagates to cell (M-1, N-1)
//     before mma_done pulses.
//   - Drain mux: unchanged. drain_en is fanned to row R's cells, drain
//     pipeline samples 2 cycles later.
//
// Per-cell `accum`: on the FIRST compute of a matmul, drives saved_accum
// (so accum=0 zero-initializes, accum=1 reads prior storage); thereafter
// always 1. The accum packet rides the skew with a, so each cell sees
// the right value at the right cycle.

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

    // ---- Drain issue ----
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

    localparam int SLOT_W   = $clog2(N_SLOTS);
    localparam int WAVE_DRAIN_CYCLES = MMA_M + MMA_N - 2;
    // Sized just big enough for WAVE_DRAIN_CYCLES (max 94 for 32×32×32 →
    // 7 bits). Use 32-bit to be safe and parameter-agnostic.
    localparam int WAVE_CNT_W = 32;

    // ------------------------------------------------------------------
    // K-loop FSM state
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        S_IDLE       = 2'd0,
        S_COMPUTE    = 2'd1,
        S_WAVE_DRAIN = 2'd2   // K pushes done, waiting wave to reach (M-1, N-1)
    } mma_state_t;

    mma_state_t state;

    logic [31:0] saved_a_off;
    logic [31:0] saved_b_off;
    logic [31:0] saved_a_stride;
    logic [31:0] saved_b_stride;
    logic [SLOT_W-1:0] saved_slot;
    logic        saved_accum;
    logic [31:0] saved_bar_id;

    // Cross-stall stash (unchanged from broadcast version).
    logic                 pa_valid;
    logic [MMA_M*8-1:0]   pa_data;
    logic                 pb_valid;
    logic [MMA_N*8-1:0]   pb_data;
    logic                 a_inflight;
    logic                 b_inflight;
    logic [31:0]          cur_collect_k;
    logic                 accum_done;
    logic                 pending_done;
    logic [WAVE_CNT_W-1:0] wave_cnt;

    // ------------------------------------------------------------------
    // Drain FSM
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        D_IDLE       = 2'd0,
        D_ISSUE      = 2'd1,
        D_DRAIN_LAST = 2'd2
    } drain_state_t;

    drain_state_t drain_state;

    logic [SLOT_W-1:0] drain_saved_slot;
    logic [$clog2(MMA_M+1)-1:0] drain_next_row;

    logic                       s1_valid;
    logic [$clog2(MMA_M)-1:0]   s1_row;
    logic                       s1_last;
    logic                       s2_valid;
    logic [$clog2(MMA_M)-1:0]   s2_row;
    logic                       s2_last;

    // ------------------------------------------------------------------
    // Push-into-skew control (drives the systolic edge each cycle)
    // ------------------------------------------------------------------
    // `push_now` fires the cycle a fresh K-element enters the array's
    // west/north edges. The associated payload:
    //   push_a_bytes : MMA_M a-bytes (one per row)
    //   push_b_bytes : MMA_N b-bytes (one per col)
    //   push_slot    : slot to write
    //   push_accum   : accum bit for this push (saved_accum on first, 1 after)
    logic                       push_now;
    logic [MMA_M*8-1:0]         push_a_bytes;
    logic [MMA_N*8-1:0]         push_b_bytes;
    logic [SLOT_W-1:0]          push_slot;
    logic                       push_accum;

    // ------------------------------------------------------------------
    // SMEM next-cycle issue (combinational)
    // ------------------------------------------------------------------
    logic                next_rd_a_en;
    logic [31:0]         next_rd_a_addr;
    logic                next_rd_b_en;
    logic [31:0]         next_rd_b_addr;

    // ------------------------------------------------------------------
    // Combinational K-loop intermediates (declared module-scope so Yosys
    // doesn't infer latches when only set inside case arms).
    // ------------------------------------------------------------------
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
        bcast_compute = 1'b0;  // not used — keep declared name out of here
        push_now      = 1'b0;
        push_a_bytes  = '0;
        push_b_bytes  = '0;
        push_slot     = '0;
        push_accum    = 1'b0;

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
                    push_now     = 1'b1;
                    push_a_bytes = a_data_now;
                    push_b_bytes = b_data_now;
                    push_slot    = saved_slot;
                    push_accum   = accum_done ? 1'b1 : saved_accum;
                end
            end

            S_WAVE_DRAIN: begin
                // No SMEM reads, no pushes — just waiting for the wave
                // to propagate through (M-1) + (N-1) cells.
            end

            default: ;
        endcase
    end

    // Defensive declaration (not used after refactor; left as placeholder
    // so the always_comb assignment above is to a real net).
    logic bcast_compute;

    // ------------------------------------------------------------------
    // Drain FSM combinational outputs (this cycle's drain_en row).
    // ------------------------------------------------------------------
    logic                       drain_issue_now;
    logic [$clog2(MMA_M)-1:0]   drain_issue_row;
    logic [SLOT_W-1:0]          bcast_drain_slot;
    always_comb begin
        drain_issue_now  = 1'b0;
        drain_issue_row  = '0;
        bcast_drain_slot = drain_saved_slot;

        if (drain_state == D_ISSUE) begin
            if (drain_next_row < MMA_M[$clog2(MMA_M+1)-1:0]) begin
                drain_issue_now = 1'b1;
                drain_issue_row = drain_next_row[$clog2(MMA_M)-1:0];
            end
        end
    end

    // ------------------------------------------------------------------
    // Systolic west/north edge sources.
    //
    // Each row i has a depth-i shift register for {a, compute, slot,
    // accum}. New entries arrive on push_now=1; otherwise the head is
    // a "no-op" entry (compute=0). Each cycle, the buffer advances by
    // one step. Row i's edge inputs come from the i-th-from-front position
    // of its shift register (so row 0 = front = freshly pushed; row M-1 =
    // depth M-1).
    //
    // Same for cols on b.
    //
    // The shift register also runs in S_WAVE_DRAIN so the wave propagates
    // out to (M-1, N-1); during drain pushes are zero so older entries
    // get NOP'd as they reach the head.
    //
    // Implementation: a single rectangular packed-array buffer for each
    // axis, shift the whole thing one position per cycle. Concretely we
    // model the buffer as `skew_a[M-1]` slots holding {byte} (max-depth
    // is M-1; row 0 reads at position [0] which is "this-cycle push";
    // row i reads at position [i-1]).
    // ------------------------------------------------------------------

    // For row-skew: rows that need delay >0 read out of skew_*.
    // skew_a_pos[k] holds the byte that was pushed k+1 cycles ago.
    // Width = M-1 entries.
    localparam int A_SKEW_DEPTH = (MMA_M > 1) ? (MMA_M - 1) : 1;
    localparam int B_SKEW_DEPTH = (MMA_N > 1) ? (MMA_N - 1) : 1;

    logic [7:0]              skew_a_byte  [MMA_M-1:0][A_SKEW_DEPTH-1:0];
    logic                    skew_a_valid [MMA_M-1:0][A_SKEW_DEPTH-1:0];
    logic [SLOT_W-1:0]       skew_a_slot  [MMA_M-1:0][A_SKEW_DEPTH-1:0];
    logic                    skew_a_accum [MMA_M-1:0][A_SKEW_DEPTH-1:0];

    logic [7:0]              skew_b_byte  [MMA_N-1:0][B_SKEW_DEPTH-1:0];
    logic                    skew_b_valid [MMA_N-1:0][B_SKEW_DEPTH-1:0];

    // Edge inputs into the cell mesh.
    // edge_a[i] / edge_compute[i] / edge_slot[i] / edge_accum[i] feed
    // cell (i, 0). edge_b[j] / edge_b_compute[j] feed cell (0, j).
    logic [7:0]              edge_a       [MMA_M-1:0];
    logic                    edge_compute [MMA_M-1:0];
    logic [SLOT_W-1:0]       edge_slot    [MMA_M-1:0];
    logic                    edge_accum   [MMA_M-1:0];
    logic [7:0]              edge_b       [MMA_N-1:0];

    // Row 0 / col 0 are the "fresh push" path (depth 0):
    //   edge_a[0]    = push_a_bytes[0]    if push_now else 0
    //   edge_compute[0] = push_now
    // Rows >0 read from skew register:
    //   edge_a[i]    = skew_a_byte[i][i-1]
    //   edge_compute[i] = skew_a_valid[i][i-1]
    genvar gi_edge;
    generate
        for (gi_edge = 0; gi_edge < MMA_M; gi_edge++) begin : gen_edge_a
            if (gi_edge == 0) begin : g_first_row
                assign edge_a[0]       = push_now ? push_a_bytes[0*8 +: 8] : 8'd0;
                assign edge_compute[0] = push_now;
                assign edge_slot[0]    = push_slot;
                assign edge_accum[0]   = push_accum;
            end else begin : g_other_rows
                assign edge_a[gi_edge]       = skew_a_byte [gi_edge][gi_edge-1];
                assign edge_compute[gi_edge] = skew_a_valid[gi_edge][gi_edge-1];
                assign edge_slot[gi_edge]    = skew_a_slot [gi_edge][gi_edge-1];
                assign edge_accum[gi_edge]   = skew_a_accum[gi_edge][gi_edge-1];
            end
        end
    endgenerate

    genvar gj_edge;
    generate
        for (gj_edge = 0; gj_edge < MMA_N; gj_edge++) begin : gen_edge_b
            if (gj_edge == 0) begin : g_first_col
                assign edge_b[0] = push_now ? push_b_bytes[0*8 +: 8] : 8'd0;
            end else begin : g_other_cols
                assign edge_b[gj_edge] = skew_b_byte[gj_edge][gj_edge-1];
            end
        end
    endgenerate

    // ------------------------------------------------------------------
    // Cell mesh — cell-to-cell systolic wiring.
    // ------------------------------------------------------------------
    logic [7:0]              a_pipe       [MMA_M-1:0][MMA_N-1:0];
    logic [7:0]              b_pipe       [MMA_M-1:0][MMA_N-1:0];
    logic                    compute_pipe [MMA_M-1:0][MMA_N-1:0];
    logic [SLOT_W-1:0]       slot_pipe    [MMA_M-1:0][MMA_N-1:0];
    logic                    accum_pipe   [MMA_M-1:0][MMA_N-1:0];
    logic [31:0]             drain_data   [MMA_M-1:0][MMA_N-1:0];

    genvar gi, gj;
    generate
        for (gi = 0; gi < MMA_M; gi++) begin : gen_row
            for (gj = 0; gj < MMA_N; gj++) begin : gen_col
                logic        cell_drain_en;
                logic [7:0]  a_in_w;
                logic [7:0]  b_in_w;
                logic        c_in_w;
                logic [SLOT_W-1:0] s_in_w;
                logic        acc_in_w;

                // a, compute, slot, accum from west neighbor; col 0 from edge.
                assign a_in_w   = (gj == 0) ? edge_a[gi]       : a_pipe      [gi][gj-1];
                assign c_in_w   = (gj == 0) ? edge_compute[gi] : compute_pipe[gi][gj-1];
                assign s_in_w   = (gj == 0) ? edge_slot[gi]    : slot_pipe   [gi][gj-1];
                assign acc_in_w = (gj == 0) ? edge_accum[gi]   : accum_pipe  [gi][gj-1];

                // b from north neighbor; row 0 from edge.
                assign b_in_w   = (gi == 0) ? edge_b[gj]       : b_pipe      [gi-1][gj];

                // Drain_en only fired on the chosen row.
                assign cell_drain_en =
                    drain_issue_now &&
                    (drain_issue_row == gi[$clog2(MMA_M)-1:0]);

                mac_tmem_cell u_cell (
                    .clk         (clk),
                    .reset       (reset),
                    .compute_in  (c_in_w),
                    .a_in        (a_in_w),
                    .b_in        (b_in_w),
                    .slot_in     (s_in_w),
                    .accum_in    (acc_in_w),
                    .compute_out (compute_pipe[gi][gj]),
                    .a_out       (a_pipe      [gi][gj]),
                    .b_out       (b_pipe      [gi][gj]),
                    .slot_out    (slot_pipe   [gi][gj]),
                    .accum_out   (accum_pipe  [gi][gj]),
                    .drain_en    (cell_drain_en),
                    .drain_slot  (bcast_drain_slot),
                    .drain_data  (drain_data  [gi][gj]),
                    .init_en     (1'b0),
                    .init_slot   ('0),
                    .init_data   (32'd0),
                    .scrub_en    (scrub_en)
                );
            end
        end
    endgenerate

    // ------------------------------------------------------------------
    // Drain row outputs (combinational from stage-2 of the drain pipeline).
    // ------------------------------------------------------------------
    assign drain_row_valid = s2_valid;
    assign drain_row_idx   = s2_row;
    assign drain_last      = s2_valid && s2_last;

    genvar gj_drain;
    generate
        for (gj_drain = 0; gj_drain < MMA_N; gj_drain++) begin : g_drain_pack
            assign drain_row_data[gj_drain*32 +: 32] =
                s2_valid ? drain_data[s2_row][gj_drain] : 32'd0;
        end
    endgenerate

    // ------------------------------------------------------------------
    // Sequential
    // ------------------------------------------------------------------
    integer i_r;
    integer i_d;
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

            // Clear skew buffers.
            for (i_r = 0; i_r < MMA_M; i_r++) begin
                for (i_d = 0; i_d < A_SKEW_DEPTH; i_d++) begin
                    skew_a_byte [i_r][i_d] <= 8'd0;
                    skew_a_valid[i_r][i_d] <= 1'b0;
                    skew_a_slot [i_r][i_d] <= '0;
                    skew_a_accum[i_r][i_d] <= 1'b0;
                end
            end
            for (i_r = 0; i_r < MMA_N; i_r++) begin
                for (i_d = 0; i_d < B_SKEW_DEPTH; i_d++) begin
                    skew_b_byte [i_r][i_d] <= 8'd0;
                    skew_b_valid[i_r][i_d] <= 1'b0;
                end
            end
        end else begin
            // Default per-cycle pulse outputs.
            mma_done      <= 1'b0;
            arrive_en     <= 1'b0;
            arrive_bar_id <= 32'd0;
            drain_done    <= 1'b0;

            rd_a_en   <= next_rd_a_en;
            rd_a_addr <= next_rd_a_addr;
            rd_b_en   <= next_rd_b_en;
            rd_b_addr <= next_rd_b_addr;

            // ---- Skew buffer advance ----
            // EVERY cycle (regardless of push_now), the skew shifts. Head
            // gets either the push payload (push_now=1) or a no-op
            // (compute_in=0 propagates as "wave hole"). Row 0 / col 0
            // don't have skew entries — they read directly from push.
            //
            // For row i (i >= 1):
            //   skew_a_*[i][0] = push payload's row-i byte if push_now
            //   skew_a_*[i][k] = skew_a_*[i][k-1]  for k=1..A_SKEW_DEPTH-1
            for (i_r = 1; i_r < MMA_M; i_r++) begin
                // Shift down (older entries move to higher index).
                for (i_d = A_SKEW_DEPTH-1; i_d > 0; i_d--) begin
                    skew_a_byte [i_r][i_d] <= skew_a_byte [i_r][i_d-1];
                    skew_a_valid[i_r][i_d] <= skew_a_valid[i_r][i_d-1];
                    skew_a_slot [i_r][i_d] <= skew_a_slot [i_r][i_d-1];
                    skew_a_accum[i_r][i_d] <= skew_a_accum[i_r][i_d-1];
                end
                // New head: push payload (or zero on idle).
                if (push_now) begin
                    skew_a_byte [i_r][0] <= push_a_bytes[i_r*8 +: 8];
                    skew_a_valid[i_r][0] <= 1'b1;
                    skew_a_slot [i_r][0] <= push_slot;
                    skew_a_accum[i_r][0] <= push_accum;
                end else begin
                    skew_a_byte [i_r][0] <= 8'd0;
                    skew_a_valid[i_r][0] <= 1'b0;
                    skew_a_slot [i_r][0] <= '0;
                    skew_a_accum[i_r][0] <= 1'b0;
                end
            end
            for (i_r = 1; i_r < MMA_N; i_r++) begin
                for (i_d = B_SKEW_DEPTH-1; i_d > 0; i_d--) begin
                    skew_b_byte [i_r][i_d] <= skew_b_byte [i_r][i_d-1];
                    skew_b_valid[i_r][i_d] <= skew_b_valid[i_r][i_d-1];
                end
                if (push_now) begin
                    skew_b_byte [i_r][0] <= push_b_bytes[i_r*8 +: 8];
                    skew_b_valid[i_r][0] <= 1'b1;
                end else begin
                    skew_b_byte [i_r][0] <= 8'd0;
                    skew_b_valid[i_r][0] <= 1'b0;
                end
            end

            // ---- K-loop FSM ----
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
                            // Last K element just pushed. Start wave drain.
                            state    <= S_WAVE_DRAIN;
                            wave_cnt <= WAVE_DRAIN_CYCLES[WAVE_CNT_W-1:0];
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
                    // Wait WAVE_DRAIN_CYCLES so the last K-element
                    // propagates from (0, 0) to (M-1, N-1).
                    if (wave_cnt == 0) begin
                        pending_done <= 1'b1;
                        state        <= S_IDLE;
                    end else begin
                        wave_cnt <= wave_cnt - 1;
                    end
                end

                default: ;
            endcase

            // ---- Done pulse latency: pulse one cycle after pending_done ----
            if (pending_done) begin
                mma_done      <= 1'b1;
                arrive_en     <= 1'b1;
                arrive_bar_id <= saved_bar_id;
                pending_done  <= 1'b0;
                mma_busy      <= 1'b0;
                accum_done    <= 1'b0;
            end

            // ---- Drain FSM ----
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
                    if (drain_issue_now) begin
                        s1_valid <= 1'b1;
                        s1_row   <= drain_issue_row;
                        s1_last  <= (drain_next_row == MMA_M - 1);
                        drain_next_row <= drain_next_row + 1;
                    end
                    s2_valid <= s1_valid;
                    s2_row   <= s1_row;
                    s2_last  <= s1_last;

                    drain_done <= (s2_valid && s2_last);

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
