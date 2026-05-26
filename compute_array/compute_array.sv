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

// BCAST_PIPE adds N register stages on the broadcast nets from cmd_unit
// to the 32 skew_lanes and 1024 mac cells. Each stage shortens the worst
// wire from ~1.5 mm (corner-to-corner at chip scale) to ~1.5/(N+1) mm.
// Set via -DBCAST_PIPE=N to sv2v.
//
// Functional model with BCAST_PIPE=N: parent inserts N forward flops on
// every cmd_unit->cell/skew_lane net (push_*, drain_*, scrub_en), and
// symmetric N output flops on every cmd_unit->chip-external completion
// signal (mma_busy/done, arrive_*, drain_busy/done/row_valid/row_idx/
// row_last). cmd_unit's internal FSM is unmodified; the symmetric output
// pipe slips every externally-visible completion event by N cycles so it
// arrives in lockstep with the cells that actually finished the work.
// rd_*_en/addr are NOT delayed (SMEM lives at chip-natural time).
// pymodel ComputeArray takes a bcast_pipe= ctor arg that models the same
// shift registers so the cocotb cycle-by-cycle compare still matches.
`ifndef BCAST_PIPE
`define BCAST_PIPE 0
`endif
`ifndef MMA_M
`define MMA_M 32
`endif
`ifndef MMA_N
`define MMA_N 32
`endif
`ifndef MMA_K
`define MMA_K 32
`endif

