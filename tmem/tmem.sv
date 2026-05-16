// tmem.sv — accumulator tile scratchpad.
//
// SV implementation of pymodel.tmem.TMEM. See pymodel/tmem.py for the
// canonical spec; this module must match it cycle-by-cycle.
//
// Ports match the pymodel kwarg / attribute names exactly so the cocotb
// testbench can use common.tb_utils.step_and_compare with string-keyed access.
//
// PORTS
//   MMA_PORT (one cycle: NONE / READ / WRITE — mutually exclusive)
//     inputs : mma_op[1:0], mma_slot, mma_write_tile
//     outputs: mma_rd_tile, mma_rd_valid (both registered, 1-cycle latency)
//
//   STORE_RD (read-only)
//     inputs : store_rd_en, store_rd_slot
//     outputs: store_rd_tile, store_rd_valid (both registered, 1-cycle latency)
//
// TILE PACKING CONVENTION
//   A tile is an MMA_M x MMA_N fp32 array. The packed wire `mma_write_tile`
//   (and `mma_rd_tile`, `store_rd_tile`) is a single [MMA_M*MMA_N*32 - 1 : 0]
//   vector. Element [i][j] (row i, col j) occupies bits
//        [(((i)*MMA_N + (j)) * 32) +: 32]
//   i.e. row-major, with element [0][0] in the low 32 bits and within each
//   32-bit word the IEEE 754 fp32 bit pattern is stored verbatim (LSB at the
//   low bit). This matches Python `tile.astype('<f4').tobytes()` followed by
//   `int.from_bytes(buf, "little")`.
//
// READ LATENCY
//   Exactly 1 cycle per port. Reads issued in cycle T present rd_tile/rd_valid
//   at cycle T+1.
//
// ORDERING (matches pymodel commit phase)
//   1. MMA WRITE commits to slots this cycle.
//   2. Drain previous-cycle pending MMA read into mma_rd_tile / mma_rd_valid.
//   3. Drain previous-cycle pending STORE read into store_rd_tile / store_rd_valid.
//   4. Capture new pending reads for next cycle.
//   Since MMA op is exclusive (READ xor WRITE) and same-cycle MMA WRITE +
//   STORE_RD on the *same* slot is undefined (spec assertion), no write-
//   forwarding is needed on the drain path: any read of a freshly-written
//   slot necessarily occurs on a later cycle.
//
// RESET
//   Dominant. Clears pending state and registered outputs; slot contents are
//   preserved (matches gmem reset semantics, and pymodel never clears slots).

