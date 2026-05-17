// fp32_fma.sv -- combinational IEEE 754 fp32 fused multiply-add (a*b + c).
//
// Thin wrapper around fpnew_fma (CVFPU) configured for:
//   - FpFormat    = fpnew_pkg::FP32
//   - NumPipeRegs = 0  (purely combinational)
//   - rnd_mode_i  = fpnew_pkg::RNE  (round-to-nearest-even)
//   - op_i        = fpnew_pkg::FMADD (computes operand_a * operand_b + operand_c)
//
// fpnew_fma always exposes clk_i / rst_ni in its port list -- even with
// NumPipeRegs=0 they're unused for data. We tie clk_i = 1'b0 and
// rst_ni = 1'b1 here because none of the pipeline FFs are instantiated
// when NumPipeRegs is zero.

`include "common_cells/registers.svh"

module fp32_fma (
    input  logic [31:0] a,
    input  logic [31:0] b,
    input  logic [31:0] c,
    output logic [31:0] result
);

    // FMADD: result = a * b + c. operands_i is a 3-element vector:
    //   [0] = operand_a (multiplicand)
    //   [1] = operand_b (multiplier)
    //   [2] = operand_c (addend)
    logic [2:0][31:0] operands;
    assign operands[0] = a;
    assign operands[1] = b;
    assign operands[2] = c;

    // Status / handshake / tag wires (tied off; not used in combinational mode).
    fpnew_pkg::status_t status_unused;
    logic               extension_bit_unused;
    logic               tag_unused;
    logic               mask_unused;
    logic               aux_unused;
    logic               in_ready_unused;
    logic               out_valid_unused;
    logic               busy_unused;
    logic               early_out_valid_unused;

    // Vendored CVFPU (common/fpnew/*) trips several Verilator width / unnamed-
    // generate-block lints. Phase 7f's lint sweep removed every project-side
    // suppression; the remaining narrow window below confines that exemption
    // to the fpnew_fma instance itself.
    /* verilator lint_off WIDTHTRUNC */
    /* verilator lint_off WIDTHEXPAND */
    /* verilator lint_off UNOPTFLAT */
    /* verilator lint_off ASCRANGE */
    /* verilator lint_off SPLITVAR */
    /* verilator lint_off GENUNNAMED */
    fpnew_fma #(
        .FpFormat    ( fpnew_pkg::FP32 ),
        .NumPipeRegs ( 0               ),
        .PipeConfig  ( fpnew_pkg::BEFORE ),
        .TagType     ( logic           ),
        .AuxType     ( logic           )
    ) i_fma (
        .clk_i             ( 1'b0  ),
        .rst_ni            ( 1'b1  ),
        .operands_i        ( operands ),
        .is_boxed_i        ( 3'b111 ),
        .rnd_mode_i        ( fpnew_pkg::RNE ),
        .op_i              ( fpnew_pkg::FMADD ),
        .op_mod_i          ( 1'b0 ),
        .tag_i             ( 1'b0 ),
        .mask_i            ( 1'b0 ),
        .aux_i             ( 1'b0 ),
        .in_valid_i        ( 1'b1 ),
        .in_ready_o        ( in_ready_unused ),
        .flush_i           ( 1'b0 ),
        .result_o          ( result ),
        .status_o          ( status_unused ),
        .extension_bit_o   ( extension_bit_unused ),
        .tag_o             ( tag_unused ),
        .mask_o            ( mask_unused ),
        .aux_o             ( aux_unused ),
        .out_valid_o       ( out_valid_unused ),
        .out_ready_i       ( 1'b1 ),
        .busy_o            ( busy_unused ),
        .reg_ena_i         ( 1'b0 ),
        .early_out_valid_o ( early_out_valid_unused )
    );
    /* verilator lint_on GENUNNAMED */
    /* verilator lint_on SPLITVAR */
    /* verilator lint_on ASCRANGE */
    /* verilator lint_on UNOPTFLAT */
    /* verilator lint_on WIDTHEXPAND */
    /* verilator lint_on WIDTHTRUNC */

endmodule
