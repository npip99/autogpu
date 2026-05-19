// mac_array_small.sv -- minimal systolic test array for Phase 7i synth proof.
//
// 4x4 grid of mac_tmem_cell leaves wired neighbor-to-neighbor. This is NOT
// the production compute_array (no K-loop FSM, no drain stream pipeline).
// Its only job is to prove that the systolic wiring pattern routes cleanly
// on sky130 with mac_tmem_cell as a hardened macro.
//
// Dataflow:
//   - a enters at the WEST edge, flows EAST through each row.
//   - b enters at the NORTH edge, flows SOUTH through each column.
//   - compute / slot / accum flow east with a (same packet).
//   - drain / scrub are broadcast as in the canonical leaf.
//   - drain_data[i][j] is exposed flat as a single output port.

`default_nettype none

module mac_array_small #(
    parameter int M = 4,
    parameter int N = 4,
    parameter int N_SLOTS = 4
) (
    input  wire                              clk,
    input  wire                              reset,

    // West edge: one a-byte per row, plus the compute packet.
    // edge_a[i*8 +: 8] feeds row i's column 0.
    input  wire [M*8-1:0]                    edge_a,
    input  wire                              edge_compute,
    input  wire [$clog2(N_SLOTS)-1:0]        edge_slot,
    input  wire                              edge_accum,

    // North edge: one b-byte per column.
    // edge_b[j*8 +: 8] feeds col j's row 0.
    input  wire [N*8-1:0]                    edge_b,

    // Drain (broadcast). Production-style: per-row local 4-way mux
    // produces one row's worth of drain (N×32 = 128 bits) per drain_row
    // selection. STORE reads this as one matmul-result-row per cycle.
    // Std-cell mux logic naturally fits the horizontal channels between
    // rows of macros, so wires stay local to a row instead of fanning
    // 16×32 bits across the whole die.
    input  wire                              drain_en,
    input  wire [$clog2(N_SLOTS)-1:0]        drain_slot,
    input  wire [$clog2(M)-1:0]              drain_row_sel,
    output wire [N*32-1:0]                   drain_row_data,

    input  wire                              scrub_en
);

    // Inter-cell pipe arrays.
    //
    // a_pipe[i][j]  = mac(i, j).a_out — feeds mac(i, j+1).a_in
    // b_pipe[i][j]  = mac(i, j).b_out — feeds mac(i+1, j).b_in
    // c_pipe[i][j], s_pipe[i][j], acc_pipe[i][j] flow east with a.
    wire [7:0]                            a_pipe   [0:M-1][0:N-1];
    wire [7:0]                            b_pipe   [0:M-1][0:N-1];
    wire                                  c_pipe   [0:M-1][0:N-1];
    wire [$clog2(N_SLOTS)-1:0]            s_pipe   [0:M-1][0:N-1];
    wire                                  acc_pipe [0:M-1][0:N-1];
    // 2-D unpacked array of each cell's drain_data; muxed into a single
    // 32-bit output port below.
    wire [31:0]                           drain_data_cell [0:M-1][0:N-1];

    genvar gi, gj;
    generate
        for (gi = 0; gi < M; gi = gi + 1) begin : gen_row
            for (gj = 0; gj < N; gj = gj + 1) begin : gen_col
                wire [7:0]                 a_in_w;
                wire [7:0]                 b_in_w;
                wire                       c_in_w;
                wire [$clog2(N_SLOTS)-1:0] s_in_w;
                wire                       acc_in_w;

                // a, compute, slot, accum come from the west neighbor; the
                // first column gets them from the west edge.
                assign a_in_w   = (gj == 0) ? edge_a[gi*8 +: 8] : a_pipe[gi][gj-1];
                assign c_in_w   = (gj == 0) ? edge_compute     : c_pipe[gi][gj-1];
                assign s_in_w   = (gj == 0) ? edge_slot        : s_pipe[gi][gj-1];
                assign acc_in_w = (gj == 0) ? edge_accum       : acc_pipe[gi][gj-1];

                // b comes from the north neighbor; the first row gets b
                // from the north edge.
                assign b_in_w   = (gi == 0) ? edge_b[gj*8 +: 8] : b_pipe[gi-1][gj];

                mac_tmem_cell u_cell (
                    .clk         (clk),
                    .reset       (reset),
                    .compute_in  (c_in_w),
                    .a_in        (a_in_w),
                    .b_in        (b_in_w),
                    .slot_in     (s_in_w),
                    .accum_in    (acc_in_w),
                    .compute_out (c_pipe[gi][gj]),
                    .a_out       (a_pipe[gi][gj]),
                    .b_out       (b_pipe[gi][gj]),
                    .slot_out    (s_pipe[gi][gj]),
                    .accum_out   (acc_pipe[gi][gj]),
                    .drain_en    (drain_en),
                    .drain_slot  (drain_slot),
                    .drain_in    (32'd0),
                    .drain_out   (drain_data_cell[gi][gj]),
                    .init_en     (1'b0),
                    .init_slot   ('0),
                    .init_data   (32'd0),
                    .scrub_en    (scrub_en)
                );

            end
        end
    endgenerate

    // Per-row packed drain bus: drain_per_row[i] holds row i's full
    // N×32 drain_data (cells gj=0..N-1 of row i packed contiguously).
    // Synthesizes into M parallel local muxes — one per row's std-cell
    // strip — instead of one centralized 16-way mux.
    wire [N*32-1:0] drain_per_row [0:M-1];
    genvar dri, drj;
    generate
        for (dri = 0; dri < M; dri = dri + 1) begin : gen_drain_row_pack
            for (drj = 0; drj < N; drj = drj + 1) begin : gen_drain_col_pack
                assign drain_per_row[dri][drj*32 +: 32] =
                    drain_data_cell[dri][drj];
            end
        end
    endgenerate

    // Final M-way row pick — small mux at the chip edge.
    assign drain_row_data = drain_per_row[drain_row_sel];

endmodule

`default_nettype wire
