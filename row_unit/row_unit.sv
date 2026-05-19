// row_unit.sv -- per-row a-skew shift register + drain OR-chain link.
//
// Phase 7i-5: split compute_array into hardenable submodules.
//
// One row_unit handles one row of the systolic array. It does two jobs:
//
//   1. a-skew: row i's a-byte must be delayed by i cycles before reaching
//      cell (i, 0) on the west edge. Implemented as a uniform max-depth
//      (MMA_M - 1) shift register of {valid, byte[7:0], slot, accum}
//      packets, with a tap-select mux. tap_index = 0 routes the live
//      input packet (zero delay); tap_index = k reads stage [k-1].
//      All instances are physically identical; tap_index is hard-wired
//      per instance so the tap mux degenerates after synthesis.
//
//   2. Drain OR-chain: this row's 32 cells emit drain_data; if this row
//      is the one being drained (drain_row_select high), those bits are
//      OR'd into the daisy-chained drain bus and forwarded upstream.
//      The chain enters at drain_chain_in (row i-1's chain_out, or 0
//      for row 0) and exits at drain_chain_out (to row i+1, or to
//      cmd_unit's drain_chain_top for the last row).
//
// Pass-through outputs: cell_drain_en / cell_drain_slot are just the
// broadcast drain controls fanned out to this row's cells.

