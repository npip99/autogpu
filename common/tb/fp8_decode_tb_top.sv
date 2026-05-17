// Combinational testbench top for fp8_decode.
// cocotb drives `fp8` and reads `fp32`.

module fp8_decode_tb_top (
    input  logic [7:0]  fp8,
    output logic [31:0] fp32
);
    fp8_decode u_dut (
        .fp8  (fp8),
        .fp32 (fp32)
    );
endmodule
