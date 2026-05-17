// mem/sram_1rw.sv — process-portable single-ported SRAM behavioral model.
//
// One read OR one write per cycle (1RW). On en=1:
//   - we=1: mem[addr] <= wdata.
//   - we=0: rdata <= mem[addr]  (1-cycle read latency).
//
// Why this exists: SMEM's 32 banks each need a 1RW macro. Wrapping bank
// storage behind this module lets `tech/<process>/sram_1rw.sv` swap in the
// vendor SRAM macro at synth time (sky130, GF180, etc.) without touching
// any pipeline logic. This file is the behavioral fallback used during
// pre-silicon verification.
//
// Constraints (must match real SRAM behavior, enforced by callers):
//   - en=1 must come with EITHER we=1 (write) OR we=0 (read), never both
//     in any meaningful "do both" sense. The model honors the if/else
//     ordering but a real macro cannot.
//   - rdata is a registered output. Reading mem[addr] while we=1 to the
//     same addr is NOT a defined operation on this port — callers must
//     not rely on write-then-read forwarding through sram_1rw. Any
//     forwarding behavior lives in the wrapper above (e.g. smem.sv).

module sram_1rw #(
    parameter int WORDS = 64,
    parameter int W     = 32
) (
    input  logic                       clk,
    input  logic                       en,
    input  logic                       we,
    input  logic [$clog2(WORDS)-1:0]   addr,
    input  logic [W-1:0]               wdata,
    output logic [W-1:0]               rdata
);

`ifdef USE_SKY130_MACRO
    // Real sky130 SRAM macro: 256 words × 32 bits, 1RW + 1R.
    // Requires WORDS<=256 and W==32; the second R port is tied off.
    logic [7:0] macro_addr;
    assign macro_addr = {{(8 - $clog2(WORDS)){1'b0}}, addr};

    // In sim (USE_SKY130_MACRO_SIM), override VERBOSE=0 to suppress the
    // behavioral .v model's $display debug prints. In synth, the macro is
    // a blackbox derived from .lib + .lef and has no VERBOSE parameter, so
    // the override would error out — use the parameterless instantiation.
`ifdef USE_SKY130_MACRO_SIM
    sky130_sram_1kbyte_1rw1r_32x256_8 #(.VERBOSE(0)) u_macro (
`else
    sky130_sram_1kbyte_1rw1r_32x256_8 u_macro (
`endif
        // Port 0: RW
        .clk0   (clk),
        .csb0   (~en),         // active-low chip select
        .web0   (~we),         // active-low write enable
        .wmask0 (4'b1111),     // always full-word writes
        .addr0  (macro_addr),
        .din0   (wdata),
        .dout0  (rdata),
        // Port 1: R (unused)
        .clk1   (1'b0),
        .csb1   (1'b1),
        .addr1  (8'b0),
        .dout1  ()
    );
`else
    // Behavioral fallback for sim: pure flip-flop array.
    logic [W-1:0] mem [WORDS];
    always_ff @(posedge clk) begin
        if (en) begin
            if (we) mem[addr] <= wdata;
            else    rdata     <= mem[addr];
        end
    end
`endif

endmodule
