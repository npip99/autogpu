// smoke_top.sv — minimal design used to validate the OpenLane → sky130 GDS
// toolchain end-to-end. If this passes, the install is working; remaining
// failures on chip_top are about chip_top's RTL, not the toolchain.
//
// Pure Verilog-2005-style SystemVerilog: no packages, no automatic functions,
// no part-select-on-call, no structs. Anything more exotic risks reintroducing
// the very tool-config issues this smoke test is meant to isolate.

module smoke_top (
    input  wire        clk,
    input  wire        reset,
    input  wire [7:0]  d_in,
    output reg  [7:0]  q_out
);

    always @(posedge clk) begin
        if (reset) begin
            q_out <= 8'h00;
        end else begin
            q_out <= d_in ^ {q_out[6:0], q_out[7]};
        end
    end

endmodule
