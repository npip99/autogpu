// mac_tmem_cell.sv -- one MAC + one cell's per-position TMEM micro-storage.
//
// Phase 7i-6: systolic drain (drain flows north through the array).
//
// The five-signal compute packet (a, b, compute, slot, accum) flows from
// west/north neighbors through a single pipeline register per cell and out
// to east/south neighbors. compute_array's cmd_unit feeds the west + north
// edges; mac_tmem_cell's east + south outputs feed neighbors.
//
// Drain port: drain_in (32 bits, from south neighbor) and drain_out (32
// bits, to north neighbor) form a per-column drain chain. When drain_en
// pulses for one cycle (broadcast to all cells with drain_slot), every
// cell injects storage[drain_slot] into drain_out at the next edge. On
// subsequent cycles (drain_en=0), drain_out registers the drain_in from
// south. Thus stored values shift north one cell per cycle, exiting the
// chip at row 0's drain_out. Drain takes M cycles total — no centralised
// drain mux required.
//
// Latency to the (M-1, N-1) corner for a K-loop fed at cycle 0:
//     K + M + N - 2 cycles.
//
// Storage priority (only one of the three may fire per cycle; pymodel
// asserts the mutex):
//   1. scrub_en    : storage[*] <= 0
//   2. init_en     : storage[init_slot] <= init_data
//   3. compute_in  : storage[slot_in]   <= a*b + (accum_in ? storage[slot_in] : 0)
//
// drain_en + drain_slot can coexist with compute (slot-disjoint guarantee).
// Same-slot compute/drain reads PRE-WRITE storage at the edge.

module mac_tmem_cell #(
    parameter int N_SLOTS = 4
) (
    input  logic                       clk,
    input  logic                       reset,

    // ---- Systolic compute packet ---------------------------------------
    input  logic                       compute_in,
    input  logic [7:0]                 a_in,
    input  logic [7:0]                 b_in,
    input  logic [$clog2(N_SLOTS)-1:0] slot_in,
    input  logic                       accum_in,
    output logic                       compute_out,
    output logic [7:0]                 a_out,
    output logic [7:0]                 b_out,
    output logic [$clog2(N_SLOTS)-1:0] slot_out,
    output logic                       accum_out,

    // ---- Systolic drain (south -> north) --------------------------------
    input  logic [31:0]                drain_in,
    output logic [31:0]                drain_out,

    // ---- Drain control (broadcast, registered, 1-cycle latency) --------
    input  logic                       drain_en,
    input  logic [$clog2(N_SLOTS)-1:0] drain_slot,

    // ---- Init (broadcast; tcgen05.cp-style; stable port for v1) --------
    input  logic                       init_en,
    input  logic [$clog2(N_SLOTS)-1:0] init_slot,
    input  logic [31:0]                init_data,

    // ---- Scrub (broadcast, one-shot from reset_seq) --------------------
    input  logic                       scrub_en
);

    // ---- Storage ------------------------------------------------------
    logic [31:0] storage [N_SLOTS];

    // ---- Pipeline registers for the compute packet --------------------
    logic                       compute_pipe;
    logic [7:0]                 a_pipe;
    logic [7:0]                 b_pipe;
    logic [$clog2(N_SLOTS)-1:0] slot_pipe;
    logic                       accum_pipe;
    assign compute_out = compute_pipe;
    assign a_out       = a_pipe;
    assign b_out       = b_pipe;
    assign slot_out    = slot_pipe;
    assign accum_out   = accum_pipe;

    // ---- Combinational decode + FMA datapath -------------------------
    logic [31:0] a_fp32;
    logic [31:0] b_fp32;

    fp8_decode u_dec_a (
        .fp8  (a_in),
        .fp32 (a_fp32)
    );
    fp8_decode u_dec_b (
        .fp8  (b_in),
        .fp32 (b_fp32)
    );

    logic [31:0] fma_addend;
    logic [31:0] fma_result;
    assign fma_addend = accum_in ? storage[slot_in] : 32'd0;

    fp32_fma u_fma (
        .a      (a_fp32),
        .b      (b_fp32),
        .c      (fma_addend),
        .result (fma_result)
    );

    // ---- Sequential ---------------------------------------------------
    integer s;
    always_ff @(posedge clk) begin
        if (reset) begin
            drain_out    <= 32'd0;
            compute_pipe <= 1'b0;
            a_pipe       <= 8'd0;
            b_pipe       <= 8'd0;
            slot_pipe    <= '0;
            accum_pipe   <= 1'b0;
            // Storage contents preserved across `reset`; zero via scrub_en.
        end else begin
            // 1. Storage commit (mutex via spec).
            if (scrub_en) begin
                for (s = 0; s < N_SLOTS; s = s + 1) begin
                    storage[s] <= 32'd0;
                end
            end else if (init_en) begin
                storage[init_slot] <= init_data;
            end else if (compute_in) begin
                storage[slot_in] <= fma_result;
            end

            // 2. Pipeline-register the compute packet for downstream cells.
            compute_pipe <= compute_in;
            a_pipe       <= a_in;
            b_pipe       <= b_in;
            slot_pipe    <= slot_in;
            accum_pipe   <= accum_in;

            // 3. Drain: either inject storage[drain_slot] OR forward south.
            if (drain_en) begin
                drain_out <= storage[drain_slot];
            end else begin
                drain_out <= drain_in;
            end
        end
    end

    // ---- Synthesizable assertions (sim-only) --------------------------
`ifndef SYNTHESIS
    always_ff @(posedge clk) begin
        if (!reset) begin
            assert (!(scrub_en && compute_in))
                else $fatal(1, "mac_tmem_cell: scrub_en concurrent with compute_in");
            assert (!(scrub_en && init_en))
                else $fatal(1, "mac_tmem_cell: scrub_en concurrent with init_en");
            assert (!(init_en && compute_in))
                else $fatal(1, "mac_tmem_cell: init_en concurrent with compute_in");
        end
    end
`endif

endmodule
