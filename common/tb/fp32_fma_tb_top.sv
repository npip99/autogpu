// Combinational testbench top for fp32_fma.
// cocotb drives `a`, `b`, `c` and reads `result`.

module fp32_fma_tb_top (
    input  logic [31:0] a,
    input  logic [31:0] b,
    input  logic [31:0] c,
    output logic [31:0] result
);
    fp32_fma u_dut (
        .a      (a),
        .b      (b),
        .c      (c),
        .result (result)
    );
endmodule
