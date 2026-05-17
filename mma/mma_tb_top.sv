// mma_tb_top.sv — TB wrapper instantiating mma + smem + tmem + barrier.
//
// Exposes the issue interface, status outputs, and a back-door read path on
// TMEM (via the STORE_RD port) so the cocotb testbench can verify the final
// accumulator tile. SMEM contents are seeded via hierarchical back-door
// (dut.u_smem.mem) since the LOAD port lives elsewhere.
//
// Parameters are driven from config.py via -G flags in the Makefile.

module mma_tb_top #(
    parameter int MMA_M        = 32,
    parameter int MMA_N        = 32,
    parameter int MMA_K        = 32,
    parameter int TMEM_SLOTS   = 4,
    parameter int SMEM_BYTES   = 16384,
    parameter int BEAT_BYTES   = 16,
    parameter int NUM_BARRIERS = 8
) (
    input  logic                          clk,
    input  logic                          reset,

    // --- MMA issue ---
    input  logic                          start,
    input  logic [31:0]                   a_smem_offset,
    input  logic [31:0]                   b_smem_offset,
    input  logic [31:0]                   d_tmem_slot,
    input  logic                          accum,
    input  logic [31:0]                   bar_id,

    output logic                          busy,
    output logic                          done,
    output logic                          arrive_en_out,
    output logic [31:0]                   arrive_bar_id_out,

    // --- Barrier INIT port (TB drives) ---
    input  logic                          init_en,
    input  logic [31:0]                   init_bar_id,
    input  logic [15:0]                   init_count,

    // --- Barrier observable state ---
    output logic [NUM_BARRIERS*16-1:0]    bars_pending,
    output logic [NUM_BARRIERS*16-1:0]    bars_expected,
    output logic [NUM_BARRIERS*32-1:0]    bars_tx_pending,
    output logic [NUM_BARRIERS-1:0]       bars_phase,

    // --- SMEM LOAD_WR port (TB-side, for cycle-driven writes if needed;
    //                       primary tile seeding is via hierarchical mem[]) ---
    input  logic                          smem_wr_en,
    input  logic [31:0]                   smem_wr_addr,
    input  logic [BEAT_BYTES*8-1:0]       smem_wr_data,

    // --- TMEM STORE_RD port (TB drives to read back the result tile) ---
    input  logic                          tmem_store_rd_en,
    input  logic [31:0]                   tmem_store_rd_slot,
    output logic [MMA_M*MMA_N*32-1:0]     tmem_store_rd_tile,
    output logic                          tmem_store_rd_valid
);

    // ------------------------------------------------------------------
    // Internal wires (mma <-> smem/tmem/barrier).
    // ------------------------------------------------------------------
    // MMA -> SMEM
    logic                              mma_rd_a_en;
    logic [31:0]                       mma_rd_a_addr;
    logic                              mma_rd_b_en;
    logic [31:0]                       mma_rd_b_addr;
    // SMEM -> MMA
    logic [MMA_M*8-1:0]                mma_rd_a_data;
    logic                              mma_rd_a_valid;
    logic [MMA_N*8-1:0]                mma_rd_b_data;
    logic                              mma_rd_b_valid;

    // MMA -> TMEM MMA_PORT
    logic [1:0]                        mma_tmem_op;
    logic [31:0]                       mma_tmem_slot;
    logic [MMA_M*MMA_N*32-1:0]         mma_tmem_write_tile;
    // TMEM MMA_PORT -> MMA
    logic [MMA_M*MMA_N*32-1:0]         mma_tmem_rd_tile;
    logic                              mma_tmem_rd_valid;

    // MMA -> Barrier (arrive)
    logic                              mma_arrive_en;
    logic [31:0]                       mma_arrive_bar_id;

    // Pass arrives out for observability.
    assign arrive_en_out     = mma_arrive_en;
    assign arrive_bar_id_out = mma_arrive_bar_id;

    // SMEM stall signals (combinational, fed back into the consumers).
    logic                              smem_load_wr_stall_out;
    logic                              smem_rd_a_stall_out;
    logic                              smem_rd_b_stall_out;
    // load_wr_stall_out is wired to the TB LOAD_WR but unused (LOAD is
    // top priority and never stalls); silence verilator unused warning.
    /* verilator lint_off UNUSEDSIGNAL */
    logic smem_load_wr_stall_unused;
    assign smem_load_wr_stall_unused = smem_load_wr_stall_out;
    /* verilator lint_on UNUSEDSIGNAL */

    // ------------------------------------------------------------------
    // MMA engine.
    // ------------------------------------------------------------------
    mma #(
        .MMA_M(MMA_M),
        .MMA_N(MMA_N),
        .MMA_K(MMA_K)
    ) u_mma (
        .clk(clk),
        .reset(reset),
        .start(start),
        .a_smem_offset(a_smem_offset),
        .b_smem_offset(b_smem_offset),
        .d_tmem_slot(d_tmem_slot),
        .accum(accum),
        .bar_id(bar_id),
        .rd_a_data(mma_rd_a_data),
        .rd_a_valid(mma_rd_a_valid),
        .rd_b_data(mma_rd_b_data),
        .rd_b_valid(mma_rd_b_valid),
        .rd_a_stall_in(smem_rd_a_stall_out),
        .rd_b_stall_in(smem_rd_b_stall_out),
        .mma_rd_tile(mma_tmem_rd_tile),
        .mma_rd_valid(mma_tmem_rd_valid),
        .rd_a_en(mma_rd_a_en),
        .rd_a_addr(mma_rd_a_addr),
        .rd_b_en(mma_rd_b_en),
        .rd_b_addr(mma_rd_b_addr),
        .mma_op(mma_tmem_op),
        .mma_slot(mma_tmem_slot),
        .mma_write_tile(mma_tmem_write_tile),
        .arrive_en(mma_arrive_en),
        .arrive_bar_id(mma_arrive_bar_id),
        .busy(busy),
        .done(done)
    );

    // ------------------------------------------------------------------
    // SMEM. LOAD_WR is driven from the TB; MMA drives the two read ports.
    // ------------------------------------------------------------------
    smem #(
        .SMEM_BYTES(SMEM_BYTES),
        .BEAT_BYTES(BEAT_BYTES),
        .MMA_M(MMA_M),
        .MMA_N(MMA_N)
    ) u_smem (
        .clk(clk),
        .reset(reset),
        .wr_en(smem_wr_en),
        .wr_addr(smem_wr_addr),
        .wr_data(smem_wr_data),
        .rd_a_en(mma_rd_a_en),
        .rd_a_addr(mma_rd_a_addr),
        .rd_b_en(mma_rd_b_en),
        .rd_b_addr(mma_rd_b_addr),
        .rd_a_data(mma_rd_a_data),
        .rd_a_valid(mma_rd_a_valid),
        .rd_b_data(mma_rd_b_data),
        .rd_b_valid(mma_rd_b_valid),
        .load_wr_stall_out (smem_load_wr_stall_out),
        .mma_rd_a_stall_out(smem_rd_a_stall_out),
        .mma_rd_b_stall_out(smem_rd_b_stall_out)
    );

    // ------------------------------------------------------------------
    // TMEM. MMA drives MMA_PORT; the TB drives STORE_RD to verify results.
    // ------------------------------------------------------------------
    tmem #(
        .TMEM_SLOTS(TMEM_SLOTS),
        .MMA_M(MMA_M),
        .MMA_N(MMA_N)
    ) u_tmem (
        .clk(clk),
        .reset(reset),
        .mma_op(mma_tmem_op),
        .mma_slot(mma_tmem_slot),
        .mma_write_tile(mma_tmem_write_tile),
        .store_rd_en(tmem_store_rd_en),
        .store_rd_slot(tmem_store_rd_slot),
        .mma_rd_tile(mma_tmem_rd_tile),
        .mma_rd_valid(mma_tmem_rd_valid),
        .store_rd_tile(tmem_store_rd_tile),
        .store_rd_valid(tmem_store_rd_valid)
    );

    // ------------------------------------------------------------------
    // Barrier. INIT comes from the TB; arrive_a is wired to MMA. arrive_b
    // is unused. ADD_TX/SUB_TX/wait_query unused.
    // ------------------------------------------------------------------
    /* verilator lint_off PINMISSING */
    logic wait_done_unused;
    barrier #(
        .NUM_BARRIERS(NUM_BARRIERS)
    ) u_barrier (
        .clk(clk),
        .reset(reset),
        .init_en(init_en),
        .init_bar_id(init_bar_id),
        .init_count(init_count),
        .arrive_en_a(mma_arrive_en),
        .arrive_bar_id_a(mma_arrive_bar_id),
        .arrive_en_b(1'b0),
        .arrive_bar_id_b(32'd0),
        .add_tx_en(1'b0),
        .add_tx_bar_id(32'd0),
        .add_tx_bytes(32'd0),
        .sub_tx_en(1'b0),
        .sub_tx_bar_id(32'd0),
        .sub_tx_bytes(32'd0),
        .query_bar_id(32'd0),
        .query_expected_phase(1'b0),
        .wait_done(wait_done_unused),
        .bars_pending(bars_pending),
        .bars_expected(bars_expected),
        .bars_tx_pending(bars_tx_pending),
        .bars_phase(bars_phase)
    );
    /* verilator lint_on PINMISSING */

endmodule
