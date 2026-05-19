// tile_buf_8row.sv — 8-row × ROW_W-bit banked storage with registered read.
//
// One sub-bank of the store engine's tile buffer. Four of these stacked give
// the full MMA_M × MMA_N fp32 output tile. Designed to be:
//
//   1. Synthesizable as FFs today (~8 KB, hardens cleanly in OpenLane).
//   2. Drop-in swappable with an SRAM-backed implementation later — same
//      port contract (write 1 cycle in, read 1 cycle out, registered).
//
// Port contract (write):
//   wr_en pulse with (wr_row, wr_data) → memory[wr_row] updated next edge.
//
// Port contract (read):
//   rd_en pulse with rd_row → rd_data valid the FOLLOWING cycle.
//   rd_data registered output; rd_en=0 keeps prior rd_data.
//
// This 1-cycle read latency matches sky130 SRAM macros (e.g. sky130_sram_*).
// To later swap FFs for an SRAM macro array, replace the body — store.sv
// does NOT need to change because the port contract is identical.

`default_nettype none

module tile_buf_8row #(
    parameter int N_ROWS = 8,
    parameter int ROW_W  = 1024
) (
    input  wire                  clk,
    input  wire                  reset,

    input  wire                  wr_en,
    input  wire [$clog2(N_ROWS)-1:0] wr_row,
    input  wire [ROW_W-1:0]      wr_data,

    input  wire                  rd_en,
    input  wire [$clog2(N_ROWS)-1:0] rd_row,
    output logic [ROW_W-1:0]     rd_data
);

    logic [ROW_W-1:0] mem [N_ROWS];

    integer i;
    always_ff @(posedge clk) begin
        if (reset) begin
            rd_data <= '0;
            for (i = 0; i < N_ROWS; i = i + 1) begin
                mem[i] <= '0;
            end
        end else begin
            if (wr_en) begin
                mem[wr_row] <= wr_data;
            end
            if (rd_en) begin
                rd_data <= mem[rd_row];
            end
        end
    end

endmodule

`default_nettype wire
