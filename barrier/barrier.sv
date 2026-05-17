// barrier.sv — mbarrier state machine, NUM_BARRIERS independent objects.
//
// SV implementation of pymodel.barrier.Barrier. See pymodel/barrier.py for the
// canonical spec; this module must match it cycle-by-cycle.
//
// Per-bar state (matches ISA.md §"Barrier State (mbarrier)"):
//     pending     : 16-bit, arrivals remaining before flip-eligible
//     expected    : 16-bit, reload value applied on flip
//     tx_pending  : 32-bit, in-flight LOAD bytes
//     phase       : 1-bit,  toggles on flip
//
// PRIORITY ORDER (per cycle, per bar):
//     1. INIT       — dominant; drops ADD_TX/SUB_TX/ARRIVE on same bar
//     2. ADD_TX
//     3. SUB_TX     — marks bar as "decremented" (flip-eligible)
//     4. ARRIVE     — channel a + channel b; same bar in one cycle → -=2
//                      also marks bar as "decremented"
//     5. FLIP CHECK — only on bars marked "decremented" this cycle
//                      INIT alone (even with count=0) does NOT flip.
//
// wait_done is COMBINATIONAL (not registered):
//     wait_done = (bars[query_bar_id].phase != query_expected_phase)
//
// Reset is dominant: zeros every bar's state and clears wait_done.
//
// Per-bar registered state is also exposed as packed arrays
// (bars_pending / bars_expected / bars_tx_pending / bars_phase) so the cocotb
// testbench can index a single signal per cycle.

