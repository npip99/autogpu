// dense_grid: 32x32 mac_tmem_cell macros, tightly packed.
// Border macros connect to chip IO; inner macros chain to neighbors.
// Purpose: A/B vs compute_array — same 1024 macros, less empty die area.
module dense_grid #(parameter int N = 32) (
    input  logic                clk,
    input  logic                reset,
    input  logic                scrub_en,
    // West border: drain_in feeds row 0..N-1, col 0
    input  logic [N*32-1:0]     west_in,
    // East border: drain_out from row 0..N-1, col N-1
    output logic [N*32-1:0]     east_out
);
    logic [31:0] drain_pipe [N-1:0][N-1:0];

    genvar r, c;
    generate
        for (r = 0; r < N; r++) begin : gen_row
            for (c = 0; c < N; c++) begin : gen_col
                logic [31:0] din;
                assign din = (c == 0) ? west_in[r*32 +: 32] : drain_pipe[r][c-1];
                mac_tmem_cell u_cell (
                    .clk_w        (clk),
                    .clk_e        (),
                    .reset_w      (reset),
                    .reset_e      (),
                    .compute_in   (1'b0),
                    .a_in         (8'd0),
                    .b_in         (8'd0),
                    .slot_in      (2'd0),
                    .accum_in     (1'b0),
                    .compute_out  (),
                    .a_out        (),
                    .b_out        (),
                    .slot_out     (),
                    .accum_out    (),
                    .drain_in     (din),
                    .drain_out    (drain_pipe[r][c]),
                    .drain_en_w   (1'b0),
                    .drain_en_e   (),
                    .drain_slot_w (2'd0),
                    .drain_slot_e (),
                    // init_* ports removed from mac_tmem_cell (#40, INVARIANTS R4a)
                    .scrub_en_w   (scrub_en),
                    .scrub_en_e   ()
                );
            end
        end
    endgenerate

    generate
        for (r = 0; r < N; r++) begin : gen_eout
            assign east_out[r*32 +: 32] = drain_pipe[r][N-1];
        end
    endgenerate
endmodule
