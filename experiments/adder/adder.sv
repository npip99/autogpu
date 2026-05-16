// adder.sv — 8-bit + 8-bit → 9-bit registered adder, gated by `en`.
// See pymodel.py for the canonical spec. This module must match it.

module adder (
    input  logic       clk,
    input  logic       en,
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [8:0] sum,
    output logic       valid
);

    always_ff @(posedge clk) begin
        if (en) begin
            sum   <= a + b;
            valid <= 1'b1;
        end else begin
            sum   <= 9'd0;
            valid <= 1'b0;
        end
    end

endmodule
