// smem.sv — on-chip scratchpad.
//
// SV implementation of pymodel.smem.SMEM. See pymodel/smem.py for the canonical
// spec; this module must match it cycle-by-cycle.
//
// Ports match the pymodel kwarg / attribute names exactly so the cocotb
// testbench can use common.tb_utils.step_and_compare with string-keyed access.
//
// PORTS
//   LOAD_WR (write port, BEAT_BYTES wide)
//     inputs : wr_en, wr_addr, wr_data
//
//   MMA_RD_A (read port, MMA_M bytes wide)
//     inputs : rd_a_en, rd_a_addr
//     outputs: rd_a_data, rd_a_valid (both registered, 1-cycle latency)
//
//   MMA_RD_B (read port, MMA_N bytes wide)
//     inputs : rd_b_en, rd_b_addr
//     outputs: rd_b_data, rd_b_valid (both registered, 1-cycle latency)
//
// BYTE PACKING CONVENTION
//   wr_data, rd_a_data, rd_b_data are packed byte vectors. Byte k lives in
//   bits [k*8 +: 8] (little-endian within the beat / read window). This
//   matches the gmem convention and `int.from_bytes(buf, "little")`.
//
// ORDERING (matches pymodel commit phase)
//   1. LOAD_WR commits to mem this cycle.
//   2. Drain previous-cycle pending MMA_RD_A into rd_a_data / rd_a_valid.
//      Per-byte write-forwarding handles the legal case where a read
//      captured at T-1 lands on a byte being written at T.
//   3. Drain previous-cycle pending MMA_RD_B similarly.
//   4. Capture new pending reads for next cycle.
//   Note: same-cycle wr_en + rd_*_en to OVERLAPPING addresses is illegal in
//   the pymodel (asserts), so the testbench filters it out. The forwarding
//   in steps 2/3 is for the legal write-then-drain (drain of a T-1 read at
//   the same address LOAD_WR is writing at T).
//
// RESET
//   Dominant. Clears pending state and registered outputs; mem contents
//   preserved (matches gmem.sv / tmem.sv semantics). pymodel has no reset;
//   this is RTL-side housekeeping so the testbench can start cleanly.

module smem #(
    parameter int SMEM_BYTES = 16384,
    parameter int BEAT_BYTES = 16,
    parameter int MMA_M      = 32,
    parameter int MMA_N      = 32
) (
    input  logic                          clk,
    input  logic                          reset,

    // LOAD_WR
    input  logic                          wr_en,
    input  logic [31:0]                   wr_addr,
    input  logic [BEAT_BYTES*8-1:0]       wr_data,

    // MMA_RD_A
    input  logic                          rd_a_en,
    input  logic [31:0]                   rd_a_addr,

    // MMA_RD_B
    input  logic                          rd_b_en,
    input  logic [31:0]                   rd_b_addr,

    // Outputs (registered)
    output logic [MMA_M*8-1:0]            rd_a_data,
    output logic                          rd_a_valid,
    output logic [MMA_N*8-1:0]            rd_b_data,
    output logic                          rd_b_valid
);

    // Storage: flat byte-addressable memory. Zero-initialized to match
    // pymodel.SMEM.__init__ (np.zeros).
    logic [7:0] mem [SMEM_BYTES];
    initial begin
        for (int unsigned k = 0; k < SMEM_BYTES; k++) begin
            mem[k] = 8'd0;
        end
    end

    // Pending read state (captured cycle T-1, drained cycle T).
    logic        rd_a_pending_valid;
    logic [31:0] rd_a_pending_addr;
    logic        rd_b_pending_valid;
    logic [31:0] rd_b_pending_addr;

    // Per-port combinational drain with per-byte write-forwarding. The pymodel
    // commits the write before the read drain, so a pending read whose
    // bytes overlap with a same-cycle LOAD_WR must observe the NEW data.
    // (NBAs alone would return the old value.)
    logic [MMA_M*8-1:0] rd_a_beat;
    always_comb begin
        logic [31:0] rb;
        logic        fwd;
        rd_a_beat = '0;
        for (int i = 0; i < MMA_M; i++) begin
            rb  = rd_a_pending_addr + i;
            fwd = wr_en && (rb >= wr_addr) && (rb < wr_addr + BEAT_BYTES);
            if (fwd) begin
                rd_a_beat[i*8 +: 8] = wr_data[(rb - wr_addr)*8 +: 8];
            end else begin
                rd_a_beat[i*8 +: 8] = mem[rb];
            end
        end
    end

    logic [MMA_N*8-1:0] rd_b_beat;
    always_comb begin
        logic [31:0] rb;
        logic        fwd;
        rd_b_beat = '0;
        for (int i = 0; i < MMA_N; i++) begin
            rb  = rd_b_pending_addr + i;
            fwd = wr_en && (rb >= wr_addr) && (rb < wr_addr + BEAT_BYTES);
            if (fwd) begin
                rd_b_beat[i*8 +: 8] = wr_data[(rb - wr_addr)*8 +: 8];
            end else begin
                rd_b_beat[i*8 +: 8] = mem[rb];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (reset) begin
            rd_a_pending_valid <= 1'b0;
            rd_a_pending_addr  <= 32'd0;
            rd_b_pending_valid <= 1'b0;
            rd_b_pending_addr  <= 32'd0;
            rd_a_data          <= '0;
            rd_a_valid         <= 1'b0;
            rd_b_data          <= '0;
            rd_b_valid         <= 1'b0;
        end else begin
            // 1. Commit write (independent of read paths; mem updates via NBA).
            if (wr_en) begin
                for (int i = 0; i < BEAT_BYTES; i++) begin
                    mem[wr_addr + i] <= wr_data[i*8 +: 8];
                end
            end

            // 2. Drain MMA_RD_A.
            if (rd_a_pending_valid) begin
                rd_a_data  <= rd_a_beat;
                rd_a_valid <= 1'b1;
            end else begin
                rd_a_data  <= '0;
                rd_a_valid <= 1'b0;
            end

            // 3. Drain MMA_RD_B.
            if (rd_b_pending_valid) begin
                rd_b_data  <= rd_b_beat;
                rd_b_valid <= 1'b1;
            end else begin
                rd_b_data  <= '0;
                rd_b_valid <= 1'b0;
            end

            // 4. Capture new pending reads for next cycle.
            if (rd_a_en) begin
                rd_a_pending_valid <= 1'b1;
                rd_a_pending_addr  <= rd_a_addr;
            end else begin
                rd_a_pending_valid <= 1'b0;
            end

            if (rd_b_en) begin
                rd_b_pending_valid <= 1'b1;
                rd_b_pending_addr  <= rd_b_addr;
            end else begin
                rd_b_pending_valid <= 1'b0;
            end
        end
    end

endmodule
