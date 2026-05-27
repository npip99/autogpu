// chip_top.sv — synthesizable top of the toy fp8-matmul GPU.
//
// What's INSIDE the die (this file):
//   cmdproc, smem (32 sram_1rw banks), compute_array (1024 mac_tmem_cell
//   leaves: 1 fp32 FMA + per-cell N_SLOTS register file at each (i, j)
//   position; replaces the old monolithic mma + tmem pair as of Phase
//   7h-3), load, store, barrier, reset_seq.
//
// What's OUTSIDE the die:
//   gmem (off-chip DRAM model). chip_top exposes a simple memory-
//   controller bus (mc_*) so an external DRAM behavioral model (or, in
//   the future, an AXI4-Lite shim into a real DDR controller) can drive
//   it. The mc_* port shape is identical to what LOAD and STORE drive
//   today (1-beat read/write, 1-cycle round trip on the read path);
//   making this synthesizable to pads is a port-rename in the FPGA / pad
//   wrapper, not a protocol translation.
//
// SEE ALSO: top/tb/chip_tb_top.sv (instantiates chip_top + gmem for the
// cocotb end-to-end tests) and top/README.md for the future AXI4-Lite
// plan.

// MMA_{M,N,K} default to 32 but accept sv2v `-D MMA_M=...` overrides so
// asap7 can harden a small (4×4) chip_top variant against the existing
// hardened-leaf macros without rebuilding compute_array at 32×32. Same
// pattern as compute_array.sv.
`ifndef MMA_M
`define MMA_M 32
`endif
`ifndef MMA_N
`define MMA_N 32
`endif
`ifndef MMA_K
`define MMA_K 32
`endif

module chip_top #(
    parameter int MMA_M            = `MMA_M,
    parameter int MMA_N            = `MMA_N,
    parameter int MMA_K            = `MMA_K,
    parameter int TMEM_SLOTS       = 4,
    parameter int SMEM_BYTES       = 16384,
    parameter int BEAT_BYTES       = 16,
    parameter int NUM_BARRIERS     = 8,
    // cmdproc instruction memory depth (max asm program length).
    parameter int IMEM_DEPTH       = 64,
    // load engine's pending-LOAD queue depth. Small (8) because LOADs are
    // throttled by WAIT; large only inflates load.sv yosys synth time.
    parameter int LOAD_FIFO_DEPTH  = 8,
    // Back-compat alias — some testbenches still read this.
    parameter int INSTR_FIFO_DEPTH = IMEM_DEPTH
) (
    input  logic                          clk,
    input  logic                          reset_in,            // external pin reset

    // Instruction injection (FIFO push, matches cmdproc today).
    input  logic                          instr_push_en,
    input  logic [255:0]                  instr_push_data,

    // Memory controller bus to off-chip DRAM.
    output logic                          mc_wr_en,
    output logic [31:0]                   mc_wr_addr,
    output logic [BEAT_BYTES*8-1:0]       mc_wr_data,
    output logic                          mc_rd_en,
    output logic [31:0]                   mc_rd_addr,
    input  logic [BEAT_BYTES*8-1:0]       mc_rd_data,
    input  logic                          mc_rd_valid,

    // Status / observability.
    output logic                          chip_in_reset,
    output logic                          sys_idle,
    output logic                          scrub_done,

    // Per-engine drive observability (used by cocotb directed tests).
    output logic                          init_en,
    output logic [31:0]                   init_bar_id,
    output logic [15:0]                   init_count,
    output logic [31:0]                   query_bar_id,
    output logic                          query_expected_phase,

    output logic                          mma_start,
    output logic [31:0]                   mma_a_smem_offset,
    output logic [31:0]                   mma_b_smem_offset,
    output logic [31:0]                   mma_d_tmem_slot,
    output logic                          mma_accum,
    output logic [31:0]                   mma_bar_id,

    output logic                          load_issue_en,
    output logic [31:0]                   load_gmem_ptr,
    output logic [31:0]                   load_smem_ptr,
    output logic [31:0]                   load_bytes_n,
    output logic [31:0]                   load_bar_id,

    output logic                          store_issue_en,
    output logic [31:0]                   store_tmem_slot,
    output logic [31:0]                   store_gmem_ptr,
    output logic                          store_dtype,

    // Engine status pulses (for TB wait-on-done).
    output logic                          load_busy,
    output logic                          load_done,
    output logic                          load_accept,
    output logic                          mma_busy,
    output logic                          mma_done,
    output logic                          store_busy,
    output logic                          store_done,
    output logic                          idle
    // (No barrier observable-state ports — testbenches use backdoor
    // access to u_barrier's internal arrays. Exposing them at the chip
    // boundary cost ~520 pins / bond pads for verification-only data.)
);

    // ------------------------------------------------------------------
    // Internal wires (cmdproc <-> engines <-> on-chip memories).
    // ------------------------------------------------------------------
    // cmdproc -> barrier
    logic                              cp_init_en;
    logic [31:0]                       cp_init_bar_id;
    logic [15:0]                       cp_init_count;
    logic [31:0]                       cp_query_bar_id;
    logic                              cp_query_phase;
    logic                              br_wait_done;

    // cmdproc -> MMA
    logic                              cp_mma_start;
    logic [31:0]                       cp_mma_a;
    logic [31:0]                       cp_mma_b;
    logic [31:0]                       cp_mma_d;
    logic                              cp_mma_accum;
    logic [31:0]                       cp_mma_bar;

    // cmdproc -> LOAD
    logic                              cp_load_en;
    logic [31:0]                       cp_load_g;
    logic [31:0]                       cp_load_s;
    logic [31:0]                       cp_load_b;
    logic [31:0]                       cp_load_bar;

    // cmdproc -> STORE
    logic                              cp_store_en;
    logic [31:0]                       cp_store_slot;
    logic [31:0]                       cp_store_g;
    logic                              cp_store_dt;

    // LOAD <-> off-chip MC (routed through chip pins).
    logic                              l_gmem_rd_en;
    logic [31:0]                       l_gmem_rd_addr;
    // gmem read response comes back via mc_rd_data / mc_rd_valid.

    // LOAD -> smem (LOAD_WR)
    logic                              l_smem_wr_en;
    logic [31:0]                       l_smem_wr_addr;
    logic [BEAT_BYTES*8-1:0]           l_smem_wr_data;

    // LOAD -> barrier
    logic                              l_add_tx_en;
    logic [31:0]                       l_add_tx_bar_id;
    logic [31:0]                       l_add_tx_bytes;
    logic                              l_sub_tx_en;
    logic [31:0]                       l_sub_tx_bar_id;
    logic [31:0]                       l_sub_tx_bytes;
    logic                              l_arrive_en;
    logic [31:0]                       l_arrive_bar_id;

    logic                              l_busy, l_done, l_accept;

    // MMA <-> smem
    logic                              m_rd_a_en;
    logic [31:0]                       m_rd_a_addr;
    logic                              m_rd_b_en;
    logic [31:0]                       m_rd_b_addr;
    logic [MMA_M*8-1:0]                s_rd_a_data;
    logic                              s_rd_a_valid;
    logic [MMA_N*8-1:0]                s_rd_b_data;
    logic                              s_rd_b_valid;
    logic                              s_load_wr_stall;
    logic                              s_rd_a_stall;
    logic                              s_rd_b_stall;

    // compute_array -> barrier (matmul arrive, was old mma -> barrier)
    logic                              m_arrive_en;
    logic [31:0]                       m_arrive_bar_id;

    logic                              m_busy, m_done;

    // STORE <-> compute_array (drain interface; replaces the old wide
    // store_rd_tile path through tmem)
    logic                              ca_drain_issue;
    logic [31:0]                       ca_drain_slot;
    logic                              ca_drain_busy;
    logic                              ca_drain_done;
    logic                              ca_drain_row_valid;
    logic [MMA_N*32-1:0]               ca_drain_row_data;
    logic [$clog2(MMA_M)-1:0]          ca_drain_row_idx;
    logic                              ca_drain_last;

    // STORE -> off-chip MC (write)
    logic                              st_wr_en;
    logic [31:0]                       st_wr_addr;
    logic [BEAT_BYTES*8-1:0]           st_wr_data;
    logic                              st_busy, st_done;

    // ------------------------------------------------------------------
    // Reset synchronizer (chip-boundary, tape-out grade).
    // `reset_in` arrives from a chip pin and is asynchronous to `clk`.
    // A 2-flop "async-assert, sync-release" synchronizer gives the
    // downstream `reset_seq` (which uses synchronous reset semantics) a
    // glitch-free `reset_in_sync` that's metastability-safe. Async-assert
    // means reset propagates immediately even if `clk` is not yet stable
    // at power-on; sync-release means the deassertion edge is filtered
    // through two flop stages so no downstream flop sees it within its
    // setup/hold window relative to the clock edge.
    //
    // ASYNC_REG="TRUE" is a synthesis hint (recognized by Vivado, DC,
    // Genus, ICC2) that keeps these two flops adjacent and forbids
    // retiming optimizations across them. Yosys ignores the attribute
    // but the flop chain survives synthesis regardless because of the
    // serial dependency.
    // ------------------------------------------------------------------
    (* ASYNC_REG = "TRUE" *) logic reset_meta;
    (* ASYNC_REG = "TRUE" *) logic reset_in_sync;
    always_ff @(posedge clk or posedge reset_in) begin
        if (reset_in) begin
            reset_meta    <= 1'b1;
            reset_in_sync <= 1'b1;
        end else begin
            reset_meta    <= 1'b0;
            reset_in_sync <= reset_meta;
        end
    end

    // ------------------------------------------------------------------
    // reset_seq — power-on reset + on-chip memory scrubber.
    // Driven by the synchronized `reset_in_sync`, not the raw pin.
    // The sequencer drives the SMEM/TMEM scrub ports and only deasserts
    // `chip_in_reset` once every bank-word has been zeroed.
    // ------------------------------------------------------------------
    localparam int SMEM_SCRUB_DEPTH = SMEM_BYTES / 32 / 4;
    logic                                       smem_scrub_en;
    logic [$clog2(SMEM_SCRUB_DEPTH)-1:0]        smem_scrub_addr_narrow;
    logic [31:0]                                smem_scrub_addr;
    logic                                       tmem_scrub_en;

    reset_seq u_reset_seq (
        .clk            (clk),
        .reset_in       (reset_in_sync),
        .chip_in_reset  (chip_in_reset),
        .smem_scrub_en  (smem_scrub_en),
        .smem_scrub_addr(smem_scrub_addr_narrow),
        .tmem_scrub_en  (tmem_scrub_en),
        .scrub_done     (scrub_done)
    );
    assign smem_scrub_addr = {{(32 - $clog2(SMEM_SCRUB_DEPTH)){1'b0}}, smem_scrub_addr_narrow};

    // ------------------------------------------------------------------
    // External-input synchronizers — qualifier sync + data passthrough.
    //
    // Both `instr_push_en/data` (from external instruction injection)
    // and `mc_rd_valid/data` (from off-chip memory controller) are
    // chip-pin inputs and assumed asynchronous to `clk`. Real silicon
    // would race if they transition near a clock edge.
    //
    // Pattern: 2-flop synchronize the qualifier (single-bit control —
    // _en / _valid), pass the data bus through directly. Contract on
    // the external driver: data must be HELD stable for at least 2
    // clk cycles after the qualifier deasserts (until the synchronized
    // version reaches the consumer). The standard handshake equivalent
    // for memory-mapped testers / DRAM controllers.
    //
    // ASYNC_REG="TRUE" hint mirrors the reset synchronizer above.
    // ------------------------------------------------------------------
    (* ASYNC_REG = "TRUE" *) logic instr_push_en_meta;
    (* ASYNC_REG = "TRUE" *) logic instr_push_en_sync;
    (* ASYNC_REG = "TRUE" *) logic mc_rd_valid_meta;
    (* ASYNC_REG = "TRUE" *) logic mc_rd_valid_sync;
    always_ff @(posedge clk or posedge reset_in) begin
        if (reset_in) begin
            instr_push_en_meta <= 1'b0;
            instr_push_en_sync <= 1'b0;
            mc_rd_valid_meta   <= 1'b0;
            mc_rd_valid_sync   <= 1'b0;
        end else begin
            instr_push_en_meta <= instr_push_en;
            instr_push_en_sync <= instr_push_en_meta;
            mc_rd_valid_meta   <= mc_rd_valid;
            mc_rd_valid_sync   <= mc_rd_valid_meta;
        end
    end

    // ------------------------------------------------------------------
    // cmdproc
    // ------------------------------------------------------------------
    cmdproc u_cmdproc (
        .clk                 (clk),
        .reset               (chip_in_reset),
        .push_en             (instr_push_en_sync),
        .push_instr          (instr_push_data),

        .load_busy           (l_busy),
        .load_done           (l_done),
        .load_accept         (l_accept),
        .mma_busy            (m_busy),
        .mma_done            (m_done),
        .store_busy          (st_busy),
        .store_done          (st_done),

        .barrier_wait_done   (br_wait_done),

        .init_en             (cp_init_en),
        .init_bar_id         (cp_init_bar_id),
        .init_count          (cp_init_count),
        .query_bar_id        (cp_query_bar_id),
        .query_expected_phase(cp_query_phase),

        .mma_start           (cp_mma_start),
        .mma_a_smem_offset   (cp_mma_a),
        .mma_b_smem_offset   (cp_mma_b),
        .mma_d_tmem_slot     (cp_mma_d),
        .mma_accum           (cp_mma_accum),
        .mma_bar_id          (cp_mma_bar),

        .load_issue_en       (cp_load_en),
        .load_gmem_ptr       (cp_load_g),
        .load_smem_ptr       (cp_load_s),
        .load_bytes_n        (cp_load_b),
        .load_bar_id         (cp_load_bar),

        .store_issue_en      (cp_store_en),
        .store_tmem_slot     (cp_store_slot),
        .store_gmem_ptr      (cp_store_g),
        .store_dtype         (cp_store_dt),

        .idle                (idle)
    );

    // ------------------------------------------------------------------
    // compute_array — Phase 7h-3: replaces the old `mma + tmem` pair.
    //
    // Wraps 1024 (MMA_M * MMA_N) mac_tmem_cell leaves with the K-loop
    // sequencer and a row-by-row drain mux. The matmul interface is
    // identical to the old mma's contract (start pulse, SMEM reads with
    // stall, busy/done, barrier arrive). The drain interface replaces the
    // old wide store_rd_tile path: STORE drives drain_issue + drain_slot
    // and receives one row of MMA_N fp32 words per cycle.
    //
    // Strides are constants here (the cmdproc doesn't emit them): A is
    // column-major (stride = MMA_M bytes per column), B is row-major
    // (stride = MMA_N bytes per row), matching the old hardcoded mma.sv.
    // ------------------------------------------------------------------
    // No parameter overrides: compute_array is consumed as a hardened
    // LEF black-box at the asap7 chip_top harden, and yosys cannot pass
    // parameter values into a LIB-only cell (it only knows the port
    // interface, not the parameter declarations). The compute_array LEF
    // is built with matching MMA_M/MMA_N/MMA_K/N_SLOTS values via the
    // sv2v -D defines, so the port widths line up without overrides.
    compute_array u_compute_array (
        .clk             (clk),
        .reset           (chip_in_reset),
        .mma_issue       (cp_mma_start),
        .mma_slot        (cp_mma_d[$clog2(TMEM_SLOTS)-1:0]),
        .mma_accum       (cp_mma_accum),
        .mma_bar_id      (cp_mma_bar),
        .issue_a_off     (cp_mma_a),
        .issue_b_off     (cp_mma_b),
        .issue_a_stride  (32'(MMA_M)),
        .issue_b_stride  (32'(MMA_N)),
        .mma_busy        (m_busy),
        .mma_done        (m_done),
        .arrive_en       (m_arrive_en),
        .arrive_bar_id   (m_arrive_bar_id),
        .rd_a_en         (m_rd_a_en),
        .rd_a_addr       (m_rd_a_addr),
        .rd_a_data       (s_rd_a_data),
        .rd_a_valid      (s_rd_a_valid),
        .rd_a_stall_in   (s_rd_a_stall),
        .rd_b_en         (m_rd_b_en),
        .rd_b_addr       (m_rd_b_addr),
        .rd_b_data       (s_rd_b_data),
        .rd_b_valid      (s_rd_b_valid),
        .rd_b_stall_in   (s_rd_b_stall),
        .drain_issue     (ca_drain_issue),
        .drain_slot      (ca_drain_slot[$clog2(TMEM_SLOTS)-1:0]),
        .drain_busy      (ca_drain_busy),
        .drain_done      (ca_drain_done),
        .drain_row_valid (ca_drain_row_valid),
        .drain_row_data  (ca_drain_row_data),
        .drain_row_idx   (ca_drain_row_idx),
        .drain_last      (ca_drain_last),
        .scrub_en        (tmem_scrub_en)
    );

    // ------------------------------------------------------------------
    // LOAD. Drives chip's mc_rd_* ports for off-chip reads; sinks the
    // response on mc_rd_data / mc_rd_valid.
    // ------------------------------------------------------------------
    load u_load (
        .clk           (clk),
        .reset         (chip_in_reset),
        .issue_en      (cp_load_en),
        .gmem_ptr      (cp_load_g),
        .smem_ptr      (cp_load_s),
        .bytes_n       (cp_load_b),
        .bar_id        (cp_load_bar),
        .gmem_rd_en    (l_gmem_rd_en),
        .gmem_rd_addr  (l_gmem_rd_addr),
        .gmem_rd_data  (mc_rd_data),
        .gmem_rd_valid (mc_rd_valid_sync),
        .smem_wr_en    (l_smem_wr_en),
        .smem_wr_addr  (l_smem_wr_addr),
        .smem_wr_data  (l_smem_wr_data),
        .smem_wr_stall_in (s_load_wr_stall),
        .add_tx_en     (l_add_tx_en),
        .add_tx_bar_id (l_add_tx_bar_id),
        .add_tx_bytes  (l_add_tx_bytes),
        .sub_tx_en     (l_sub_tx_en),
        .sub_tx_bar_id (l_sub_tx_bar_id),
        .sub_tx_bytes  (l_sub_tx_bytes),
        .arrive_en     (l_arrive_en),
        .arrive_bar_id (l_arrive_bar_id),
        .busy          (l_busy),
        .done          (l_done),
        .accept        (l_accept)
    );

    // Memory-controller read port: combinational pass-through from LOAD.
    assign mc_rd_en   = l_gmem_rd_en;
    assign mc_rd_addr = l_gmem_rd_addr;

    // ------------------------------------------------------------------
    // STORE. Drives chip's mc_wr_* ports for off-chip writes. Drains
    // compute_array's accumulator row-by-row over the drain-stream
    // interface (Phase 7h-3).
    // ------------------------------------------------------------------
    store #(
        .MMA_M     (MMA_M),
        .MMA_N     (MMA_N),
        .BEAT_BYTES(BEAT_BYTES)
    ) u_store (
        .clk             (clk),
        .reset           (chip_in_reset),
        .issue_en        (cp_store_en),
        .tmem_slot       (cp_store_slot),
        .gmem_ptr        (cp_store_g),
        .dtype           (cp_store_dt),
        .drain_issue     (ca_drain_issue),
        .drain_slot      (ca_drain_slot),
        .drain_row_valid (ca_drain_row_valid),
        .drain_row_data  (ca_drain_row_data),
        .drain_row_idx   (ca_drain_row_idx),
        .drain_last      (ca_drain_last),
        .drain_done      (ca_drain_done),
        .wr_en           (st_wr_en),
        .wr_addr         (st_wr_addr),
        .wr_data         (st_wr_data),
        .busy            (st_busy),
        .done            (st_done)
    );

    // Memory-controller write port: combinational pass-through from STORE.
    assign mc_wr_en   = st_wr_en;
    assign mc_wr_addr = st_wr_addr;
    assign mc_wr_data = st_wr_data;

    // ------------------------------------------------------------------
    // SMEM (32 banks of sram_1rw).
    // ------------------------------------------------------------------
    smem #(
        .MMA_M     (MMA_M),
        .MMA_N     (MMA_N),
        .SMEM_BYTES(SMEM_BYTES),
        .BEAT_BYTES(BEAT_BYTES)
    ) u_smem (
        .clk        (clk),
        .reset      (chip_in_reset),
        .wr_en      (l_smem_wr_en),
        .wr_addr    (l_smem_wr_addr),
        .wr_data    (l_smem_wr_data),
        .rd_a_en    (m_rd_a_en),
        .rd_a_addr  (m_rd_a_addr),
        .rd_b_en    (m_rd_b_en),
        .rd_b_addr  (m_rd_b_addr),
        .scrub_en   (smem_scrub_en),
        .scrub_addr (smem_scrub_addr),
        .rd_a_data  (s_rd_a_data),
        .rd_a_valid (s_rd_a_valid),
        .rd_b_data  (s_rd_b_data),
        .rd_b_valid (s_rd_b_valid),
        .load_wr_stall_out  (s_load_wr_stall),
        .mma_rd_a_stall_out (s_rd_a_stall),
        .mma_rd_b_stall_out (s_rd_b_stall)
    );

    // ------------------------------------------------------------------
    // (TMEM removed in Phase 7h-3 — its per-(i, j) micro-storage is now
    // owned by mac_tmem_cell leaves inside u_compute_array. The
    // tmem_scrub_en pulse from u_reset_seq drives compute_array.scrub_en
    // directly.)
    //
    // ------------------------------------------------------------------
    // Barrier.
    // ------------------------------------------------------------------
    barrier u_barrier (
        .clk                  (clk),
        .reset                (chip_in_reset),
        .init_en              (cp_init_en),
        .init_bar_id          (cp_init_bar_id),
        .init_count           (cp_init_count),
        .arrive_en_a          (l_arrive_en),
        .arrive_bar_id_a      (l_arrive_bar_id),
        .arrive_en_b          (m_arrive_en),
        .arrive_bar_id_b      (m_arrive_bar_id),
        .add_tx_en            (l_add_tx_en),
        .add_tx_bar_id        (l_add_tx_bar_id),
        .add_tx_bytes         (l_add_tx_bytes),
        .sub_tx_en            (l_sub_tx_en),
        .sub_tx_bar_id        (l_sub_tx_bar_id),
        .sub_tx_bytes         (l_sub_tx_bytes),
        .query_bar_id         (cp_query_bar_id),
        .query_expected_phase (cp_query_phase),
        .wait_done            (br_wait_done)
    );

    // ------------------------------------------------------------------
    // Observability passthroughs.
    // ------------------------------------------------------------------
    assign init_en              = cp_init_en;
    assign init_bar_id          = cp_init_bar_id;
    assign init_count           = cp_init_count;
    assign query_bar_id         = cp_query_bar_id;
    assign query_expected_phase = cp_query_phase;

    assign mma_start            = cp_mma_start;
    assign mma_a_smem_offset    = cp_mma_a;
    assign mma_b_smem_offset    = cp_mma_b;
    assign mma_d_tmem_slot      = cp_mma_d;
    assign mma_accum            = cp_mma_accum;
    assign mma_bar_id           = cp_mma_bar;

    assign load_issue_en        = cp_load_en;
    assign load_gmem_ptr        = cp_load_g;
    assign load_smem_ptr        = cp_load_s;
    assign load_bytes_n         = cp_load_b;
    assign load_bar_id          = cp_load_bar;

    assign store_issue_en       = cp_store_en;
    assign store_tmem_slot      = cp_store_slot;
    assign store_gmem_ptr       = cp_store_g;
    assign store_dtype          = cp_store_dt;

    assign load_busy            = l_busy;
    assign load_done            = l_done;
    assign load_accept          = l_accept;
    assign mma_busy             = m_busy;
    assign mma_done             = m_done;
    assign store_busy           = st_busy;
    assign store_done           = st_done;

    assign sys_idle             = idle && !l_busy && !m_busy && !st_busy;

endmodule
