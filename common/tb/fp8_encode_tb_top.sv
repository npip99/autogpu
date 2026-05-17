// Combinational testbench top for fp8_encode.
// cocotb drives `fp32` and reads `fp8`.

module fp8_encode_tb_top (
    input  logic [31:0] fp32,
    output logic [7:0]  fp8
);
    fp8_encode u_dut (
        .fp32 (fp32),
        .fp8  (fp8)
    );
endmodule
