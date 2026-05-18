// store_tb_top.sv — TB wrapper instantiating store + compute_array + gmem.
//
// Phase 7h-3: store now consumes compute_array's drain-stream interface
// (was: read a 32k-bit tile from tmem in one cycle). The TB wrapper
// instantiates a real compute_array so the testbench exercises both the
// drain mux and store's row-by-row gather + format + drain path.
//
// Tile seeding for the test happens via cocotb back-door into the
// per-cell storage (compute_array.gen_row[i].gen_col[j].u_cell.storage[slot]),
// matching the pattern in compute_array/tb/test_compute_array.py.
//
// Exposes ports the cocotb testbench needs:
//   - clk / reset
//   - store issue interface (issue_en, tmem_slot, gmem_ptr, dtype)
//   - store status (busy, done)
//   - gmem read port (so the TB can verify written bytes via the normal port)
//
// Parameters match config.py (driven from the Makefile via -G flags).

module store_tb_top #(
    parameter int MMA_M      = 32,
    parameter int MMA_N      = 32,
    parameter int MMA_K      = 32,
    parameter int TMEM_SLOTS = 4,
    parameter int BEAT_BYTES = 16,
    parameter int GMEM_BYTES = 16777216
) (
    input  logic                          clk,
    input  logic                          reset,

    // --- STORE issue ---
    input  logic                          issue_en,
    input  logic [31:0]                   tmem_slot,
    input  logic [31:0]                   gmem_ptr,
    input  logic                          dtype,

    output logic                          busy,
    output logic                          done,

    // --- GMEM read port (TB-side; lets the TB verify written bytes) ---
    input  logic                          gmem_rd_en,
    input  logic [31:0]                   gmem_rd_addr,
    output logic [BEAT_BYTES*8-1:0]       gmem_rd_data,
    output logic                          gmem_rd_valid
);

    // ------------------------------------------------------------------
    // Internal wires.
    // ------------------------------------------------------------------
    // store ↔ compute_array drain interface
    logic                                ca_drain_issue;
    logic [31:0]                         ca_drain_slot;
    logic                                ca_drain_busy;
    logic                                ca_drain_done;
    logic                                ca_drain_row_valid;
    logic [MMA_N*32-1:0]                 ca_drain_row_data;
    logic [$clog2(MMA_M)-1:0]            ca_drain_row_idx;
    logic                                ca_drain_last;

    // store → gmem write
    logic                                store_wr_en;
    logic [31:0]                         store_wr_addr;
    logic [BEAT_BYTES*8-1:0]             store_wr_data;

    // ------------------------------------------------------------------
    // STORE engine.
    // ------------------------------------------------------------------
    store #(
        .MMA_M(MMA_M),
        .MMA_N(MMA_N),
        .BEAT_BYTES(BEAT_BYTES)
    ) u_store (
        .clk             (clk),
        .reset           (reset),
        .issue_en        (issue_en),
        .tmem_slot       (tmem_slot),
        .gmem_ptr        (gmem_ptr),
        .dtype           (dtype),
        .drain_issue     (ca_drain_issue),
        .drain_slot      (ca_drain_slot),
        .drain_row_valid (ca_drain_row_valid),
        .drain_row_data  (ca_drain_row_data),
        .drain_row_idx   (ca_drain_row_idx),
        .drain_last      (ca_drain_last),
        .drain_done      (ca_drain_done),
        .wr_en           (store_wr_en),
        .wr_addr         (store_wr_addr),
        .wr_data         (store_wr_data),
        .busy            (busy),
        .done            (done)
    );

    // ------------------------------------------------------------------
    // compute_array. Matmul / SMEM ports are tied off — the TB only uses
    // its drain side. Cells are seeded via cocotb back-door before issue.
    // ------------------------------------------------------------------
    compute_array #(
        .MMA_M  (MMA_M),
        .MMA_N  (MMA_N),
        .MMA_K  (MMA_K),
        .N_SLOTS(TMEM_SLOTS)
    ) u_compute_array (
        .clk             (clk),
        .reset           (reset),
        .mma_issue       (1'b0),
        .mma_slot        ('0),
        .mma_accum       (1'b0),
        .mma_bar_id      (32'd0),
        .issue_a_off     (32'd0),
        .issue_b_off     (32'd0),
        .issue_a_stride  (32'd0),
        .issue_b_stride  (32'd0),
        .mma_busy        (),
        .mma_done        (),
        .arrive_en       (),
        .arrive_bar_id   (),
        .rd_a_en         (),
        .rd_a_addr       (),
        .rd_a_data       ('0),
        .rd_a_valid      (1'b0),
        .rd_a_stall_in   (1'b0),
        .rd_b_en         (),
        .rd_b_addr       (),
        .rd_b_data       ('0),
        .rd_b_valid      (1'b0),
        .rd_b_stall_in   (1'b0),
        .drain_issue     (ca_drain_issue),
        .drain_slot      (ca_drain_slot[$clog2(TMEM_SLOTS)-1:0]),
        .drain_busy      (ca_drain_busy),
        .drain_done      (ca_drain_done),
        .drain_row_valid (ca_drain_row_valid),
        .drain_row_data  (ca_drain_row_data),
        .drain_row_idx   (ca_drain_row_idx),
        .drain_last      (ca_drain_last),
        .scrub_en        (1'b0)
    );

    // ------------------------------------------------------------------
    // GMEM. TB drives the read port directly; STORE drives the write port.
    // ------------------------------------------------------------------
    gmem #(
        .GMEM_BYTES(GMEM_BYTES),
        .BEAT_BYTES(BEAT_BYTES)
    ) u_gmem (
        .clk(clk),
        .reset(reset),
        .rd_en(gmem_rd_en),
        .rd_addr(gmem_rd_addr),
        .wr_en(store_wr_en),
        .wr_addr(store_wr_addr),
        .wr_data(store_wr_data),
        .rd_data(gmem_rd_data),
        .rd_valid(gmem_rd_valid)
    );

    // Silence "unused" lint for drain_busy.
    /* verilator lint_off UNUSEDSIGNAL */
    wire _unused_ok = &{1'b0, ca_drain_busy};
    /* verilator lint_on UNUSEDSIGNAL */

endmodule
