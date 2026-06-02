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

    // B5 (#40): cmd_unit is a hardened black box at synth time. The
    // BCAST_PIPE register stages are baked INTO the cmd_unit LEF (built
    // with -DBCAST_PIPE=1 in the standalone sv2v rule — see
    // tech/sky130/Makefile). Yosys cannot pass parameters into a
    // hardened module, so we instantiate it positionally — the macro's
    // output ports already carry the registered values.
    cmd_unit u_cmd (
        .clk_w                (clk),
        .clk_e                (),
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
    // Broadcast pipeline — B5 (#40) partial absorption.
    //
    // After B5, cmd_unit ITSELF holds the BCAST_PIPE flops for the push_*
    // family (push_now, push_a_bytes, push_b_bytes, push_slot, push_accum).
    // Those outputs are already registered when they leave the hardened
    // cmd_unit macro, so the *_piped signals here are direct wires from
    // cmd_unit's output ports — no parent shift register, no parent flops.
    //
    // Why: the parent pa_pipe/pb_pipe block previously held ~16K parent
    // flops (256-bit BCAST_PIPE register × MMA dimensions × stages). At
    // 32×32 the resizer had to insert buffer chains from those parent
    // flops to each skew_lane macro, congesting the W mac boundary at
    // GRT. Hiding the flops INSIDE cmd_unit's macro eliminates those
    // long parent-flop→skew wires entirely (the placer puts cmd_unit
    // adjacent to skew_a[0] / skew_b[0], so the macro→macro wires are
    // short by construction).
    //
    // Drain pipe (cells_drain_en, cells_drain_slot) and scrub_en stay at
    // parent for now — their fanout is small (drain_en/slot → 1024 mac
    // cells but via existing feedthrough chains; scrub_en is one signal
    // through the same chains). Absorbing those into cmd_unit too would
    // be a follow-up.
    // ------------------------------------------------------------------
    localparam int PIPE_SZ = (BCAST_PIPE > 0) ? BCAST_PIPE : 1;

    logic                       dre_pipe [0:PIPE_SZ-1];
    logic [SLOT_W-1:0]          drs_pipe [0:PIPE_SZ-1];
    logic                       sc_pipe  [0:PIPE_SZ-1];

    generate
        if (BCAST_PIPE > 0) begin : gen_drain_scrub_pipe
            always_ff @(posedge clk) begin
                dre_pipe[0] <= cells_drain_en;
                drs_pipe[0] <= cells_drain_slot;
                sc_pipe[0]  <= scrub_en;
                for (int s = 1; s < BCAST_PIPE; s++) begin
                    dre_pipe[s] <= dre_pipe[s-1];
                    drs_pipe[s] <= drs_pipe[s-1];
                    sc_pipe[s]  <= sc_pipe[s-1];
                end
            end
            assign cells_drain_en_piped   = dre_pipe[PIPE_SZ-1];
            assign cells_drain_slot_piped = drs_pipe[PIPE_SZ-1];
            assign scrub_en_piped         = sc_pipe[PIPE_SZ-1];
        end else begin : gen_drain_scrub_direct
            assign cells_drain_en_piped   = cells_drain_en;
            assign cells_drain_slot_piped = cells_drain_slot;
            assign scrub_en_piped         = scrub_en;
        end
    endgenerate

    // Push family: cmd_unit registers internally (B5), so the *_piped
    // wires are direct from cmd_unit's already-registered outputs.
    assign push_now_piped     = push_now;
    assign push_a_bytes_piped = push_a_bytes;
    assign push_b_bytes_piped = push_b_bytes;
    assign push_slot_piped    = push_slot;
    assign push_accum_piped   = push_accum;

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
    // Broadcast topology (B4, #40): cmd_unit's BCAST_PIPE-registered
    // outputs (push_a_bytes_piped, push_now_piped, etc.) fan out
    // combinationally to every skew_lane. Each skew_lane[i] then uses
    // its own internal DEPTH-1 shift register (tap_index = i) to delay
    // its byte by i cycles — the i-cycle systolic delay lives INSIDE
    // the hardened skew_lane macro, where its flops are hidden from
    // parent CTS / GRT behind a single macro clk pin.
    //
    // This reverts PR #31/#34's parent pa_chain / pb_chain shift
    // registers, which moved the i-cycle delay out to parent level (~16K
    // parent flops, all clocked from chip clk). That parent chain made
    // the chip clk net's fanout balloon to ~8K endpoints at parent,
    // causing GRT mazeRouteMSMDOrder3D to spin for hours on the 32×32
    // build (perf-attached take-2 showed 97% CPU in the maze router on
    // a single iter for >78 min before kill).
    //
    // Setup safety: BCAST_PIPE>=1 must be set in the synth define
    // (compute_array_abut.config.mk uses chip_top_bcast1.v).
    // BCAST_PIPE=1 registers push_a_bytes at cmd_unit's output edge
    // before the 32-way fan-out — same fix PR #27 used to close the
    // -451 ps broadcast setup violation before the chain was introduced.
    //
    // Total flop count is unchanged (~16K delay flops); they just live
    // INSIDE the hardened skew_lane macros (which already contained
    // them — the chain made them dead). No macro re-harden needed.
    // ------------------------------------------------------------------

    // ------------------------------------------------------------------
    // a-side skew_lanes: one per row. Each row i consumes chain stage i;
    // tap_index=0 (skew_lane runs as a live pass-through — the i-cycle
    // systolic delay is now in the chain instead).
    // Outputs feed the west edge of cell (i, 0).
    //
    // Clock distribution: chain clk through the skew_a column via the
    // clk_w/clk_e feedthrough on each hardened skew_lane_a macro (same
    // pattern as the mac mesh below). Parent CTS only drives skew_a[0];
    // skew_a[i] for i>0 takes clk from skew_a[i-1].clk_e at the parent
    // level (a short north-jog wire — set_dont_touch in the SDC keeps
    // the resizer from inserting buffers and breaking the matched-delay
    // chain). Without this, parent CTS sees 32 separate skew_a endpoints
    // and ends up with widely-different insertion delays → hold storm.
    // ------------------------------------------------------------------
    // B6 (#40): pure-abutment broadcast chain. cmd_unit's outputs form the
    // 260-bit chain head ({push_now, push_slot, push_accum, push_a_bytes});
    // each skew_lane_a[i] registers chain_w_s and exposes chain_e_n on its
    // N edge, which abuts skew_a[i+1]'s S edge. Per-row data is tapped at
    // the parent level from each instance's chain_w_s[i*8 +: 8] (the byte
    // for THIS instance's row), with push_now/slot/accum tapped from the
    // common upper bits. No fan-out from cmd_unit to distant skew_a's; the
    // only "long" route is cmd_unit→skew_a[0] (~40 µm at parent).
    //
    // CHAIN_WIDTH layout (LSB→MSB, must match skew_lane_a.sv comment):
    //   [255:0]   push_a_bytes (each row taps its byte at [row*8 +: 8])
    //   [257:256] push_slot (SLOT_W=2 bits)
    //   [258]     push_accum
    //   [259]     push_now
    localparam int CHAIN_W = MMA_M*8 + SLOT_W + 1 + 1;   // = 260 for 32×32

    logic [MMA_M-1:0]            edge_compute;
    logic [MMA_M*8-1:0]          edge_a_bytes_flat;
    logic [MMA_M*SLOT_W-1:0]     edge_slot_flat;
    logic [MMA_M-1:0]            edge_accum;

    logic [CHAIN_W-1:0] sa_chain_w_s [MMA_M-1:0];  // S-edge input to skew_a[i]
    logic [CHAIN_W-1:0] sa_chain_e_n [MMA_M-1:0];  // N-edge output from skew_a[i]
    logic               clk_chain_a_w [MMA_M-1:0];
    logic               clk_chain_a_e [MMA_M-1:0];

    // Chain head: cmd_unit outputs into skew_a[0].chain_w_s
    assign sa_chain_w_s[0] = {push_now, push_accum, push_slot, push_a_bytes};

    genvar gi_a;
    generate
        for (gi_a = 0; gi_a < MMA_M; gi_a++) begin : gen_a_skew
            logic                  ev;
            logic [7:0]            eb;
            logic [SLOT_W-1:0]     es;
            logic                  ea;
            // Chain feedthrough: skew_a[i].chain_w_s = skew_a[i-1].chain_e_n
            // (for i>0); abutment-aligned pins make this a zero-length wire.
            if (gi_a > 0) begin : gen_chain_link
                assign sa_chain_w_s[gi_a] = sa_chain_e_n[gi_a-1];
            end
            assign clk_chain_a_w[gi_a] = (gi_a == 0) ? clk : clk_chain_a_e[gi_a-1];
            skew_lane_a #(.CHAIN_WIDTH(CHAIN_W)) u_a (
                .clk_w      (clk_chain_a_w[gi_a]),
                .clk_e      (clk_chain_a_e[gi_a]),
                .reset      (reset),
                .chain_w_s  (sa_chain_w_s[gi_a]),
                .chain_e_n  (sa_chain_e_n[gi_a]),
                // Parent slice from THIS instance's chain_w_s — tap the
                // byte for row gi_a from the LSB half of the chain bus,
                // and the common control bits from the upper bits.
                .push_byte  (sa_chain_w_s[gi_a][gi_a*8 +: 8]),
                .push_slot  (sa_chain_w_s[gi_a][MMA_M*8 +: SLOT_W]),
                .push_accum (sa_chain_w_s[gi_a][MMA_M*8 + SLOT_W]),
                .push_now   (sa_chain_w_s[gi_a][MMA_M*8 + SLOT_W + 1]),
                .tap_index  ({$clog2(SKEW_DEPTH){1'b0}}),
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
    // b-side skew_lanes: mirror of a-side but the chain hops W→E along
    // the south row instead of S→N along the west column. Chain head is
    // cmd_unit's push_b_bytes (same shared push_now/slot/accum bits).
    // ------------------------------------------------------------------
    logic [MMA_N*8-1:0]          edge_b_bytes_flat;

    logic [CHAIN_W-1:0] sb_chain_w_w [MMA_N-1:0];  // W-edge input to skew_b[j]
    logic [CHAIN_W-1:0] sb_chain_e_e [MMA_N-1:0];  // E-edge output from skew_b[j]
    logic               clk_chain_b_w [MMA_N-1:0];
    logic               clk_chain_b_e [MMA_N-1:0];

    // Chain head: cmd_unit outputs into skew_b[0].chain_w_w
    assign sb_chain_w_w[0] = {push_now, push_accum, push_slot, push_b_bytes};

    genvar gj_b;
    generate
        for (gj_b = 0; gj_b < MMA_N; gj_b++) begin : gen_b_skew
            logic                  ev_unused;
            logic [7:0]            eb;
            logic [SLOT_W-1:0]     es_unused;
            logic                  ea_unused;
            if (gj_b > 0) begin : gen_chain_link
                assign sb_chain_w_w[gj_b] = sb_chain_e_e[gj_b-1];
            end
            assign clk_chain_b_w[gj_b] = (gj_b == 0) ? clk : clk_chain_b_e[gj_b-1];
            skew_lane_b #(.CHAIN_WIDTH(CHAIN_W)) u_b (
                .clk_w      (clk_chain_b_w[gj_b]),
                .clk_e      (clk_chain_b_e[gj_b]),
                .reset      (reset),
                .chain_w_w  (sb_chain_w_w[gj_b]),
                .chain_e_e  (sb_chain_e_e[gj_b]),
                .push_byte  (sb_chain_w_w[gj_b][gj_b*8 +: 8]),
                .push_slot  (sb_chain_w_w[gj_b][MMA_N*8 +: SLOT_W]),
                .push_accum (sb_chain_w_w[gj_b][MMA_N*8 + SLOT_W]),
                .push_now   (sb_chain_w_w[gj_b][MMA_N*8 + SLOT_W + 1]),
                .tap_index  ({$clog2(SKEW_DEPTH){1'b0}}),
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

    // Broadcast feedthrough chain (W→E per row). Parent drives col 0's
    // *_w; the cell asserts *_e = *_w (M4 wire), so abutment carries the
    // signal east through the array — no parent routing over a macro.
    // clk is on the same pattern (#40): the chip's clk pad feeds each
    // row's col-0 clk_w, and clk propagates east through the row via
    // tile-internal `assign clk_e = clk_w` feedthroughs. No parent CTS.
    // Works in both abutted and non-abutted layouts (in the non-abutted
    // case synth optimizes the chain into the same fan-out as before).
    logic              clk_chain_w        [MMA_M-1:0][MMA_N-1:0];
    logic              clk_chain_e        [MMA_M-1:0][MMA_N-1:0];
    logic              reset_chain_w      [MMA_M-1:0][MMA_N-1:0];
    logic              reset_chain_e      [MMA_M-1:0][MMA_N-1:0];
    logic              drain_en_chain_w   [MMA_M-1:0][MMA_N-1:0];
    logic              drain_en_chain_e   [MMA_M-1:0][MMA_N-1:0];
    logic [SLOT_W-1:0] drain_slot_chain_w [MMA_M-1:0][MMA_N-1:0];
    logic [SLOT_W-1:0] drain_slot_chain_e [MMA_M-1:0][MMA_N-1:0];
    logic              scrub_en_chain_w   [MMA_M-1:0][MMA_N-1:0];
    logic              scrub_en_chain_e   [MMA_M-1:0][MMA_N-1:0];

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

                // Broadcast chain: col 0 from cmd_unit's piped output;
                // col j>0 from the W neighbor's _e (abutment-fed).
                // clk follows the same chain — col 0 from the chip clk
                // pad (via `clk` port), col j>0 from W neighbor's clk_e.
                assign clk_chain_w       [gi][gj] = (gj == 0) ? clk                    : clk_chain_e       [gi][gj-1];
                assign reset_chain_w     [gi][gj] = (gj == 0) ? reset                  : reset_chain_e     [gi][gj-1];
                assign drain_en_chain_w  [gi][gj] = (gj == 0) ? cells_drain_en_piped   : drain_en_chain_e  [gi][gj-1];
                assign drain_slot_chain_w[gi][gj] = (gj == 0) ? cells_drain_slot_piped : drain_slot_chain_e[gi][gj-1];
                assign scrub_en_chain_w  [gi][gj] = (gj == 0) ? scrub_en_piped         : scrub_en_chain_e  [gi][gj-1];

                mac_tmem_cell u_cell (
                    .clk_w        (clk_chain_w       [gi][gj]),
                    .clk_e        (clk_chain_e       [gi][gj]),
                    .reset_w      (reset_chain_w     [gi][gj]),
                    .reset_e      (reset_chain_e     [gi][gj]),
                    .compute_in   (c_in_w),
                    .a_in         (a_in_w),
                    .b_in         (b_in_w),
                    .slot_in      (s_in_w),
                    .accum_in     (acc_in_w),
                    .compute_out  (compute_pipe[gi][gj]),
                    .a_out        (a_pipe      [gi][gj]),
                    .b_out        (b_pipe      [gi][gj]),
                    .slot_out     (slot_pipe   [gi][gj]),
                    .accum_out    (accum_pipe  [gi][gj]),
                    .drain_in     (drain_in_w),
                    .drain_out    (drain_pipe  [gi][gj]),
                    .drain_en_w   (drain_en_chain_w  [gi][gj]),
                    .drain_en_e   (drain_en_chain_e  [gi][gj]),
                    .drain_slot_w (drain_slot_chain_w[gi][gj]),
                    .drain_slot_e (drain_slot_chain_e[gi][gj]),
                    .init_en      (1'b0),
                    .init_slot    ('0),
                    .init_data    (32'd0),
                    .scrub_en_w   (scrub_en_chain_w  [gi][gj]),
                    .scrub_en_e   (scrub_en_chain_e  [gi][gj])
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