module tmem #(
    parameter int TMEM_SLOTS = 4,
    parameter int MMA_M      = 32,
    parameter int MMA_N      = 32
) (
    input  logic                                 clk,
    input  logic                                 reset,

    // MMA_PORT
    input  logic [1:0]                           mma_op,   // 0=NONE, 1=READ, 2=WRITE
    input  logic [31:0]                          mma_slot,
    input  logic [MMA_M*MMA_N*32-1:0]            mma_write_tile,

    // STORE_RD
    input  logic                                 store_rd_en,
    input  logic [31:0]                          store_rd_slot,

    // Outputs (registered)
    output logic [MMA_M*MMA_N*32-1:0]            mma_rd_tile,
    output logic                                 mma_rd_valid,
    output logic [MMA_M*MMA_N*32-1:0]            store_rd_tile,
    output logic                                 store_rd_valid
);

    // Op encoding (must match pymodel.tmem.MMAOp).
    localparam logic [1:0] OP_NONE  = 2'd0;
    localparam logic [1:0] OP_READ  = 2'd1;
    localparam logic [1:0] OP_WRITE = 2'd2;

    // Slot storage: TMEM_SLOTS x MMA_M x MMA_N words of fp32 (32-bit each).
    // Zero-initialized to match pymodel.TMEM.__init__ (np.zeros).
    logic [31:0] slots [TMEM_SLOTS][MMA_M][MMA_N];
    initial begin
        for (int s = 0; s < TMEM_SLOTS; s++) begin
            for (int i = 0; i < MMA_M; i++) begin
                for (int j = 0; j < MMA_N; j++) begin
                    slots[s][i][j] = 32'd0;
                end
            end
        end
    end

    // Pending read state (captured cycle T-1, drained cycle T).
    logic                rd_mma_pending_valid;
    logic [31:0]         rd_mma_pending_slot;
    logic                rd_store_pending_valid;
    logic [31:0]         rd_store_pending_slot;

    // Zero constant sized to the packed-tile width. For typical MMA_M/MMA_N
    // (e.g. 32x32 -> 32768 bits) this replication exceeds Verilator's default
    // --replication-limit, so we silence the WIDTHCONCAT warning on this one
    // localparam (and the assignments using '0 below).
    /* verilator lint_off WIDTHCONCAT */
    localparam logic [MMA_M*MMA_N*32-1:0] TILE_ZERO = {(MMA_M*MMA_N*32){1'b0}};
    /* verilator lint_on WIDTHCONCAT */

    // Combinational read of slots[slot] -> packed tile (row-major, [0][0] in
    // low bits), with same-cycle MMA WRITE forwarding. The pymodel commits
    // the WRITE before draining reads, so when a drained read targets the
    // slot being written this cycle, it must observe the new value. (NBAs
    // alone would return the old value.) This forwarding is necessary even
    // though same-cycle MMA WRITE + STORE_RD *capture* on the same slot is
    // illegal — a drain happens one cycle after capture, and a freshly
    // captured store_rd_pending on slot X followed by an MMA WRITE to X on
    // the very next cycle is legal and must read the new value.
    function automatic logic [MMA_M*MMA_N*32-1:0] pack_slot(input logic [31:0] s);
        logic [MMA_M*MMA_N*32-1:0] out;
        logic                       fwd;
        out = TILE_ZERO;
        fwd = (mma_op == OP_WRITE) && (mma_slot == s);
        for (int i = 0; i < MMA_M; i++) begin
            for (int j = 0; j < MMA_N; j++) begin
                if (fwd) begin
                    out[((i*MMA_N) + j)*32 +: 32]
                        = mma_write_tile[((i*MMA_N) + j)*32 +: 32];
                end else begin
                    out[((i*MMA_N) + j)*32 +: 32] = slots[s][i][j];
                end
            end
        end
        return out;
    endfunction

    always_ff @(posedge clk) begin
        if (reset) begin
            rd_mma_pending_valid   <= 1'b0;
            rd_mma_pending_slot    <= 32'd0;
            rd_store_pending_valid <= 1'b0;
            rd_store_pending_slot  <= 32'd0;
            mma_rd_tile            <= TILE_ZERO;
            mma_rd_valid           <= 1'b0;
            store_rd_tile          <= TILE_ZERO;
            store_rd_valid         <= 1'b0;
        end else begin
            // 1. Commit MMA WRITE this cycle.
            if (mma_op == OP_WRITE) begin
                for (int i = 0; i < MMA_M; i++) begin
                    for (int j = 0; j < MMA_N; j++) begin
                        slots[mma_slot][i][j]
                            <= mma_write_tile[((i*MMA_N) + j)*32 +: 32];
                    end
                end
            end

            // 2. Drain previous-cycle MMA pending read.
            if (rd_mma_pending_valid) begin
                mma_rd_tile  <= pack_slot(rd_mma_pending_slot);
                mma_rd_valid <= 1'b1;
            end else begin
                mma_rd_tile  <= TILE_ZERO;
                mma_rd_valid <= 1'b0;
            end

            // 3. Drain previous-cycle STORE pending read.
            if (rd_store_pending_valid) begin
                store_rd_tile  <= pack_slot(rd_store_pending_slot);
                store_rd_valid <= 1'b1;
            end else begin
                store_rd_tile  <= TILE_ZERO;
                store_rd_valid <= 1'b0;
            end

            // 4. Capture new pending reads for next cycle.
            if (mma_op == OP_READ) begin
                rd_mma_pending_valid <= 1'b1;
                rd_mma_pending_slot  <= mma_slot;
            end else begin
                rd_mma_pending_valid <= 1'b0;
            end

            if (store_rd_en) begin
                rd_store_pending_valid <= 1'b1;
                rd_store_pending_slot  <= store_rd_slot;
            end else begin
                rd_store_pending_valid <= 1'b0;
            end
        end
    end

endmodule
