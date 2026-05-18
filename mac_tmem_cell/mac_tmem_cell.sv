// mac_tmem_cell.sv -- one MAC + one cell's per-position TMEM micro-storage.
//
// Phase 7h-1 leaf. See pymodel/mac_tmem_cell.py for the canonical spec; this
// module must match it cycle-by-cycle. compute_array (Phase 7h-2) will
// instantiate MMA_M x MMA_N of these and feed them via a broadcast network.
//
// Datapath:
//   - Two fp8_decode (combinational) for a/b -> fp32.
//   - One fp32_fma (combinational, NumPipeRegs=0): a*b + addend, registered
//     into storage on the next clock edge.
//   - storage[N_SLOTS] register file. Small enough to map to FFs.
//
// Storage priority (only one of the three may fire per cycle; pymodel asserts
// the mutex):
//   1. scrub_en  : storage[*] <= 0
//   2. init_en   : storage[init_slot] <= init_data
//   3. compute   : storage[slot]      <= a*b + (accum ? storage[slot] : 0)
//
// Drain: registered with 1-cycle latency. drain_en at cycle T captures slot;
// drain_data at cycle T+1 reflects the stored value, INCLUDING any same-cycle
// commit by scrub/init/compute (write-then-drain ordering, matching tmem.sv
// pack_slot()). Without this forwarding mux, drain would return the prior
// register value because NBAs commit at end-of-cycle.

module mac_tmem_cell #(
    parameter int N_SLOTS = 4
) (
    input  logic                       clk,
    input  logic                       reset,

    // Per-cycle FMA (broadcast network drives these).
    input  logic                       compute,
    input  logic [7:0]                 a,
    input  logic [7:0]                 b,
    input  logic [$clog2(N_SLOTS)-1:0] slot,
    input  logic                       accum,

    // Drain (registered, 1-cycle latency).
    input  logic                       drain_en,
    input  logic [$clog2(N_SLOTS)-1:0] drain_slot,
    output logic [31:0]                drain_data,

    // Init (tcgen05.cp-style SMEM->TMEM seed; ports stable for v1 use).
    input  logic                       init_en,
    input  logic [$clog2(N_SLOTS)-1:0] init_slot,
    input  logic [31:0]                init_data,

    // Scrub (reset_seq drives this; clears all slots in one cycle).
    input  logic                       scrub_en
);

    // ---- Storage ------------------------------------------------------
    logic [31:0] storage [N_SLOTS];

    // ---- Pending drain (captured cycle T-1, drained cycle T) ----------
    logic                            drain_pending_valid;
    logic [$clog2(N_SLOTS)-1:0]      drain_pending_slot;

    // ---- Combinational decode + FMA datapath --------------------------
    logic [31:0] a_fp32;
    logic [31:0] b_fp32;

    fp8_decode u_dec_a (
        .fp8  (a),
        .fp32 (a_fp32)
    );
    fp8_decode u_dec_b (
        .fp8  (b),
        .fp32 (b_fp32)
    );

    // FMA addend: storage[slot] when accum=1, else 0 (initialize).
    logic [31:0] fma_addend;
    logic [31:0] fma_result;
    assign fma_addend = accum ? storage[slot] : 32'd0;

    fp32_fma u_fma (
        .a      (a_fp32),
        .b      (b_fp32),
        .c      (fma_addend),
        .result (fma_result)
    );

    // ---- Same-cycle commit value of storage[drain_pending_slot] -------
    //
    // The pymodel commits scrub/init/compute BEFORE draining the previous
    // cycle's pending read. NBAs to `storage` happen at end-of-cycle, so
    // for a drain that matches the slot we're committing this cycle, we
    // must forward the post-commit value into drain_data instead of the
    // pre-commit storage register.
    logic [31:0] drain_forwarded;
    always_comb begin
        // Default: registered value.
        drain_forwarded = storage[drain_pending_slot];

        // Priority matches the sequential update below.
        if (scrub_en) begin
            drain_forwarded = 32'd0;
        end else if (init_en && (init_slot == drain_pending_slot)) begin
            drain_forwarded = init_data;
        end else if (compute && (slot == drain_pending_slot)) begin
            drain_forwarded = fma_result;
        end
    end

    // ---- Sequential -----------------------------------------------------
    integer s;
    always_ff @(posedge clk) begin
        if (reset) begin
            drain_data          <= 32'd0;
            drain_pending_valid <= 1'b0;
            drain_pending_slot  <= '0;
            // Storage contents preserved across `reset` (matches tmem.sv).
            // Power-on zeroing happens via scrub_en, driven by reset_seq.
        end else begin
            // 1. Storage commit (mutex via spec; SV doesn't arbitrate).
            if (scrub_en) begin
                for (s = 0; s < N_SLOTS; s = s + 1) begin
                    storage[s] <= 32'd0;
                end
            end else if (init_en) begin
                storage[init_slot] <= init_data;
            end else if (compute) begin
                storage[slot] <= fma_result;
            end

            // 2. Drain the previous-cycle pending read with write-forwarding.
            if (drain_pending_valid) begin
                drain_data <= drain_forwarded;
            end else begin
                drain_data <= 32'd0;
            end

            // 3. Capture new pending drain.
            if (drain_en) begin
                drain_pending_valid <= 1'b1;
                drain_pending_slot  <= drain_slot;
            end else begin
                drain_pending_valid <= 1'b0;
            end
        end
    end

    // ---- Synthesizable assertions (sim-only) --------------------------
`ifndef SYNTHESIS
    always_ff @(posedge clk) begin
        if (!reset) begin
            assert (!(scrub_en && compute))
                else $fatal(1, "mac_tmem_cell: scrub_en concurrent with compute");
            assert (!(scrub_en && init_en))
                else $fatal(1, "mac_tmem_cell: scrub_en concurrent with init_en");
            assert (!(init_en && compute))
                else $fatal(1, "mac_tmem_cell: init_en concurrent with compute");
        end
    end
`endif

endmodule
