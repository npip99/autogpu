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
    input  wire                          clk,
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
    skew_lane #(.DEPTH(DEPTH), .N_SLOTS(N_SLOTS)) u (.*);
endmodule

`default_nettype wire