module row_unit #(
    parameter int MMA_M   = 32,
    parameter int MMA_N   = 32,
    parameter int N_SLOTS = 4
) (
    input  logic                              clk,
    input  logic                              reset,

    // ---- From cmd_unit (broadcast / per-row picks) ----
    input  logic                              push_now,
    input  logic [7:0]                        push_a_byte,
    input  logic [$clog2(N_SLOTS)-1:0]        push_slot,
    input  logic                              push_accum,
    input  logic [$clog2(MMA_M)-1:0]          tap_index,

    input  logic                              drain_row_select,
    input  logic [$clog2(N_SLOTS)-1:0]        drain_slot_to_cells,

    // ---- From this row's 32 cells (drain_data outputs) ----
    input  logic [MMA_N*32-1:0]               drain_data_in,

    // ---- Drain OR-chain ----
    input  logic [MMA_N*32-1:0]               drain_chain_in,

    // ---- To this row's cell (i, 0) west edge ----
    output logic                              edge_compute,
    output logic [7:0]                        edge_a_byte,
    output logic [$clog2(N_SLOTS)-1:0]        edge_slot,
    output logic                              edge_accum,

    // ---- To this row's cells (broadcast drain_en) ----
    output logic                              cell_drain_en,
    output logic [$clog2(N_SLOTS)-1:0]        cell_drain_slot,

    // ---- Drain OR-chain out ----
    output logic [MMA_N*32-1:0]               drain_chain_out
);

    localparam int SLOT_W      = $clog2(N_SLOTS);
    localparam int TAP_W       = $clog2(MMA_M);
    localparam int A_SKEW_DEPTH = (MMA_M > 1) ? (MMA_M - 1) : 1;
    localparam int PKT_W       = 1 + 8 + SLOT_W + 1;  // valid, byte, slot, accum

    // ------------------------------------------------------------------
    // Live (pre-skew) packet — what would be at tap_index = 0.
    // On no-push the packet is a zero/no-op (valid = 0).
    // ------------------------------------------------------------------
    logic               live_valid;
    logic [7:0]         live_byte;
    logic [SLOT_W-1:0]  live_slot;
    logic               live_accum;

    assign live_valid = push_now;
    assign live_byte  = push_now ? push_a_byte : 8'd0;
    assign live_slot  = push_now ? push_slot   : '0;
    assign live_accum = push_now ? push_accum  : 1'b0;

    // ------------------------------------------------------------------
    // a-skew shift register: depth A_SKEW_DEPTH, stage[0] = newest,
    // stage[A_SKEW_DEPTH-1] = oldest. tap_index = k (k >= 1) reads
    // stage[k-1].
    // ------------------------------------------------------------------
    logic               skew_valid [A_SKEW_DEPTH-1:0];
    logic [7:0]         skew_byte  [A_SKEW_DEPTH-1:0];
    logic [SLOT_W-1:0]  skew_slot  [A_SKEW_DEPTH-1:0];
    logic               skew_accum [A_SKEW_DEPTH-1:0];

    integer i_s;
    always_ff @(posedge clk) begin
        if (reset) begin
            for (i_s = 0; i_s < A_SKEW_DEPTH; i_s++) begin
                skew_valid[i_s] <= 1'b0;
                skew_byte [i_s] <= 8'd0;
                skew_slot [i_s] <= '0;
                skew_accum[i_s] <= 1'b0;
            end
        end else begin
            for (i_s = A_SKEW_DEPTH-1; i_s > 0; i_s--) begin
                skew_valid[i_s] <= skew_valid[i_s-1];
                skew_byte [i_s] <= skew_byte [i_s-1];
                skew_slot [i_s] <= skew_slot [i_s-1];
                skew_accum[i_s] <= skew_accum[i_s-1];
            end
            skew_valid[0] <= live_valid;
            skew_byte [0] <= live_byte;
            skew_slot [0] <= live_slot;
            skew_accum[0] <= live_accum;
        end
    end

    // ------------------------------------------------------------------
    // Tap-select mux: MMA_M options. tap_index = 0 -> live packet;
    // tap_index = k (k >= 1) -> skew stage[k-1].
    //
    // Built as an explicit per-bit assign-based selector to dodge the
    // sv2v latch-on-loopvar gotcha. We expand the tap as a packet, then
    // unpack the fields.
    // ------------------------------------------------------------------
    logic [PKT_W-1:0] tap_packet;

    // Flatten live + all skew stages into a single 2D array of packets
    // so we can do a clean indexed select.
    logic [PKT_W-1:0] tap_options [MMA_M-1:0];

    assign tap_options[0] = {live_valid, live_byte, live_slot, live_accum};

    genvar gk;
    generate
        for (gk = 1; gk < MMA_M; gk++) begin : gen_tap_opts
            assign tap_options[gk] = {
                skew_valid[gk-1],
                skew_byte [gk-1],
                skew_slot [gk-1],
                skew_accum[gk-1]
            };
        end
    endgenerate

    assign tap_packet = tap_options[tap_index];

    assign edge_compute = tap_packet[PKT_W-1];
    assign edge_a_byte  = tap_packet[PKT_W-2 -: 8];
    assign edge_slot    = tap_packet[SLOT_W -: SLOT_W];
    assign edge_accum   = tap_packet[0];

    // ------------------------------------------------------------------
    // Drain pass-throughs to this row's cells.
    // ------------------------------------------------------------------
    assign cell_drain_en   = drain_row_select;
    assign cell_drain_slot = drain_slot_to_cells;

    // ------------------------------------------------------------------
    // Drain-select pipeline: cell.drain_en at cycle T → cell.drain_data
    // valid at cycle T+2. The OR-chain gate must use drain_row_select
    // delayed by 2 cycles so it aligns with when the data is real.
    // ------------------------------------------------------------------
    logic drain_sel_d1;
    logic drain_sel_d2;
    always_ff @(posedge clk) begin
        if (reset) begin
            drain_sel_d1 <= 1'b0;
            drain_sel_d2 <= 1'b0;
        end else begin
            drain_sel_d1 <= drain_row_select;
            drain_sel_d2 <= drain_sel_d1;
        end
    end

    // ------------------------------------------------------------------
    // Drain OR-chain: N*32 AND-OR gates. genvar/assign to keep
    // sv2v + yosys happy (no for-in-always_comb).
    // ------------------------------------------------------------------
    genvar gb;
    generate
        for (gb = 0; gb < MMA_N*32; gb++) begin : gen_drain_or
            assign drain_chain_out[gb] =
                drain_chain_in[gb] | (drain_sel_d2 & drain_data_in[gb]);
        end
    endgenerate

endmodule
