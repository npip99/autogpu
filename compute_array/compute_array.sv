// compute_array.sv -- MMA_M x MMA_N systolic grid + per-lane skew + cmd_unit.
//
// Phase 7i-7: three-macro hierarchy.
//
//   - 1 cmd_unit        : K-loop FSM + drain pulse generator. Tiny.
//   - MMA_M skew_lane   : a-skew, one per row. Each tap_index = row_index.
//   - MMA_N skew_lane   : b-skew, one per col. Each tap_index = col_index.
//                         b-side instances tie push_slot/push_accum to 0.
//   - MMA_M*MMA_N cells : mac_tmem_cell systolic mesh with south->north drain.
//
// Drain output = top row of cells' drain_out (no mux).

module compute_array #(
    parameter int MMA_M   = 32,
    parameter int MMA_N   = 32,
    parameter int MMA_K   = 32,
    parameter int N_SLOTS = 4
) (
    input  logic                          clk,
    input  logic                          reset,
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
    input  logic                          drain_issue,
    input  logic [$clog2(N_SLOTS)-1:0]    drain_slot,
    output logic                          drain_busy,
    output logic                          drain_done,
    output logic                          drain_row_valid,
    output logic [MMA_N*32-1:0]           drain_row_data,
    output logic [$clog2(MMA_M)-1:0]      drain_row_idx,
    output logic                          drain_last,
    input  logic                          scrub_en
);

    localparam int SLOT_W = $clog2(N_SLOTS);
    // skew_lane DEPTH must be >= max(MMA_M, MMA_N) so tap_index <= depth-1.
    // We make it large enough to cover both axes from the same hardened macro.
    localparam int SKEW_DEPTH = (MMA_M > MMA_N) ? MMA_M : MMA_N;

    // ------------------------------------------------------------------
    // cmd_unit
    // ------------------------------------------------------------------
    logic                       push_now;
    logic [MMA_M*8-1:0]         push_a_bytes;
    logic [MMA_N*8-1:0]         push_b_bytes;
    logic [SLOT_W-1:0]          push_slot;
    logic                       push_accum;
    logic                       cells_drain_en;
    logic [SLOT_W-1:0]          cells_drain_slot;

    cmd_unit u_cmd (
        .clk                  (clk),
        .reset                (reset),
        .mma_issue            (mma_issue),
        .mma_slot             (mma_slot),
        .mma_accum            (mma_accum),
        .mma_bar_id           (mma_bar_id),
        .issue_a_off          (issue_a_off),
        .issue_b_off          (issue_b_off),
        .issue_a_stride       (issue_a_stride),
        .issue_b_stride       (issue_b_stride),
        .mma_busy             (mma_busy),
        .mma_done             (mma_done),
        .arrive_en            (arrive_en),
        .arrive_bar_id        (arrive_bar_id),
        .rd_a_en              (rd_a_en),
        .rd_a_addr            (rd_a_addr),
        .rd_a_data            (rd_a_data),
        .rd_a_valid           (rd_a_valid),
        .rd_a_stall_in        (rd_a_stall_in),
        .rd_b_en              (rd_b_en),
        .rd_b_addr            (rd_b_addr),
        .rd_b_data            (rd_b_data),
        .rd_b_valid           (rd_b_valid),
        .rd_b_stall_in        (rd_b_stall_in),
        .drain_issue          (drain_issue),
        .drain_slot           (drain_slot),
        .drain_busy           (drain_busy),
        .drain_done           (drain_done),
        .drain_row_valid      (drain_row_valid),
        .drain_row_idx        (drain_row_idx),
        .drain_last           (drain_last),
        .push_now_o           (push_now),
        .push_a_bytes         (push_a_bytes),
        .push_b_bytes         (push_b_bytes),
        .push_slot_o          (push_slot),
        .push_accum_o         (push_accum),
        .drain_en_o           (cells_drain_en),
        .drain_slot_to_cells  (cells_drain_slot)
    );

    // ------------------------------------------------------------------
    // a-side skew_lanes: one per row. Each row i has tap_index = i.
    // Outputs feed the west edge of cell (i, 0).
    // ------------------------------------------------------------------
    logic [MMA_M-1:0]            edge_compute;
    logic [MMA_M*8-1:0]          edge_a_bytes_flat;
    logic [MMA_M*SLOT_W-1:0]     edge_slot_flat;
    logic [MMA_M-1:0]            edge_accum;

    genvar gi_a;
    generate
        for (gi_a = 0; gi_a < MMA_M; gi_a++) begin : gen_a_skew
            logic                  ev;
            logic [7:0]            eb;
            logic [SLOT_W-1:0]     es;
            logic                  ea;
            skew_lane_a u_a (
                .clk        (clk),
                .reset      (reset),
                .push_now   (push_now),
                .push_byte  (push_a_bytes[(MMA_M-1-gi_a)*8 +: 8]),
                .push_slot  (push_slot),
                .push_accum (push_accum),
                .tap_index  (gi_a[$clog2(SKEW_DEPTH)-1:0]),
                .edge_valid (ev),
                .edge_byte  (eb),
                .edge_slot  (es),
                .edge_accum (ea)
            );
            assign edge_compute[gi_a]                  = ev;
            assign edge_a_bytes_flat[gi_a*8 +: 8]      = eb;
            assign edge_slot_flat[gi_a*SLOT_W +: SLOT_W] = es;
            assign edge_accum[gi_a]                    = ea;
        end
    endgenerate

    // ------------------------------------------------------------------
    // b-side skew_lanes: one per col. tap_index = col_index.
    // push_slot / push_accum tied to 0 on b-side (only the byte matters).
    // ------------------------------------------------------------------
    logic [MMA_N*8-1:0]          edge_b_bytes_flat;

    genvar gj_b;
    generate
        for (gj_b = 0; gj_b < MMA_N; gj_b++) begin : gen_b_skew
            logic                  ev_unused;
            logic [7:0]            eb;
            logic [SLOT_W-1:0]     es_unused;
            logic                  ea_unused;
            skew_lane_b u_b (
                .clk        (clk),
                .reset      (reset),
                .push_now   (push_now),
                .push_byte  (push_b_bytes[(MMA_N-1-gj_b)*8 +: 8]),
                // push_slot / push_accum: functionally unused on b-side
                // (b-skew's edge_slot / edge_accum outputs are dangling — the
                // cell grid takes slot/accum from a-skew, not from here).
                // Reusing cmd_unit's existing broadcast nets instead of tying
                // to '0 avoids 96 per-instance conb_1 tie cells (32 b-skews
                // × 3 bits) and their wires, which otherwise pile up in the
                // std-cell strip just south of the b-skew row and contribute
                // to GR congestion there.
                .push_slot  (push_slot),
                .push_accum (push_accum),
                .tap_index  (gj_b[$clog2(SKEW_DEPTH)-1:0]),
                .edge_valid (ev_unused),
                .edge_byte  (eb),
                .edge_slot  (es_unused),
                .edge_accum (ea_unused)
            );
            assign edge_b_bytes_flat[gj_b*8 +: 8] = eb;
        end
    endgenerate

    // ------------------------------------------------------------------
    // Cell mesh: systolic east+south compute + south->north drain.
    // ------------------------------------------------------------------
    logic [7:0]              a_pipe       [MMA_M-1:0][MMA_N-1:0];
    logic [7:0]              b_pipe       [MMA_M-1:0][MMA_N-1:0];
    logic                    compute_pipe [MMA_M-1:0][MMA_N-1:0];
    logic [SLOT_W-1:0]       slot_pipe    [MMA_M-1:0][MMA_N-1:0];
    logic                    accum_pipe   [MMA_M-1:0][MMA_N-1:0];
    logic [31:0]             drain_pipe   [MMA_M-1:0][MMA_N-1:0];

    genvar gi, gj;
    generate
        for (gi = 0; gi < MMA_M; gi++) begin : gen_row
            for (gj = 0; gj < MMA_N; gj++) begin : gen_col
                logic [7:0]        a_in_w;
                logic [7:0]        b_in_w;
                logic              c_in_w;
                logic [SLOT_W-1:0] s_in_w;
                logic              acc_in_w;
                logic [31:0]       drain_in_w;

                assign a_in_w   = (gj == 0) ? edge_a_bytes_flat[gi*8 +: 8]      : a_pipe      [gi][gj-1];
                assign c_in_w   = (gj == 0) ? edge_compute[gi]                  : compute_pipe[gi][gj-1];
                assign s_in_w   = (gj == 0) ? edge_slot_flat[gi*SLOT_W +: SLOT_W] : slot_pipe [gi][gj-1];
                assign acc_in_w = (gj == 0) ? edge_accum[gi]                    : accum_pipe[gi][gj-1];
                assign b_in_w   = (gi == 0) ? edge_b_bytes_flat[gj*8 +: 8]      : b_pipe[gi-1][gj];
                assign drain_in_w = (gi == MMA_M-1) ? 32'd0 : drain_pipe[gi+1][gj];

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
                    .drain_in    (drain_in_w),
                    .drain_out   (drain_pipe  [gi][gj]),
                    .drain_en    (cells_drain_en),
                    .drain_slot  (cells_drain_slot),
                    .init_en     (1'b0),
                    .init_slot   ('0),
                    .init_data   (32'd0),
                    .scrub_en    (scrub_en)
                );
            end
        end
    endgenerate

    // ------------------------------------------------------------------
    // Chip drain output = top row's drain_out (gated by drain_row_valid).
    // ------------------------------------------------------------------
    genvar gj_drain;
    generate
        for (gj_drain = 0; gj_drain < MMA_N; gj_drain++) begin : g_drain_top
            assign drain_row_data[gj_drain*32 +: 32] =
                drain_row_valid ? drain_pipe[0][gj_drain] : 32'd0;
        end
    endgenerate

endmodule
