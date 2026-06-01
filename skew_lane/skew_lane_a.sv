// skew_lane_a.sv -- a-skew variant of skew_lane.
//
// Identical logic to `skew_lane`, but hardened independently so the LEF can
// have W/E pin placement (input pins on west, output pins on east) suitable
// for the a-skew column placed at the left edge of compute_array's cell
// grid. Pin sides are set via tech/sky130/submodules/skew_lane_a/pin_order.cfg.
//
// Pairs symmetrically with skew_lane_b (pins on N/S sides). Both wrappers
// keep compute_array at orientation N for every macro — no rotation,
// uniform PDN.

`default_nettype none

module skew_lane_a #(
    parameter int DEPTH   = 32,
    parameter int N_SLOTS = 4
) (
    // clk_w / clk_e: matches mac_tmem_cell_tile's clock contract (#40).
    // clk_e is an unbuffered combinational pass-through so the abstract
    // .lib has a clk_w → clk_e arc and the parent can chain clk through
    // multiple skew_lanes via abutment.
    input  wire                          clk_w,
    output wire                          clk_e,
    input  wire                          reset,
    input  wire                          push_now,
    input  wire [7:0]                    push_byte,
    input  wire [$clog2(N_SLOTS)-1:0]    push_slot,
    input  wire                          push_accum,
    input  wire [$clog2(DEPTH)-1:0]      tap_index,
    output wire                          edge_valid,
    output wire [7:0]                    edge_byte,
    output wire [$clog2(N_SLOTS)-1:0]    edge_slot,
    output wire                          edge_accum
);
    assign clk_e = clk_w;
    skew_lane #(.DEPTH(DEPTH), .N_SLOTS(N_SLOTS)) u (
        .clk        (clk_w),
        .reset      (reset),
        .push_now   (push_now),
        .push_byte  (push_byte),
        .push_slot  (push_slot),
        .push_accum (push_accum),
        .tap_index  (tap_index),
        .edge_valid (edge_valid),
        .edge_byte  (edge_byte),
        .edge_slot  (edge_slot),
        .edge_accum (edge_accum)
    );
endmodule

`default_nettype wire
