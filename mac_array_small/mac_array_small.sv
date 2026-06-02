// mac_array_small.sv -- minimal systolic test array (4×4 version of compute_array).
//
// Modeled directly on compute_array.sv's mac mesh: same neighbor-to-neighbor
// systolic wiring (a east, b north, drain north-to-south), same top-row drain
// output.
//
// Broadcast wiring (issue #32): the four parent broadcasts (reset,
// drain_en, drain_slot, scrub_en) are propagated W→E through each row
// via the mac_tmem_cell `*_w` / `*_e` abutment feedthrough ports. Parent
// drives only the westernmost column (gj=0); the cell itself acts as a
// wire on M4 (`assign *_e = *_w`), so the signal carries east across
// every row via abutment. No parent routing over the array.
//
// `clk` is still a single broadcast (CTS responsibility, see #33).
//
// Dataflow (matches compute_array.sv):
//   - a enters at the WEST edge, flows EAST through each row (a_pipe).
//   - b enters at the SOUTH edge, flows NORTH up each column (b_pipe).
//   - compute / slot / accum flow east with a (same packet).
//   - drain enters at the NORTH edge (from cell[i+1][j]), flows SOUTH
//     down each column (drain_pipe). The chip output is the TOP row's
//     drain_pipe[0][*] — no per-row mux, identical to compute_array.

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

    // South edge: one b-byte per column (b flows S→N through the array).
    // edge_b[j*8 +: 8] feeds col j's row 0.
    input  wire [N*8-1:0]                    edge_b,

    // Drain bus = TOP row's drain_pipe[0][*] (no per-row mux).
    input  wire                              drain_en,
    input  wire [$clog2(N_SLOTS)-1:0]        drain_slot,
    output wire [N*32-1:0]                   drain_row_data,

    input  wire                              scrub_en
);

    // Inter-cell pipe arrays for systolic data flow.
    wire [7:0]                            a_pipe       [0:M-1][0:N-1];
    wire [7:0]                            b_pipe       [0:M-1][0:N-1];
    wire                                  compute_pipe [0:M-1][0:N-1];
    wire [$clog2(N_SLOTS)-1:0]            slot_pipe    [0:M-1][0:N-1];
    wire                                  accum_pipe   [0:M-1][0:N-1];
    wire [31:0]                           drain_pipe   [0:M-1][0:N-1];

    // Broadcast feedthrough chain (W→E per row).
    // *_chain_w[i][j] = signal entering cell (i, j) on its west edge.
    // *_chain_e[i][j] = signal exiting cell (i, j) on its east edge.
    // Internally each cell asserts `*_e = *_w`, so the chain is a wire.
    wire                       clk_chain_w        [0:M-1][0:N-1];
    wire                       clk_chain_e        [0:M-1][0:N-1];
    wire                       reset_chain_w      [0:M-1][0:N-1];
    wire                       reset_chain_e      [0:M-1][0:N-1];
    wire                       drain_en_chain_w   [0:M-1][0:N-1];
    wire                       drain_en_chain_e   [0:M-1][0:N-1];
    wire [$clog2(N_SLOTS)-1:0] drain_slot_chain_w [0:M-1][0:N-1];
    wire [$clog2(N_SLOTS)-1:0] drain_slot_chain_e [0:M-1][0:N-1];
    wire                       scrub_en_chain_w   [0:M-1][0:N-1];
    wire                       scrub_en_chain_e   [0:M-1][0:N-1];

    genvar gi, gj;
    generate
        for (gi = 0; gi < M; gi = gi + 1) begin : gen_row
            for (gj = 0; gj < N; gj = gj + 1) begin : gen_col
                wire [7:0]                 a_in_w;
                wire [7:0]                 b_in_w;
                wire                       c_in_w;
                wire [$clog2(N_SLOTS)-1:0] s_in_w;
                wire                       acc_in_w;
                wire [31:0]                drain_in_w;

                // a, compute, slot, accum from west neighbor; first column from edge.
                assign a_in_w   = (gj == 0) ? edge_a[gi*8 +: 8] : a_pipe      [gi][gj-1];
                assign c_in_w   = (gj == 0) ? edge_compute     : compute_pipe[gi][gj-1];
                assign s_in_w   = (gj == 0) ? edge_slot        : slot_pipe   [gi][gj-1];
                assign acc_in_w = (gj == 0) ? edge_accum       : accum_pipe  [gi][gj-1];

                // b from south neighbor; first row from edge.
                assign b_in_w   = (gi == 0) ? edge_b[gj*8 +: 8] : b_pipe[gi-1][gj];

                // drain from north neighbor; last row terminates with 0.
                assign drain_in_w = (gi == M-1) ? 32'd0 : drain_pipe[gi+1][gj];

                // Broadcast + clk chain: col 0 from parent, others from W neighbor's _e.
                assign clk_chain_w       [gi][gj] = (gj == 0) ? clk         : clk_chain_e       [gi][gj-1];
                assign reset_chain_w     [gi][gj] = (gj == 0) ? reset       : reset_chain_e     [gi][gj-1];
                assign drain_en_chain_w  [gi][gj] = (gj == 0) ? drain_en    : drain_en_chain_e  [gi][gj-1];
                assign drain_slot_chain_w[gi][gj] = (gj == 0) ? drain_slot  : drain_slot_chain_e[gi][gj-1];
                assign scrub_en_chain_w  [gi][gj] = (gj == 0) ? scrub_en    : scrub_en_chain_e  [gi][gj-1];

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
                    // init_* ports removed from mac_tmem_cell (#40, INVARIANTS R4a)
                    .scrub_en_w   (scrub_en_chain_w  [gi][gj]),
                    .scrub_en_e   (scrub_en_chain_e  [gi][gj])
                );

            end
        end
    endgenerate

    // Chip drain output = top row's drain_out, like compute_array.
    genvar gj_drain;
    generate
        for (gj_drain = 0; gj_drain < N; gj_drain = gj_drain + 1) begin : g_drain_top
            assign drain_row_data[gj_drain*32 +: 32] = drain_pipe[0][gj_drain];
        end
    endgenerate

endmodule

`default_nettype wire
