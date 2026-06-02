// skew_lane.sv -- live broadcast lane (purely combinational).
//
// History: this module used to hold a 31-stage shift register with a
// tap-select mux (tap_index = 0..DEPTH-1) intended for source-synchronous
// skew compensation. In B6 (PR #41) all parent callers were rewired to
// hard-tie tap_index=0 — making the shift register and tap mux dead
// silicon (~24K flops under the 32×32 array, see issue #44).
//
// This file now contains only the live (zero-delay) pass-through path:
// the carried 12-bit packet {valid, byte[7:0], slot[1:0], accum} is
// gated by push_now and driven straight onto the edge_* outputs.
// The internal flop stages, the tap_options[] array, tap_index port,
// clk, reset, and DEPTH parameter are all gone.
//
// If a future product wants the skew functionality back, restore from
// git history (commit before #44 closed) rather than reintroduce dead
// silicon now.

`default_nettype none

module skew_lane #(
    parameter int N_SLOTS = 4
) (
    input  wire                          push_now,
    input  wire [7:0]                    push_byte,
    input  wire [$clog2(N_SLOTS)-1:0]    push_slot,
    input  wire                          push_accum,
    output wire                          edge_valid,
    output wire [7:0]                    edge_byte,
    output wire [$clog2(N_SLOTS)-1:0]    edge_slot,
    output wire                          edge_accum
);

    assign edge_valid = push_now;
    assign edge_byte  = push_now ? push_byte : 8'd0;
    assign edge_slot  = push_now ? push_slot : '0;
    assign edge_accum = push_now ? push_accum : 1'b0;

endmodule

`default_nettype wire
