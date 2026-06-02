// skew_lane_a.sv -- a-skew variant of skew_lane (B6: pure abutment broadcast chain).
//
// B6 architecture (#40):
//   Each skew_lane_a holds a 260-bit broadcast chain register (push_now +
//   push_slot + push_accum + push_a_bytes 256-bit vector). The chain flows
//   S→N via abutment pins between vertically-stacked skew_a instances.
//   No long fan-out from cmd_unit to distant skew_a's — cmd_unit drives only
//   the chain HEAD (skew_a[0].chain_w_s); each subsequent skew_a[i] receives
//   chain_w_s from skew_a[i-1].chain_e_n via abutment.
//
//   Per stage:
//     - chain_w_s (south edge input, registered to chain_e_n at posedge clk_w)
//     - chain_e_n (north edge output = registered chain_w_s)
//     - this row consumes its byte from chain_w_s[row*8 +: 8] (parent slices
//       the chain_w_s net externally — short stub on the south edge)
//
//   The internal `skew_lane u` is purely combinational (issue #44 stripped
//   the dead 31-stage shift register); the abutment chain register above
//   provides the systolic per-row delay.
//
// Pairs symmetrically with skew_lane_b (chain on W/E sides).

`default_nettype none

module skew_lane_a #(
    parameter int N_SLOTS     = 4,
    parameter int CHAIN_WIDTH = 260   // push_now(1) + push_slot(2) + push_accum(1) + push_a_bytes(256)
) (
    // ---- N/S abutment: clk + broadcast chain --------------------------
    // clk_w / clk_e: matches mac_tmem_cell_tile's clock contract (#40).
    // Despite the _w/_e suffix, in skew_lane_a's pin TCL clk_w sits on
    // the SOUTH edge and clk_e on the NORTH edge so the clock abuts in
    // the vertical (column) direction. The SV port names retain w/e for
    // consistency with skew_lane_b's actual W/E geometry.
    input  wire                          clk_w,
    output wire                          clk_e,
    input  wire                          reset,

    // Broadcast chain: chain_w_s on SOUTH edge, chain_e_n on NORTH edge.
    // Width is CHAIN_WIDTH = 260 (push_now + push_slot + push_accum +
    // push_a_bytes); same width for skew_a and skew_b chains.
    input  wire [CHAIN_WIDTH-1:0]        chain_w_s,
    output wire [CHAIN_WIDTH-1:0]        chain_e_n,

    // ---- Per-row data (parent taps chain_w_s externally) --------------
    // Parent compute_array wires these from chain_w_s slices at the south
    // edge — short stubs.
    input  wire                          push_now,
    input  wire [7:0]                    push_byte,
    input  wire [$clog2(N_SLOTS)-1:0]    push_slot,
    input  wire                          push_accum,

    // ---- Row output to mac mesh col-0 (E edge) ------------------------
    output wire                          edge_valid,
    output wire [7:0]                    edge_byte,
    output wire [$clog2(N_SLOTS)-1:0]    edge_slot,
    output wire                          edge_accum
);
    // Clk passes through unbuffered (S→N abutment carries the clock; see #40).
    assign clk_e = clk_w;

    // Broadcast chain: register the south-side input, expose registered
    // version on the north side. One flop stage per skew_lane instance.
    // 32 instances stacked vertically = 32 chain stages = MMA_M-cycle delay
    // from the chain head (cmd_unit) to the top instance, which exactly
    // matches the systolic schedule (row i sees its byte at cycle i, where
    // the byte was emitted by cmd_unit at cycle 0).
    reg [CHAIN_WIDTH-1:0] chain_reg;
    always @(posedge clk_w) begin
        if (reset) chain_reg <= '0;
        else       chain_reg <= chain_w_s;
    end
    assign chain_e_n = chain_reg;

    // Per-row pass-through (combinational; the abutment chain register
    // above is what provides the systolic delay).
    skew_lane #(.N_SLOTS(N_SLOTS)) u (
        .push_now   (push_now),
        .push_byte  (push_byte),
        .push_slot  (push_slot),
        .push_accum (push_accum),
        .edge_valid (edge_valid),
        .edge_byte  (edge_byte),
        .edge_slot  (edge_slot),
        .edge_accum (edge_accum)
    );
endmodule

`default_nettype wire
