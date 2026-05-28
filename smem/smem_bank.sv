// smem_bank.sv — one SRAM bank with built-in per-dword output gating.
//
// Wraps one `sram_1rw` (which on asap7 becomes `fakeram7_256x32`) plus
// the local logic that decides which output dword (0..RDA_DWORDS-1 or
// 0..RDB_DWORDS-1) this bank is contributing to in the current cycle.
//
// EXISTS so the smem-level read-beat wires stay LOCAL — each bank emits
// one already-gated 32-bit contribution per read port, and smem.sv reads
// each bank's gated dword directly (no central mux, no OR-tree: under the
// B1 region partition each bank feeds exactly one dword of one port). The
// 512 bank_rdata wires that used to fan out to a central mux are gone;
// the gating happens inside the hardened bank macro next to where
// bank_rdata is produced.
//
// HARDENED INTO ITS OWN LEF — see tech/asap7/orfs/smem_bank.config.mk.
// 16 instances of this macro replace 16 sram_1rw + central mux in
// smem.sv.
//
// PORT CONTRACT
//
//   Standard 1RW port (driven by smem.sv arbitration):
//     en, we, addr, wdata, scrub_en, scrub_addr — bank reads/writes
//
//   Per-read-port "am I selected and for which output dword?":
//     rd_a_active     : this bank's rdata feeds an rd_a output dword
//     rd_a_dword_idx  : which of RDA_DWORDS output positions (0..7)
//     rd_b_active     : same for rd_b
//     rd_b_dword_idx  : same for rd_b
//
//   Outputs:
//     rd_a_out  : bank_rdata if rd_a_active else 0
//     rd_b_out  : bank_rdata if rd_b_active else 0
//
//   Under the region partition each bank feeds exactly one dword of one
//   read port, so smem.sv reads the bank's gated output directly (the
//   8-wide rd_*_out vector keeps the bank generic; only the matching
//   index is consumed). No OR network.
//
// PARAMETERS
//   WORDS : depth of the SRAM bank (smem.SMEM_BYTES / NUM_BANKS / 4).
//   For chip_top defaults (8 KB total, 16 banks, 4 B/dword): 128 words.

`ifndef SMEM_BANK_WORDS
`define SMEM_BANK_WORDS 128
`endif

(* keep_hierarchy = "yes" *)
module smem_bank #(
    parameter int WORDS = `SMEM_BANK_WORDS
) (
    input  logic                       clk,

    // Standard 1RW port (smem.sv arbitration drives these).
    input  logic                       en,
    input  logic                       we,
    input  logic [$clog2(WORDS)-1:0]   addr,
    input  logic [31:0]                wdata,

    // Per-read-port gating (smem.sv computes these per bank per cycle).
    input  logic                       rd_a_active,
    input  logic [2:0]                 rd_a_dword_idx,
    input  logic                       rd_b_active,
    input  logic [2:0]                 rd_b_dword_idx,

    // 8 outputs per read port — exactly one is non-zero per cycle when
    // active; the other 7 are zero. smem.sv reads this bank's gated dword
    // directly into the read beat (no OR-tree across banks).
    output logic [31:0]                rd_a_out [8],
    output logic [31:0]                rd_b_out [8]
);

    // Underlying SRAM.
    logic [31:0] bank_rdata;
    sram_1rw #(
        .WORDS(WORDS),
        .W    (32)
    ) u_sram (
        .clk   (clk),
        .en    (en),
        .we    (we),
        .addr  (addr),
        .wdata (wdata),
        .rdata (bank_rdata)
    );

    // Per-output gating. Since rd_a_dword_idx is a 3-bit one-hot-select
    // (only one dword position is mine per cycle), the per-output
    // expression is bank_rdata AND (active AND idx == d). The other 7
    // outputs are 0.
    //
    // (* keep_hierarchy *) on the bank prevents yosys from flattening
    // these 8 gated outputs into a single shared-mux structure that
    // would defeat the locality intent.
    int i;
    always_comb begin
        for (i = 0; i < 8; i++) begin
            rd_a_out[i] = (rd_a_active && (rd_a_dword_idx == 3'(i))) ? bank_rdata : 32'd0;
            rd_b_out[i] = (rd_b_active && (rd_b_dword_idx == 3'(i))) ? bank_rdata : 32'd0;
        end
    end

endmodule
