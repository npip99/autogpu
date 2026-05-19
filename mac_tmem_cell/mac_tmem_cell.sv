// mac_tmem_cell.sv -- one MAC + one cell's per-position TMEM micro-storage.
//
// Phase 7i-1: systolic leaf. The five-signal compute packet (a, b, compute,
// slot, accum) flows from west/north neighbors through a single pipeline
// register per cell and out to east/south neighbors. The MAC operates on the
// FRESH inputs (a_in / b_in / compute_in / slot_in / accum_in) of the current
// cycle; *_out is a registered copy that downstream cells read next cycle, so
// the per-cell propagation delay is 1.
//
// Latency to the (M-1, N-1) corner for a K-loop fed at cycle 0:
//     K + M + N - 2 cycles
//
// drain / init / scrub remain broadcast inputs (per-cell, NOT systolic).
// drain is only used between MMA bursts so its wires can be slow paths;
// scrub is a one-shot from reset_seq; init is unused in v1.
//
// Datapath:
//   - Two fp8_decode (combinational) for a/b -> fp32.
//   - One fp32_fma (combinational, NumPipeRegs=0): a*b + addend, registered
//     into storage on the next clock edge.
//   - storage[N_SLOTS] register file. Small enough to map to FFs.
//
// Storage priority (only one of the three may fire per cycle; pymodel asserts
// the mutex):
//   1. scrub_en    : storage[*] <= 0
//   2. init_en     : storage[init_slot] <= init_data
//   3. compute_in  : storage[slot_in]   <= a*b + (accum_in ? storage[slot_in] : 0)
//
// Drain: registered with 1-cycle latency. drain_en at cycle T captures slot;
// drain_data at cycle T+1 reflects the stored value, INCLUDING any same-cycle
// commit by scrub/init/compute (write-then-drain ordering).

module mac_tmem_cell #(
    parameter int N_SLOTS = 4
) (
    input  logic                       clk,
    input  logic                       reset,

    // ---- Systolic compute packet (west/north -> east/south) -----------
    // The five-signal "wave" that flows through the array. compute_in says
    // "this cycle's (a_in, b_in, slot_in, accum_in) is a valid MAC."
    input  logic                       compute_in,
    input  logic [7:0]                 a_in,
    input  logic [7:0]                 b_in,
    input  logic [$clog2(N_SLOTS)-1:0] slot_in,
    input  logic                       accum_in,

    // Registered pass-through to next cell. Combinational from internal
    // pipe regs (which update at clk edge from *_in).
    output logic                       compute_out,
    output logic [7:0]                 a_out,
    output logic [7:0]                 b_out,
    output logic [$clog2(N_SLOTS)-1:0] slot_out,
    output logic                       accum_out,

    // ---- Drain (broadcast, registered, 1-cycle latency) ---------------
    input  logic                       drain_en,
    input  logic [$clog2(N_SLOTS)-1:0] drain_slot,
    output logic [31:0]                drain_data,

    // ---- Init (broadcast; tcgen05.cp-style; stable port for v1) -------
    input  logic                       init_en,
    input  logic [$clog2(N_SLOTS)-1:0] init_slot,
    input  logic [31:0]                init_data,

    // ---- Scrub (broadcast, one-shot from reset_seq) -------------------
    input  logic                       scrub_en
);

    // ---- Storage ------------------------------------------------------
    logic [31:0] storage [N_SLOTS];

    // ---- Pipeline registers for the compute packet --------------------
    // *_out is the registered copy of *_in. Downstream cell reads *_out
    // one cycle after we receive *_in -- this is the per-cell hop delay.
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

    // ---- Pending drain (captured cycle T-1, drained cycle T) ----------
    logic                            drain_pending_valid;
    logic [$clog2(N_SLOTS)-1:0]      drain_pending_slot;

    // ---- Combinational decode + FMA datapath --------------------------
    // FMA fires on the FRESH packet (*_in), not the pipe regs. This keeps
    // the per-cell latency at 1 (delay is in the propagation, not the
    // compute) and matches the K + M + N - 2 total.
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

    // FMA addend: storage[slot_in] when accum_in=1, else 0.
    logic [31:0] fma_addend;
    logic [31:0] fma_result;
    assign fma_addend = accum_in ? storage[slot_in] : 32'd0;

    fp32_fma u_fma (
        .a      (a_fp32),
        .b      (b_fp32),
        .c      (fma_addend),
        .result (fma_result)
    );

    // ---- Drain write-forwarding (same-cycle commit visible at drain) --
    logic [31:0] drain_forwarded;
    always_comb begin
        drain_forwarded = storage[drain_pending_slot];
        if (scrub_en) begin
            drain_forwarded = 32'd0;
        end else if (init_en && (init_slot == drain_pending_slot)) begin
            drain_forwarded = init_data;
        end else if (compute_in && (slot_in == drain_pending_slot)) begin
            drain_forwarded = fma_result;
        end
    end

    // ---- Sequential ---------------------------------------------------
    integer s;
    always_ff @(posedge clk) begin
        if (reset) begin
            drain_data          <= 32'd0;
            drain_pending_valid <= 1'b0;
            drain_pending_slot  <= '0;
            compute_pipe        <= 1'b0;
            a_pipe              <= 8'd0;
            b_pipe              <= 8'd0;
            slot_pipe           <= '0;
            accum_pipe          <= 1'b0;
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

            // 3. Drain the previous-cycle pending read with write-forwarding.
            if (drain_pending_valid) begin
                drain_data <= drain_forwarded;
            end else begin
                drain_data <= 32'd0;
            end

            // 4. Capture new pending drain.
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
