// row_unit_smoke.sv -- standalone elaboration wrapper for row_unit.
//
// Pure pass-through of row_unit's primary IO so sv2v can elaborate
// row_unit by itself (no compute_array dependency). tap_index is tied
// to a constant; the per-row instantiation in compute_array will tie
// it to its row index.

module row_unit_smoke (
    input  logic                 clk,
    input  logic                 reset,

    input  logic                 push_now,
    input  logic [7:0]           push_a_byte,
    input  logic [1:0]           push_slot,
    input  logic                 push_accum,

    input  logic                 drain_row_select,
    input  logic [1:0]           drain_slot_to_cells,

    input  logic [32*32-1:0]     drain_data_in,
    input  logic [32*32-1:0]     drain_chain_in,

    output logic                 edge_compute,
    output logic [7:0]           edge_a_byte,
    output logic [1:0]           edge_slot,
    output logic                 edge_accum,

    output logic                 cell_drain_en,
    output logic [1:0]           cell_drain_slot,

    output logic [32*32-1:0]     drain_chain_out
);

    row_unit #(
        .MMA_M  (32),
        .MMA_N  (32),
        .N_SLOTS(4)
    ) u_inst (
        .clk                 (clk),
        .reset               (reset),
        .push_now            (push_now),
        .push_a_byte         (push_a_byte),
        .push_slot           (push_slot),
        .push_accum          (push_accum),
        .tap_index           (5'd3),
        .drain_row_select    (drain_row_select),
        .drain_slot_to_cells (drain_slot_to_cells),
        .drain_data_in       (drain_data_in),
        .drain_chain_in      (drain_chain_in),
        .edge_compute        (edge_compute),
        .edge_a_byte         (edge_a_byte),
        .edge_slot           (edge_slot),
        .edge_accum          (edge_accum),
        .cell_drain_en       (cell_drain_en),
        .cell_drain_slot     (cell_drain_slot),
        .drain_chain_out     (drain_chain_out)
    );

endmodule
