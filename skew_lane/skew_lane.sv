// skew_lane.sv -- one lane of a depth-DEPTH shift register with tap-select.
//
// Phase 7i-7: hardenable per-lane skew unit. Replaces the giant in-cmd_unit
// skew buffer (32 lanes × 31 stages × 12 bits ≈ 12k FFs) with 32 identical
// macros, each holding ~372 FFs. Routed locally next to each row/col edge
// of the compute_array.
//
// The carried packet is {valid, byte[7:0], slot[1:0], accum} — 12 bits.
// a-side skew_lanes carry the full packet; b-side instances tie slot and
// accum to 0 and ignore those edge outputs (yosys constant-propagates the
// unused tap regs away when this is instantiated, but the HARDENED macro
// is uniform — same physical layout for both a-side and b-side use).
//
// Tap select:
//   tap_index = 0    -> edge_* = live push payload (zero delay)
//   tap_index = k>=1 -> edge_* = stage[k-1] (k-cycle delay)
//
// Per instance, tap_index is hard-wired to a constant at the top level
// (=row index for a-side, =col index for b-side).

`default_nettype none

module skew_lane #(
    parameter int DEPTH   = 32,   // max delay supported (instance picks via tap_index)
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

    localparam int SLOT_W      = $clog2(N_SLOTS);
    localparam int A_SKEW_DEPTH = (DEPTH > 1) ? (DEPTH - 1) : 1;
    localparam int PKT_W        = 1 + 8 + SLOT_W + 1;

    // Live (zero-delay) packet at tap_index = 0.
    wire             live_valid = push_now;
    wire [7:0]       live_byte  = push_now ? push_byte : 8'd0;
    wire [SLOT_W-1:0] live_slot = push_now ? push_slot : '0;
    wire             live_accum = push_now ? push_accum : 1'b0;

    // Shift register stages [0..A_SKEW_DEPTH-1]: stage 0 = newest (1 cycle delay).
    reg               skew_valid [A_SKEW_DEPTH-1:0];
    reg [7:0]         skew_byte  [A_SKEW_DEPTH-1:0];
    reg [SLOT_W-1:0]  skew_slot  [A_SKEW_DEPTH-1:0];
    reg               skew_accum [A_SKEW_DEPTH-1:0];

    integer i_s;
    always @(posedge clk) begin
        if (reset) begin
            for (i_s = 0; i_s < A_SKEW_DEPTH; i_s = i_s + 1) begin
                skew_valid[i_s] <= 1'b0;
                skew_byte [i_s] <= 8'd0;
                skew_slot [i_s] <= '0;
                skew_accum[i_s] <= 1'b0;
            end
        end else begin
            for (i_s = A_SKEW_DEPTH-1; i_s > 0; i_s = i_s - 1) begin
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

    // Build the tap option array: 0 = live, k>=1 = stage[k-1].
    wire [PKT_W-1:0] tap_options [DEPTH-1:0];
    assign tap_options[0] = {live_valid, live_byte, live_slot, live_accum};

    genvar gk;
    generate
        for (gk = 1; gk < DEPTH; gk = gk + 1) begin : gen_taps
            assign tap_options[gk] = {
                skew_valid[gk-1],
                skew_byte [gk-1],
                skew_slot [gk-1],
                skew_accum[gk-1]
            };
        end
    endgenerate

    wire [PKT_W-1:0] tap_packet = tap_options[tap_index];

    assign edge_valid = tap_packet[PKT_W-1];
    assign edge_byte  = tap_packet[PKT_W-2 -: 8];
    assign edge_slot  = tap_packet[SLOT_W -: SLOT_W];
    assign edge_accum = tap_packet[0];

endmodule

`default_nettype wire
