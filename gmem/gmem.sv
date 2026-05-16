// gmem.sv — external DRAM model.
//
// SV implementation of pymodel.gmem.GMEM. See pymodel/gmem.py for the canonical
// spec; this module must match it cycle-by-cycle.
//
// Ports match the pymodel kwarg / attribute names exactly so the cocotb
// testbench can use common.tb_utils.step_and_compare with string-keyed access.
//
// Read latency: exactly 1 cycle. rd_data and rd_valid are registered outputs.
// Reset is dominant — clears pending read and outputs; mem contents preserved.
//
// Notes on pymodel ordering (which this RTL matches):
//   1. write commits to mem this cycle
//   2. drain previous-cycle pending read into rd_data (reads NEW mem)
//   3. capture new pending read addr for next cycle
// As a result, when a drained rd_pending address overlaps with a same-cycle
// wr_addr, the drain returns the new (just-written) data. We implement this
// with per-byte write-forwarding on the drain path. Note this scenario is
// legal — the spec only forbids overlap of *current-cycle* rd_en and wr_en.

module gmem #(
    parameter int GMEM_BYTES = 16777216,
    parameter int BEAT_BYTES = 16
) (
    input  logic                       clk,
    input  logic                       reset,

    input  logic                       rd_en,
    input  logic [31:0]                rd_addr,

    input  logic                       wr_en,
    input  logic [31:0]                wr_addr,
    input  logic [BEAT_BYTES*8-1:0]    wr_data,

    output logic [BEAT_BYTES*8-1:0]    rd_data,
    output logic                       rd_valid
);

    // Storage: byte-addressable memory backing. Verilator allocates a flat
    // unpacked array of bytes, indexed by byte address.
    // Zero-initialized to match pymodel.GMEM.__init__ (np.zeros).
    logic [7:0] mem [GMEM_BYTES];
    initial begin
        for (int unsigned k = 0; k < GMEM_BYTES; k++) begin
            mem[k] = 8'd0;
        end
    end

    // Pending read captured last cycle (drained this cycle).
    logic                       rd_pending_valid;
    logic [31:0]                rd_pending_addr;

    // Beat-sized read of mem at the pending address, with per-byte forwarding
    // of an in-flight same-cycle write. This matches pymodel order: write
    // commits to mem before the drain reads it.
    logic [BEAT_BYTES*8-1:0]    rd_beat;
    always_comb begin
        logic [31:0] rb;
        logic        fwd;
        rd_beat = '0;
        for (int i = 0; i < BEAT_BYTES; i++) begin
            rb  = rd_pending_addr + i;
            fwd = wr_en && (rb >= wr_addr) && (rb < wr_addr + BEAT_BYTES);
            if (fwd) begin
                rd_beat[i*8 +: 8] = wr_data[(rb - wr_addr)*8 +: 8];
            end else begin
                rd_beat[i*8 +: 8] = mem[rb];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (reset) begin
            // Reset is dominant: clear pending state and outputs; mem preserved.
            rd_pending_valid <= 1'b0;
            rd_pending_addr  <= 32'd0;
            rd_data          <= '0;
            rd_valid         <= 1'b0;
        end else begin
            // 1. Commit write (independent of read path; mem updates via NBA).
            if (wr_en) begin
                for (int i = 0; i < BEAT_BYTES; i++) begin
                    mem[wr_addr + i] <= wr_data[i*8 +: 8];
                end
            end

            // 2. Drain previous pending read into registered outputs.
            //    rd_beat already reflects same-cycle write forwarding.
            if (rd_pending_valid) begin
                rd_data  <= rd_beat;
                rd_valid <= 1'b1;
            end else begin
                rd_data  <= '0;
                rd_valid <= 1'b0;
            end

            // 3. Capture new pending read for next cycle.
            if (rd_en) begin
                rd_pending_valid <= 1'b1;
                rd_pending_addr  <= rd_addr;
            end else begin
                rd_pending_valid <= 1'b0;
            end
        end
    end

endmodule
