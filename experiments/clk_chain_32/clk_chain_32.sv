// V1 for #40: characterize a 32-stage clock feedthrough chain on asap7.
//
// 32 BUFx2_ASAP7_75t_R buffers in series. ONE FF clocked by the end of
// the chain. STA must time the clock arrival at this FF through all 32
// stages — that gives us the insertion delay + OCV growth we need to
// characterize.
//
// One-FF-only design avoids the "many distinct clock domains" problem
// that breaks CTS when each chain stage clocks its own FF. With ONE
// terminal FF + a generated_clock declaration on chain[32], CTS treats
// the chain as a pre-built derived clock and leaves it alone.

`default_nettype none

module clk_chain_32 (
    input  wire clk_in,
    input  wire rst_n,
    input  wire d_in,
    output wire q_out
);

    wire [32:0] chain;
    assign chain[0] = clk_in;

    genvar i;
    generate
        for (i = 0; i < 32; i = i + 1) begin : g_buf
            (* keep = "true" *)
            BUFx2_ASAP7_75t_R u_buf (
                .A (chain[i]),
                .Y (chain[i+1])
            );
        end
    endgenerate

    // ONE flop clocked by the END of the chain. The data path (d_in port
    // → q FF.D) is trivially short; the clock path (clk_in → 32 chain
    // stages → q.CLK) is the long one STA must report.
    logic q;
    always_ff @(posedge chain[32]) begin
        if (!rst_n) q <= 1'b0;
        else        q <= d_in;
    end

    assign q_out = q;

endmodule

`default_nettype wire
