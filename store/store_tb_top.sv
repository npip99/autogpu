// store_tb_top.sv — TB wrapper instantiating store + tmem + gmem.
//
// Exposes ports the cocotb testbench needs:
//   - clk / reset
//   - store issue interface (issue_en, tmem_slot, gmem_ptr, dtype)
//   - store status (busy, done)
//   - tmem MMA_PORT (so the TB can seed a tile via WRITE)
//   - gmem read port (so the TB can verify written bytes via the normal port)
//
// Internal wiring:
//   - store.store_rd_{en,slot}  ↔ tmem.store_rd_{en,slot}
//   - store.{store_rd_tile, store_rd_valid} ← tmem.{store_rd_tile, store_rd_valid}
//   - store.{wr_en, wr_addr, wr_data}        → gmem.{wr_en, wr_addr, wr_data}
//
// Parameters match config.py (driven from the Makefile via -G flags).

module store_tb_top #(
    parameter int MMA_M      = 32,
    parameter int MMA_N      = 32,
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

    // --- TMEM MMA_PORT (so the TB can backdoor-seed a slot via WRITE) ---
    input  logic [1:0]                    mma_op,
    input  logic [31:0]                   mma_slot,
    input  logic [MMA_M*MMA_N*32-1:0]     mma_write_tile,

    // --- GMEM read port (TB-side; lets the TB verify written bytes) ---
    input  logic                          gmem_rd_en,
    input  logic [31:0]                   gmem_rd_addr,
    output logic [BEAT_BYTES*8-1:0]       gmem_rd_data,
    output logic                          gmem_rd_valid
);

    // ------------------------------------------------------------------
    // Internal wires.
    // ------------------------------------------------------------------
    // store → tmem.STORE_RD
    logic                            store_rd_en;
    logic [31:0]                     store_rd_slot;
    // tmem.STORE_RD → store
    logic [MMA_M*MMA_N*32-1:0]       store_rd_tile;
    logic                            store_rd_valid;

    // tmem MMA read outputs (unused by store; keep ports tied off).
    logic [MMA_M*MMA_N*32-1:0]       mma_rd_tile_w;
    logic                            mma_rd_valid_w;

    // store → gmem write
    logic                            store_wr_en;
    logic [31:0]                     store_wr_addr;
    logic [BEAT_BYTES*8-1:0]         store_wr_data;

    // ------------------------------------------------------------------
    // STORE engine.
    // ------------------------------------------------------------------
    store #(
        .MMA_M(MMA_M),
        .MMA_N(MMA_N),
        .BEAT_BYTES(BEAT_BYTES)
    ) u_store (
        .clk(clk),
        .reset(reset),
        .issue_en(issue_en),
        .tmem_slot(tmem_slot),
        .gmem_ptr(gmem_ptr),
        .dtype(dtype),
        .store_rd_tile(store_rd_tile),
        .store_rd_valid(store_rd_valid),
        .store_rd_en(store_rd_en),
        .store_rd_slot(store_rd_slot),
        .wr_en(store_wr_en),
        .wr_addr(store_wr_addr),
        .wr_data(store_wr_data),
        .busy(busy),
        .done(done)
    );

    // ------------------------------------------------------------------
    // TMEM.
    // ------------------------------------------------------------------
    tmem #(
        .TMEM_SLOTS(TMEM_SLOTS),
        .MMA_M(MMA_M),
        .MMA_N(MMA_N)
    ) u_tmem (
        .clk(clk),
        .reset(reset),
        .mma_op(mma_op),
        .mma_slot(mma_slot),
        .mma_write_tile(mma_write_tile),
        .store_rd_en(store_rd_en),
        .store_rd_slot(store_rd_slot),
        .mma_rd_tile(mma_rd_tile_w),
        .mma_rd_valid(mma_rd_valid_w),
        .store_rd_tile(store_rd_tile),
        .store_rd_valid(store_rd_valid)
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

endmodule
