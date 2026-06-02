// skew_lane_b.sv -- b-skew variant of skew_lane (B6: pure abutment broadcast chain).
//
// B6 architecture (#40):
//   Symmetric to skew_lane_a — same per-instance chain register, but the
//   chain flows W→E via abutment between horizontally-adjacent skew_b
//   instances (which sit on compute_array's south row).
//
//   Per stage:
//     - chain_w_w (west edge input, registered to chain_e_e at posedge clk_w)
//     - chain_e_e (east edge output = registered chain_w_w)
//     - this column consumes its byte from chain_w_w[col*8 +: 8]
//
//   Pin layout (geometry — see tech/asap7/orfs/scripts/skew_lane_b.pins.tcl):
//     - W edge: clk_w + chain_w_w + per-col taps (push_byte/now/slot/accum
//       /tap_index/reset) — parent slices the chain externally
//     - E edge: clk_e + chain_e_e (registered)
//     - N edge: edge_byte/valid/slot/accum — feeds mac mesh row 0 above
//     - S edge: empty (skew_b sits at compute_array's south boundary)

`default_nettype none

module skew_lane_b #(
    parameter int DEPTH       = 32,
    parameter int N_SLOTS     = 4,
    parameter int CHAIN_WIDTH = 260   // push_now(1) + push_slot(2) + push_accum(1) + push_b_bytes(256)
) (
    input  wire                          clk_w,
    output wire                          clk_e,
    input  wire                          reset,

    input  wire [CHAIN_WIDTH-1:0]        chain_w_w,
    output wire [CHAIN_WIDTH-1:0]        chain_e_e,

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

    reg [CHAIN_WIDTH-1:0] chain_reg;
    always @(posedge clk_w) begin
        if (reset) chain_reg <= '0;
        else       chain_reg <= chain_w_w;
    end
    assign chain_e_e = chain_reg;

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
