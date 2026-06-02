// compute_array.sv -- MMA_M x MMA_N systolic grid + per-lane skew + cmd_unit.
//
// Phase 7i-7: three-macro hierarchy.
//
//   - 1 cmd_unit        : K-loop FSM + drain pulse generator. Tiny.
//   - MMA_M skew_lane_a : a-skew, one per row. Stacked S→N along W column;
//                         each instance holds one stage of the 260-bit
//                         broadcast chain register (B6 #40).
//   - MMA_N skew_lane_b : b-skew, one per col. Stacked W→E along S row;
//                         same chain-register layout, mirror axis.
//                         b-side instances tie push_slot/push_accum to 0.
//                         The internal skew_lane is purely combinational
//                         (#44 stripped the dead 31-stage shift register).
//   - MMA_M*MMA_N cells : mac_tmem_cell systolic mesh with south->north drain.
//
// Drain output = top row of cells' drain_out (no mux).
//
// R1 (tech/INVARIANTS.md): this parent design holds macros + wires only.
// The broadcast-pipeline knob `BCAST_PIPE` was deleted in #45 — its
// motivating routing problem was eliminated by B6's per-skew abutment
// chain (skew_lane_a/b internal `chain_w_s/chain_e_n` registers), and
// the parent flops it inserted on the cmd_unit-out / cmd_unit-in nets
// violated R1.
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
    parameter int N_SLOTS    = 4
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

    // ------------------------------------------------------------------
    // cmd_unit
    // ------------------------------------------------------------------
    // push_*  : combinational outputs from cmd_unit's K-loop FSM. Drive
    //           the chain head of each skew_lane stack (skew_a[0],
    //           skew_b[0]) directly — the skew_lane chain registers
    //           provide the broadcast pipeline (B6 #40).
    // cells_drain_* / status outputs : registered inside cmd_unit.
    logic                       push_now;
    logic [MMA_M*8-1:0]         push_a_bytes;
    logic [MMA_N*8-1:0]         push_b_bytes;
    logic [SLOT_W-1:0]          push_slot;
    logic                       push_accum;
    logic                       cells_drain_en;
    logic [SLOT_W-1:0]          cells_drain_slot;

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
    // Broadcast topology (B6, #40): cmd_unit drives the chain HEAD of
    // both skew_lane stacks. Each skew_lane_a/b instance registers the
    // 260-bit chain (push_now + push_slot + push_accum + 256-bit byte
    // vector) at posedge clk_w and exposes it on its opposite-edge
    // abutment pin to the next instance. After 32 stages the byte for
    // row/col i has been delayed i cycles — that's the systolic delay.
    // Per-row data is sliced from chain_w_s[i*8 +: 8] at parent level
    // and fed to the cell mesh via combinational pass-through (#44
    // stripped the dead internal shift register; the chain register
    // is the only delay element now).
    //
    // History: PR #31/#34 used parent pa_chain / pb_chain shift
    // registers (~16K parent flops, all on chip clk). That made the
    // chip clk net's fanout balloon to ~8K endpoints at parent,
    // causing GRT mazeRouteMSMDOrder3D to spin >78 min on 32×32.
    // B6 fixes this by hiding the delay flops inside the hardened
    // skew_lane_a/b chain register, where they share the macro's
    // local clk and don't burden the parent.
    // ------------------------------------------------------------------

    // ------------------------------------------------------------------
    // a-side skew_lanes: one per row. The chain register inside each
    // skew_lane_a provides the i-cycle systolic delay; the internal
    // skew_lane is purely combinational (live pass-through).
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
            // CHAIN_WIDTH is fixed at 260 in the hardened skew_lane_a macro
            // (compile-time default); yosys can't pass parameters into a
            // hardened black box at synth.
            skew_lane_a u_a (
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
            skew_lane_b u_b (
                .clk_w      (clk_chain_b_w[gj_b]),
                .clk_e      (clk_chain_b_e[gj_b]),
                .reset      (reset),
                .chain_w_w  (sb_chain_w_w[gj_b]),
                .chain_e_e  (sb_chain_e_e[gj_b]),
                .push_byte  (sb_chain_w_w[gj_b][gj_b*8 +: 8]),
                .push_slot  (sb_chain_w_w[gj_b][MMA_N*8 +: SLOT_W]),
                .push_accum (sb_chain_w_w[gj_b][MMA_N*8 + SLOT_W]),
                .push_now   (sb_chain_w_w[gj_b][MMA_N*8 + SLOT_W + 1]),
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

                // Broadcast chain: col 0 from cmd_unit's output;
                // col j>0 from the W neighbor's _e (abutment-fed).
                // clk follows the same chain — col 0 from the chip clk
                // pad (via `clk` port), col j>0 from W neighbor's clk_e.
                assign clk_chain_w       [gi][gj] = (gj == 0) ? clk              : clk_chain_e       [gi][gj-1];
                assign reset_chain_w     [gi][gj] = (gj == 0) ? reset            : reset_chain_e     [gi][gj-1];
                assign drain_en_chain_w  [gi][gj] = (gj == 0) ? cells_drain_en   : drain_en_chain_e  [gi][gj-1];
                assign drain_slot_chain_w[gi][gj] = (gj == 0) ? cells_drain_slot : drain_slot_chain_e[gi][gj-1];
                assign scrub_en_chain_w  [gi][gj] = (gj == 0) ? scrub_en         : scrub_en_chain_e  [gi][gj-1];

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
                    // init_* ports only exist in sim (mac_tmem_cell.sv
                    // strips them at hardening — INVARIANTS.md R4a/R4b).
                    // Without this `ifndef guard, Verilator warns UNDRIVEN.
`ifndef SYNTHESIS
                    .init_en   (1'b0),
                    .init_slot ('0),
                    .init_data (32'd0),
`endif
                    .scrub_en_w   (scrub_en_chain_w  [gi][gj]),
                    .scrub_en_e   (scrub_en_chain_e  [gi][gj])
                );
            end
        end
    endgenerate

    // ------------------------------------------------------------------
    // Chip drain output = top row's drain_out (UNGATED — per R5 in
    // tech/INVARIANTS.md). Earlier this was gated by drain_row_valid
    // for "clean waveforms" during invalid cycles, costing 1024 AND2x2
    // cells at parent. The receiver (store.sv:193) gates its write
    // enable on drain_row_valid, so the data lines are don't-care
    // when valid is low. Outputting raw mac data + valid flag is
    // standard handshake convention.
    // ------------------------------------------------------------------
    genvar gj_drain;
    generate
        for (gj_drain = 0; gj_drain < MMA_N; gj_drain++) begin : g_drain_top
            assign drain_row_data[gj_drain*32 +: 32] = drain_pipe[0][gj_drain];
        end
    endgenerate

endmodule