module compute_array #(
    parameter int MMA_M      = `MMA_M,
    parameter int MMA_N      = `MMA_N,
    parameter int MMA_K      = `MMA_K,
    parameter int N_SLOTS    = 4,
    parameter int BCAST_PIPE = `BCAST_PIPE
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

    // Pipelined copies of cmd_unit's broadcast outputs. Width matches
    // the source signals exactly. Consumed by skew_lanes (push_*) and
    // by every mac cell (cells_drain_* / scrub_en).
    logic                       push_now_piped;
    logic [MMA_M*8-1:0]         push_a_bytes_piped;
    logic [MMA_N*8-1:0]         push_b_bytes_piped;
    logic [SLOT_W-1:0]          push_slot_piped;
    logic                       push_accum_piped;
    logic                       cells_drain_en_piped;
    logic [SLOT_W-1:0]          cells_drain_slot_piped;
    logic                       scrub_en_piped;

    // cmd_unit completion outputs (before the output pipe). These are
    // re-driven onto the chip output ports through a matching N-stage
    // shift register so external observers see them aligned with the
    // forward-piped cell activity.
    logic                       u_mma_busy;
    logic                       u_mma_done;
    logic                       u_arrive_en;
    logic [31:0]                u_arrive_bar_id;
    logic                       u_drain_busy;
    logic                       u_drain_done;
    logic                       u_drain_row_valid;
    logic [$clog2(MMA_M)-1:0]   u_drain_row_idx;
    logic                       u_drain_last;

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
        .mma_busy             (u_mma_busy),
        .mma_done             (u_mma_done),
        .arrive_en            (u_arrive_en),
        .arrive_bar_id        (u_arrive_bar_id),
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
        .drain_busy           (u_drain_busy),
        .drain_done           (u_drain_done),
        .drain_row_valid      (u_drain_row_valid),
        .drain_row_idx        (u_drain_row_idx),
        .drain_last           (u_drain_last),
        .push_now_o           (push_now),
        .push_a_bytes         (push_a_bytes),
        .push_b_bytes         (push_b_bytes),
        .push_slot_o          (push_slot),
        .push_accum_o         (push_accum),
        .drain_en_o           (cells_drain_en),
        .drain_slot_to_cells  (cells_drain_slot)
    );

    // ------------------------------------------------------------------
    // Broadcast pipeline. `BCAST_PIPE=0 is a direct wire; >0 inserts N
    // D-FF stages on every broadcast signal so the long wire from cmd_unit
    // (SW corner) to the NE-most consumer is cut into segments of
    // ~1.5/(N+1) mm each. Sets the achievable Fmax of the hierarchical
    // layout. Resolved at sv2v preprocess time (`BCAST_PIPE macro), not
    // at parameter elaboration, so sv2v emits the right code per variant.
    // ------------------------------------------------------------------
    // Size-safe array decl: PIPE_SZ is at least 1 even when BCAST_PIPE=0.
    // The unused element gets optimized away by yosys; the generate-if
    // below decides whether we emit a real shift register or direct wires.
    localparam int PIPE_SZ = (BCAST_PIPE > 0) ? BCAST_PIPE : 1;

    logic                       pn_pipe  [0:PIPE_SZ-1];
    logic [MMA_M*8-1:0]         pa_pipe  [0:PIPE_SZ-1];
    logic [MMA_N*8-1:0]         pb_pipe  [0:PIPE_SZ-1];
    logic [SLOT_W-1:0]          ps_pipe  [0:PIPE_SZ-1];
    logic                       pac_pipe [0:PIPE_SZ-1];
    logic                       dre_pipe [0:PIPE_SZ-1];
    logic [SLOT_W-1:0]          drs_pipe [0:PIPE_SZ-1];
    logic                       sc_pipe  [0:PIPE_SZ-1];

    generate
        if (BCAST_PIPE > 0) begin : gen_bcast_pipe
            always_ff @(posedge clk) begin
                pn_pipe[0]  <= push_now;
                pa_pipe[0]  <= push_a_bytes;
                pb_pipe[0]  <= push_b_bytes;
                ps_pipe[0]  <= push_slot;
                pac_pipe[0] <= push_accum;
                dre_pipe[0] <= cells_drain_en;
                drs_pipe[0] <= cells_drain_slot;
                sc_pipe[0]  <= scrub_en;
                for (int s = 1; s < BCAST_PIPE; s++) begin
                    pn_pipe[s]  <= pn_pipe[s-1];
                    pa_pipe[s]  <= pa_pipe[s-1];
                    pb_pipe[s]  <= pb_pipe[s-1];
                    ps_pipe[s]  <= ps_pipe[s-1];
                    pac_pipe[s] <= pac_pipe[s-1];
                    dre_pipe[s] <= dre_pipe[s-1];
                    drs_pipe[s] <= drs_pipe[s-1];
                    sc_pipe[s]  <= sc_pipe[s-1];
                end
            end
            assign push_now_piped         = pn_pipe[PIPE_SZ-1];
            assign push_a_bytes_piped     = pa_pipe[PIPE_SZ-1];
            assign push_b_bytes_piped     = pb_pipe[PIPE_SZ-1];
            assign push_slot_piped        = ps_pipe[PIPE_SZ-1];
            assign push_accum_piped       = pac_pipe[PIPE_SZ-1];
            assign cells_drain_en_piped   = dre_pipe[PIPE_SZ-1];
            assign cells_drain_slot_piped = drs_pipe[PIPE_SZ-1];
            assign scrub_en_piped         = sc_pipe[PIPE_SZ-1];
        end else begin : gen_bcast_direct
            assign push_now_piped         = push_now;
            assign push_a_bytes_piped     = push_a_bytes;
            assign push_b_bytes_piped     = push_b_bytes;
            assign push_slot_piped        = push_slot;
            assign push_accum_piped       = push_accum;
            assign cells_drain_en_piped   = cells_drain_en;
            assign cells_drain_slot_piped = cells_drain_slot;
            assign scrub_en_piped         = scrub_en;
        end
    endgenerate

    // ------------------------------------------------------------------
    // Output pipe: BCAST_PIPE D-FF stages on every cmd_unit -> chip
    // completion signal. Symmetric with the forward pipe above: cells
    // see push/drain N cycles after cmd_unit emits them, so external
    // observers must see mma_done / drain_row_valid / etc. N cycles
    // later too, or they'll latch results before cells have written
    // them. rd_*_en/addr are NOT in this pipe — SMEM lives at the
    // chip boundary and cmd_unit's FSM expects round-trip at the
    // natural clock-cycle latency. Without this pipe (the original
    // BCAST_PIPE scaffolding) the chip is structurally sane but the
    // cocotb cycle-by-cycle compare drifts by N.
    // ------------------------------------------------------------------
    logic                       mb_pipe  [0:PIPE_SZ-1];
    logic                       md_pipe  [0:PIPE_SZ-1];
    logic                       ae_pipe  [0:PIPE_SZ-1];
    logic [31:0]                ab_pipe  [0:PIPE_SZ-1];
    logic                       db_pipe  [0:PIPE_SZ-1];
    logic                       dd_pipe  [0:PIPE_SZ-1];
    logic                       drv_pipe [0:PIPE_SZ-1];
    logic [$clog2(MMA_M)-1:0]   dri_pipe [0:PIPE_SZ-1];
    logic                       dl_pipe  [0:PIPE_SZ-1];

    generate
        if (BCAST_PIPE > 0) begin : gen_out_pipe
            always_ff @(posedge clk) begin
                if (reset) begin
                    for (int s = 0; s < BCAST_PIPE; s++) begin
                        mb_pipe[s]  <= 1'b0;
                        md_pipe[s]  <= 1'b0;
                        ae_pipe[s]  <= 1'b0;
                        ab_pipe[s]  <= 32'd0;
                        db_pipe[s]  <= 1'b0;
                        dd_pipe[s]  <= 1'b0;
                        drv_pipe[s] <= 1'b0;
                        dri_pipe[s] <= '0;
                        dl_pipe[s]  <= 1'b0;
                    end
                end else begin
                    mb_pipe[0]  <= u_mma_busy;
                    md_pipe[0]  <= u_mma_done;
                    ae_pipe[0]  <= u_arrive_en;
                    ab_pipe[0]  <= u_arrive_bar_id;
                    db_pipe[0]  <= u_drain_busy;
                    dd_pipe[0]  <= u_drain_done;
                    drv_pipe[0] <= u_drain_row_valid;
                    dri_pipe[0] <= u_drain_row_idx;
                    dl_pipe[0]  <= u_drain_last;
                    for (int s = 1; s < BCAST_PIPE; s++) begin
                        mb_pipe[s]  <= mb_pipe[s-1];
                        md_pipe[s]  <= md_pipe[s-1];
                        ae_pipe[s]  <= ae_pipe[s-1];
                        ab_pipe[s]  <= ab_pipe[s-1];
                        db_pipe[s]  <= db_pipe[s-1];
                        dd_pipe[s]  <= dd_pipe[s-1];
                        drv_pipe[s] <= drv_pipe[s-1];
                        dri_pipe[s] <= dri_pipe[s-1];
                        dl_pipe[s]  <= dl_pipe[s-1];
                    end
                end
            end
            assign mma_busy        = mb_pipe[PIPE_SZ-1];
            assign mma_done        = md_pipe[PIPE_SZ-1];
            assign arrive_en       = ae_pipe[PIPE_SZ-1];
            assign arrive_bar_id   = ab_pipe[PIPE_SZ-1];
            assign drain_busy      = db_pipe[PIPE_SZ-1];
            assign drain_done      = dd_pipe[PIPE_SZ-1];
            assign drain_row_valid = drv_pipe[PIPE_SZ-1];
            assign drain_row_idx   = dri_pipe[PIPE_SZ-1];
            assign drain_last      = dl_pipe[PIPE_SZ-1];
        end else begin : gen_out_direct
            assign mma_busy        = u_mma_busy;
            assign mma_done        = u_mma_done;
            assign arrive_en       = u_arrive_en;
            assign arrive_bar_id   = u_arrive_bar_id;
            assign drain_busy      = u_drain_busy;
            assign drain_done      = u_drain_done;
            assign drain_row_valid = u_drain_row_valid;
            assign drain_row_idx   = u_drain_row_idx;
            assign drain_last      = u_drain_last;
        end
    endgenerate

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
                .push_now   (push_now_piped),
                .push_byte  (push_a_bytes_piped[gi_a*8 +: 8]),
                .push_slot  (push_slot_piped),
                .push_accum (push_accum_piped),
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
                .push_now   (push_now_piped),
                .push_byte  (push_b_bytes_piped[gj_b*8 +: 8]),
                // push_slot / push_accum: functionally unused on b-side
                // (b-skew's edge_slot / edge_accum outputs are dangling — the
                // cell grid takes slot/accum from a-skew, not from here).
                // Reusing cmd_unit's existing broadcast nets instead of tying
                // to '0 avoids 96 per-instance conb_1 tie cells (32 b-skews
                // × 3 bits) and their wires, which otherwise pile up in the
                // std-cell strip just south of the b-skew row and contribute
                // to GR congestion there.
                .push_slot  (push_slot_piped),
                .push_accum (push_accum_piped),
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
                    .drain_en    (cells_drain_en_piped),
                    .drain_slot  (cells_drain_slot_piped),
                    .init_en     (1'b0),
                    .init_slot   ('0),
                    .init_data   (32'd0),
                    .scrub_en    (scrub_en_piped)
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
