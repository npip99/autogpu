// reset_seq.sv — power-on reset sequencer + on-chip memory scrubber.
//
// Replaces the simulation-only `initial begin ... end` zero-init blocks in
// smem / tmem with a real reset sequence:
//   1. While reset_in is high, hold chip_in_reset=1, do nothing else.
//   2. After reset_in deasserts, walk smem_scrub_addr through every
//      per-bank word index (0..NUM_WORDS_PER_BANK-1), driving
//      smem_scrub_en=1 each cycle. SMEM writes all 16 banks in parallel
//      at the addressed per-bank word. Simultaneously pulse tmem_scrub_en
//      for exactly the first scrub cycle (TMEM is a flop array — a single
//      parallel clear is enough).
//   3. Once the final scrub addr has been committed, transition to S_RUN
//      with chip_in_reset=0 and scrub_done=1. The pipeline runs.
//
// GMEM is OFF-CHIP and is NOT scrubbed here.
//
// Cycle-by-cycle behavior is the canonical reference: pymodel/reset_seq.py.
// This module is verified against that pymodel via reset_seq/tb/test_reset_seq.py.

module reset_seq #(
    parameter int SCRUB_DEPTH = 128  // = SMEM_BYTES / NUM_BANKS / 4
) (
    input  logic                            clk,
    input  logic                            reset_in,

    output logic                            chip_in_reset,
    output logic                            smem_scrub_en,
    output logic [$clog2(SCRUB_DEPTH)-1:0]  smem_scrub_addr,
    output logic                            tmem_scrub_en,
    output logic                            scrub_done
);

    typedef enum logic [1:0] {
        S_RESET = 2'd0,
        S_SCRUB = 2'd1,
        S_RUN   = 2'd2
    } phase_e;

    phase_e                              phase;
    logic [$clog2(SCRUB_DEPTH)-1:0]      scrub_addr_q;

    always_ff @(posedge clk) begin
        if (reset_in) begin
            phase            <= S_RESET;
            scrub_addr_q     <= '0;
            chip_in_reset    <= 1'b1;
            smem_scrub_en    <= 1'b0;
            smem_scrub_addr  <= '0;
            tmem_scrub_en    <= 1'b0;
            scrub_done       <= 1'b0;
        end else begin
            unique case (phase)
                S_RESET: begin
                    // First cycle after reset_in deasserts: drive scrub
                    // addr=0 and pulse tmem clear.
                    phase           <= S_SCRUB;
                    scrub_addr_q    <= '0;
                    chip_in_reset   <= 1'b1;
                    smem_scrub_en   <= 1'b1;
                    smem_scrub_addr <= '0;
                    tmem_scrub_en   <= 1'b1;
                    scrub_done      <= 1'b0;
                end
                S_SCRUB: begin
                    if (scrub_addr_q == ($clog2(SCRUB_DEPTH))'(SCRUB_DEPTH - 1)) begin
                        // Final addr committed last cycle; release.
                        phase           <= S_RUN;
                        scrub_addr_q    <= '0;
                        chip_in_reset   <= 1'b0;
                        smem_scrub_en   <= 1'b0;
                        smem_scrub_addr <= '0;
                        tmem_scrub_en   <= 1'b0;
                        scrub_done      <= 1'b1;
                    end else begin
                        scrub_addr_q    <= scrub_addr_q + 1'b1;
                        chip_in_reset   <= 1'b1;
                        smem_scrub_en   <= 1'b1;
                        smem_scrub_addr <= scrub_addr_q + 1'b1;
                        tmem_scrub_en   <= 1'b0;
                        scrub_done      <= 1'b0;
                    end
                end
                S_RUN: begin
                    chip_in_reset   <= 1'b0;
                    smem_scrub_en   <= 1'b0;
                    smem_scrub_addr <= '0;
                    tmem_scrub_en   <= 1'b0;
                    scrub_done      <= 1'b1;
                end
                default: begin
                    // unreachable
                    phase           <= S_RESET;
                    chip_in_reset   <= 1'b1;
                    smem_scrub_en   <= 1'b0;
                    smem_scrub_addr <= '0;
                    tmem_scrub_en   <= 1'b0;
                    scrub_done      <= 1'b0;
                end
            endcase
        end
    end

endmodule