module barrier #(
    parameter int NUM_BARRIERS = 8
) (
    input  logic                       clk,
    input  logic                       reset,

    // INIT
    input  logic                       init_en,
    input  logic [31:0]                init_bar_id,
    input  logic [15:0]                init_count,

    // ARRIVE (two source channels: LOAD, MMA)
    input  logic                       arrive_en_a,
    input  logic [31:0]                arrive_bar_id_a,
    input  logic                       arrive_en_b,
    input  logic [31:0]                arrive_bar_id_b,

    // ADD_TX
    input  logic                       add_tx_en,
    input  logic [31:0]                add_tx_bar_id,
    input  logic [31:0]                add_tx_bytes,

    // SUB_TX
    input  logic                       sub_tx_en,
    input  logic [31:0]                sub_tx_bar_id,
    input  logic [31:0]                sub_tx_bytes,

    // Combinational WAIT_QUERY. query_bar_id is 32-bit on the wire for
    // uniformity; only the low log2(NUM_BARRIERS) bits index the array.
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [31:0]                query_bar_id,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic                       query_expected_phase,
    output logic                       wait_done,

    // Observable per-bar state (registered; packed for cocotb indexing).
    output logic [NUM_BARRIERS*16-1:0] bars_pending,
    output logic [NUM_BARRIERS*16-1:0] bars_expected,
    output logic [NUM_BARRIERS*32-1:0] bars_tx_pending,
    output logic [NUM_BARRIERS-1:0]    bars_phase
);

    // Per-bar storage as unpacked arrays — easier to index inside always_ff.
    logic [15:0] pending    [NUM_BARRIERS];
    logic [15:0] expected_r [NUM_BARRIERS];
    logic [31:0] tx_pending [NUM_BARRIERS];
    logic        phase      [NUM_BARRIERS];

    initial begin
        for (int b = 0; b < NUM_BARRIERS; b++) begin
            pending[b]    = 16'd0;
            expected_r[b] = 16'd0;
            tx_pending[b] = 32'd0;
            phase[b]      = 1'b0;
        end
    end

    // Pack observable state into the output ports each cycle.
    always_comb begin
        for (int b = 0; b < NUM_BARRIERS; b++) begin
            bars_pending   [b*16 +: 16] = pending[b];
            bars_expected  [b*16 +: 16] = expected_r[b];
            bars_tx_pending[b*32 +: 32] = tx_pending[b];
            bars_phase     [b]          = phase[b];
        end
    end

    // Combinational wait_done.
    assign wait_done = (phase[query_bar_id] != query_expected_phase);

    // ------------------------------------------------------------------
    // Update logic.
    // ------------------------------------------------------------------
    // Strategy: build next-state variables initialized to current state, then
    // mutate them in the priority order documented above. Commit via NBAs.
    // `decremented[b]` tracks whether bar b received a SUB_TX or ARRIVE this
    // cycle (gates FLIP CHECK). INIT alone does NOT set `decremented`.
    always_ff @(posedge clk) begin
        if (reset) begin
            for (int b = 0; b < NUM_BARRIERS; b++) begin
                pending[b]    <= 16'd0;
                expected_r[b] <= 16'd0;
                tx_pending[b] <= 32'd0;
                phase[b]      <= 1'b0;
            end
        end else begin
            // Next-state scratch — start from current values.
            automatic logic [15:0] n_pending    [NUM_BARRIERS];
            automatic logic [15:0] n_expected   [NUM_BARRIERS];
            automatic logic [31:0] n_tx_pending [NUM_BARRIERS];
            automatic logic        n_phase      [NUM_BARRIERS];
            automatic logic        decremented  [NUM_BARRIERS];

            for (int b = 0; b < NUM_BARRIERS; b++) begin
                n_pending[b]    = pending[b];
                n_expected[b]   = expected_r[b];
                n_tx_pending[b] = tx_pending[b];
                n_phase[b]      = phase[b];
                decremented[b]  = 1'b0;
            end

            // 1. INIT — dominant. Resets bar fully and zeroes phase.
            if (init_en) begin
                n_pending[init_bar_id]    = init_count;
                n_expected[init_bar_id]   = init_count;
                n_tx_pending[init_bar_id] = 32'd0;
                n_phase[init_bar_id]      = 1'b0;
            end

            // Helper predicate: does INIT target this bar this cycle?
            // (inlined below as init_en && init_bar_id == X)

            // 2. ADD_TX (dropped if INIT targets same bar).
            if (add_tx_en && !(init_en && init_bar_id == add_tx_bar_id)) begin
                n_tx_pending[add_tx_bar_id] = n_tx_pending[add_tx_bar_id]
                                              + add_tx_bytes;
            end

            // 3. SUB_TX (dropped if INIT targets same bar). Marks decremented.
            if (sub_tx_en && !(init_en && init_bar_id == sub_tx_bar_id)) begin
                n_tx_pending[sub_tx_bar_id] = n_tx_pending[sub_tx_bar_id]
                                              - sub_tx_bytes;
                decremented[sub_tx_bar_id]  = 1'b1;
            end

            // 4. ARRIVE — two channels; same bar in one cycle decrements by 2.
            //    Each channel dropped independently if INIT targets its bar.
            if (arrive_en_a && !(init_en && init_bar_id == arrive_bar_id_a)) begin
                n_pending[arrive_bar_id_a] = n_pending[arrive_bar_id_a] - 16'd1;
                decremented[arrive_bar_id_a] = 1'b1;
            end
            if (arrive_en_b && !(init_en && init_bar_id == arrive_bar_id_b)) begin
                n_pending[arrive_bar_id_b] = n_pending[arrive_bar_id_b] - 16'd1;
                decremented[arrive_bar_id_b] = 1'b1;
            end

            // 5. FLIP CHECK — only on bars that had SUB_TX or ARRIVE this cycle.
            for (int b = 0; b < NUM_BARRIERS; b++) begin
                if (decremented[b]
                        && n_pending[b] == 16'd0
                        && n_tx_pending[b] == 32'd0) begin
                    n_phase[b]   = ~n_phase[b];
                    n_pending[b] = n_expected[b];
                end
            end

            // Commit.
            for (int b = 0; b < NUM_BARRIERS; b++) begin
                pending[b]    <= n_pending[b];
                expected_r[b] <= n_expected[b];
                tx_pending[b] <= n_tx_pending[b];
                phase[b]      <= n_phase[b];
            end
        end
    end

endmodule
