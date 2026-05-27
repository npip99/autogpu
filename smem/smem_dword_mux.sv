// smem_dword_mux.sv — per-output-dword mux for smem.
//
// Owns the assembly of one 4-byte (32-bit) output dword from:
//   - 32 banks' rdata (each 32 bits wide)
//   - Per-byte write-forwarding from current-cycle wr_data
//
// EXISTS for a physical reason, not an architectural one: smem.sv used
// to do all 32 output bytes (rd_a_beat) in one `always_comb` for-loop,
// which yosys flattened into a single centralized mux block. The
// resulting 1024 bank_rdata wires all converged on one die region and
// blew asap7 inter-bank routing channels.
//
// This module is instantiated 8 times per read port (8 dwords × 4 bytes
// = 32 byte outputs, matching MMA_M=32). With `keep_hierarchy=yes` plus
// `place_inst` directives in smem.macro_placement.tcl, each instance is
// placed at a distinct y-coordinate along the consumer edge. The wires
// from banks still exist (same fan-out count) but their destinations
// are spread across 8 physical locations instead of converging on one.
//
// Same structural shape as a per-lane mux in a CUDA SMEM crossbar. When
// SIMT support is added, additional dword_mux instances can be wired
// up for the warp-lane interface without further restructure.
//
// ASSUMPTIONS:
//   - bank_rdata is the full NUM_BANKS-element array, all instances
//     receive the same view. Fan-out is intentional — distribution comes
//     from placement, not from input pruning.
//   - base_addr is the byte address of byte 0 of this dword.
//     base_addr+0..base_addr+3 are the 4 byte positions handled.
//   - fwd_mask is the 4-bit slice of the parent's MMA_M-wide forwarding
//     mask covering these 4 byte positions.
//   - wr_data is the full BEAT_BYTES-wide write data; byte_idx_in_wr
//     gives the per-byte index into wr_data when forwarding (precomputed
//     at the parent to keep the dword-mux purely combinational).

(* keep_hierarchy = "yes" *)
module smem_dword_mux #(
    parameter int NUM_BANKS  = 32,
    parameter int BANK_BITS  = 5,
    parameter int BEAT_BYTES = 16
) (
    input  logic [31:0]              bank_rdata    [NUM_BANKS],
    input  logic [31:0]              base_addr,
    input  logic [3:0]               fwd_mask,
    // Per-byte index into wr_data for forwarded bytes. Only valid where
    // fwd_mask[b]=1; otherwise ignored. Precomputed by the parent so
    // every dword_mux instance gets the same wr_data input.
    input  logic [3:0]               byte_idx_in_wr [4],
    input  logic [BEAT_BYTES*8-1:0]  wr_data,
    output logic [31:0]              dword_out
);

    // Per-byte assembly. Each iteration picks one of:
    //   - wr_data byte at byte_idx_in_wr[b] (write-forwarding case)
    //   - bank_rdata[bank][byte_within_bank] (normal read case)
    //
    // bank/byte selection comes from (base_addr + b). Each byte's mux is
    // 128:1 (32 banks × 4 byte positions within bank). The 4 muxes share
    // the same bank_rdata fanin so yosys can share decoder logic if it
    // wants — but the OUTPUT cells stay inside this module.
    int                   b;
    logic [31:0]          byte_addr;
    logic [BANK_BITS-1:0] b_idx;

    always_comb begin
        b         = 0;
        byte_addr = '0;
        b_idx     = '0;
        dword_out = '0;
        for (b = 0; b < 4; b++) begin
            byte_addr = base_addr + 32'(b);
            b_idx     = byte_addr[2 +: BANK_BITS];
            if (fwd_mask[b]) begin
                dword_out[b*8 +: 8] = wr_data[byte_idx_in_wr[b] * 8 +: 8];
            end else begin
                dword_out[b*8 +: 8] = bank_rdata[b_idx][byte_addr[1:0] * 8 +: 8];
            end
        end
    end

endmodule
