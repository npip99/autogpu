// cmdproc_tb_top.sv — TB wrapper instantiating cmdproc + the full Phase 4
// pipeline: mma + load + store + barrier + smem + tmem + gmem.
//
// The cocotb testbench pushes instructions via (push_en, push_instr), watches
// the registered drives from cmdproc to each engine for the directed test,
// and verifies the final gmem contents for the end-to-end matmul.
//
// Wiring summary (mirrors pymodel/sim.py):
//   cmdproc.init_*            -> barrier
//   cmdproc.query_*           -> barrier (combinational), barrier.wait_done -> cmdproc
//   cmdproc.mma_*             -> mma.start, operands
//   cmdproc.load_*            -> load.issue port
//   cmdproc.store_*           -> store.issue port
//   load.{busy,done,accept}   -> cmdproc
//   mma.{busy,done}           -> cmdproc
//   store.{busy,done}         -> cmdproc
//   load -> gmem (rd), smem (LOAD_WR), barrier (add_tx/sub_tx/arrive_a)
//   mma  -> smem (MMA_RD_A/B), tmem (MMA_PORT), barrier (arrive_b)
//   store -> tmem (STORE_RD), gmem (write)
//   barrier observable state exposed as packed arrays.
//
// Backdoor handles the TB uses:
//   u_gmem.mem[..]  — preload A/B, dump C
//   u_smem.mem[..]  — direct inspection if needed
//   u_tmem.slots[..][..][..] — direct inspection if needed

module cmdproc_tb_top #(
    parameter int MMA_M            = 32,
    parameter int MMA_N            = 32,
    parameter int MMA_K            = 32,
    parameter int TMEM_SLOTS       = 4,
    parameter int SMEM_BYTES       = 16384,
    parameter int GMEM_BYTES       = 16777216,
    parameter int BEAT_BYTES       = 16,
    parameter int NUM_BARRIERS     = 8,
    parameter int INSTR_FIFO_DEPTH = 256
) (
    input  logic                          clk,
    input  logic                          reset,

    // Instruction push (TB side).
    input  logic                          push_en,
    input  logic [255:0]                  push_instr,

    // Cmdproc-observable drives (so the directed test can sample them).
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

    // Engine status (so the TB can wait on done pulses).
    output logic                          load_busy,
    output logic                          load_done,
    output logic                          load_accept,
    output logic                          mma_busy,
    output logic                          mma_done,
    output logic                          store_busy,
    output logic                          store_done,

    // Cmdproc-local idle (FIFO empty + state==IDLE).
    output logic                          idle,

    // Aggregate idle: cmdproc idle AND all engines idle. Useful for the
    // end-to-end test's wait condition.
    output logic                          sys_idle,

    // Barrier observable state.
    output logic [NUM_BARRIERS*16-1:0]    bars_pending,
    output logic [NUM_BARRIERS*16-1:0]    bars_expected,
    output logic [NUM_BARRIERS*32-1:0]    bars_tx_pending,
    output logic [NUM_BARRIERS-1:0]       bars_phase
);

    // ------------------------------------------------------------------
    // Internal wires.
    // ------------------------------------------------------------------
    // cmdproc -> barrier (INIT)
    logic                              cp_init_en;
    logic [31:0]                       cp_init_bar_id;
    logic [15:0]                       cp_init_count;
    logic [31:0]                       cp_query_bar_id;
    logic                              cp_query_phase;
    // barrier -> cmdproc
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

    // LOAD <-> gmem
    logic                              l_gmem_rd_en;
    logic [31:0]                       l_gmem_rd_addr;
    logic [BEAT_BYTES*8-1:0]           g_rd_data;
    logic                              g_rd_valid;
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
    // LOAD status
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

    // smem combinational stall outputs (back-pressure for LOAD / MMA).
    logic                              s_load_wr_stall;
    logic                              s_rd_a_stall;
    logic                              s_rd_b_stall;
    // MMA <-> tmem
    logic [1:0]                        m_tmem_op;
    logic [31:0]                       m_tmem_slot;
    logic [MMA_M*MMA_N*32-1:0]         m_tmem_write;
    logic [MMA_M*MMA_N*32-1:0]         t_mma_rd_tile;
    logic                              t_mma_rd_valid;
    // MMA -> barrier
    logic                              m_arrive_en;
    logic [31:0]                       m_arrive_bar_id;
    // MMA status
    logic                              m_busy, m_done;

    // STORE <-> tmem
    logic                              st_rd_en;
    logic [31:0]                       st_rd_slot;
    logic [MMA_M*MMA_N*32-1:0]         t_store_rd_tile;
    logic                              t_store_rd_valid;
    // STORE -> gmem
    logic                              st_wr_en;
    logic [31:0]                       st_wr_addr;
    logic [BEAT_BYTES*8-1:0]           st_wr_data;
    // STORE status
    logic                              st_busy, st_done;

    // ------------------------------------------------------------------
    // cmdproc
    // ------------------------------------------------------------------
    cmdproc #(
        .INSTR_FIFO_DEPTH(INSTR_FIFO_DEPTH),
        .NUM_BARRIERS    (NUM_BARRIERS)
    ) u_cmdproc (
        .clk                 (clk),
        .reset               (reset),
        .push_en             (push_en),
        .push_instr          (push_instr),

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
    // MMA
    // ------------------------------------------------------------------
    mma #(
        .MMA_M(MMA_M),
        .MMA_N(MMA_N),
        .MMA_K(MMA_K)
    ) u_mma (
        .clk           (clk),
        .reset         (reset),
        .start         (cp_mma_start),
        .a_smem_offset (cp_mma_a),
        .b_smem_offset (cp_mma_b),
        .d_tmem_slot   (cp_mma_d),
        .accum         (cp_mma_accum),
        .bar_id        (cp_mma_bar),
        .rd_a_data     (s_rd_a_data),
        .rd_a_valid    (s_rd_a_valid),
        .rd_b_data     (s_rd_b_data),
        .rd_b_valid    (s_rd_b_valid),
        .rd_a_stall_in (s_rd_a_stall),
        .rd_b_stall_in (s_rd_b_stall),
        .mma_rd_tile   (t_mma_rd_tile),
        .mma_rd_valid  (t_mma_rd_valid),
        .rd_a_en       (m_rd_a_en),
        .rd_a_addr     (m_rd_a_addr),
        .rd_b_en       (m_rd_b_en),
        .rd_b_addr     (m_rd_b_addr),
        .mma_op        (m_tmem_op),
        .mma_slot      (m_tmem_slot),
        .mma_write_tile(m_tmem_write),
        .arrive_en     (m_arrive_en),
        .arrive_bar_id (m_arrive_bar_id),
        .busy          (m_busy),
        .done          (m_done)
    );

    // ------------------------------------------------------------------
    // LOAD
    // ------------------------------------------------------------------
    load #(
        .BEAT_BYTES      (BEAT_BYTES),
        .NUM_BARRIERS    (NUM_BARRIERS),
        .INSTR_FIFO_DEPTH(INSTR_FIFO_DEPTH)
    ) u_load (
        .clk           (clk),
        .reset         (reset),
        .issue_en      (cp_load_en),
        .gmem_ptr      (cp_load_g),
        .smem_ptr      (cp_load_s),
        .bytes_n       (cp_load_b),
        .bar_id        (cp_load_bar),
        .gmem_rd_en    (l_gmem_rd_en),
        .gmem_rd_addr  (l_gmem_rd_addr),
        .gmem_rd_data  (g_rd_data),
        .gmem_rd_valid (g_rd_valid),
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

    // ------------------------------------------------------------------
    // STORE
    // ------------------------------------------------------------------
    store #(
        .MMA_M(MMA_M),
        .MMA_N(MMA_N),
        .BEAT_BYTES(BEAT_BYTES)
    ) u_store (
        .clk           (clk),
        .reset         (reset),
        .issue_en      (cp_store_en),
        .tmem_slot     (cp_store_slot),
        .gmem_ptr      (cp_store_g),
        .dtype         (cp_store_dt),
        .store_rd_tile (t_store_rd_tile),
        .store_rd_valid(t_store_rd_valid),
        .store_rd_en   (st_rd_en),
        .store_rd_slot (st_rd_slot),
        .wr_en         (st_wr_en),
        .wr_addr       (st_wr_addr),
        .wr_data       (st_wr_data),
        .busy          (st_busy),
        .done          (st_done)
    );

    // ------------------------------------------------------------------
    // GMEM. Two clients: LOAD (read), STORE (write). LOAD has the read port;
    // STORE has the write port. (TB backdoors through u_gmem.mem.)
    // ------------------------------------------------------------------
    gmem #(
        .GMEM_BYTES(GMEM_BYTES),
        .BEAT_BYTES(BEAT_BYTES)
    ) u_gmem (
        .clk      (clk),
        .reset    (reset),
        .rd_en    (l_gmem_rd_en),
        .rd_addr  (l_gmem_rd_addr),
        .wr_en    (st_wr_en),
        .wr_addr  (st_wr_addr),
        .wr_data  (st_wr_data),
        .rd_data  (g_rd_data),
        .rd_valid (g_rd_valid)
    );

    // ------------------------------------------------------------------
    // SMEM. Three clients: LOAD (write), MMA (two read ports).
    // ------------------------------------------------------------------
    smem #(
        .SMEM_BYTES(SMEM_BYTES),
        .BEAT_BYTES(BEAT_BYTES),
        .MMA_M     (MMA_M),
        .MMA_N     (MMA_N)
    ) u_smem (
        .clk        (clk),
        .reset      (reset),
        .wr_en      (l_smem_wr_en),
        .wr_addr    (l_smem_wr_addr),
        .wr_data    (l_smem_wr_data),
        .rd_a_en    (m_rd_a_en),
        .rd_a_addr  (m_rd_a_addr),
        .rd_b_en    (m_rd_b_en),
        .rd_b_addr  (m_rd_b_addr),
        .rd_a_data  (s_rd_a_data),
        .rd_a_valid (s_rd_a_valid),
        .rd_b_data  (s_rd_b_data),
        .rd_b_valid (s_rd_b_valid),
        .load_wr_stall_out  (s_load_wr_stall),
        .mma_rd_a_stall_out (s_rd_a_stall),
        .mma_rd_b_stall_out (s_rd_b_stall)
    );

    // ------------------------------------------------------------------
    // TMEM. MMA drives MMA_PORT; STORE drives STORE_RD.
    // ------------------------------------------------------------------
    tmem #(
        .TMEM_SLOTS(TMEM_SLOTS),
        .MMA_M     (MMA_M),
        .MMA_N     (MMA_N)
    ) u_tmem (
        .clk           (clk),
        .reset         (reset),
        .mma_op        (m_tmem_op),
        .mma_slot      (m_tmem_slot),
        .mma_write_tile(m_tmem_write),
        .store_rd_en   (st_rd_en),
        .store_rd_slot (st_rd_slot),
        .mma_rd_tile   (t_mma_rd_tile),
        .mma_rd_valid  (t_mma_rd_valid),
        .store_rd_tile (t_store_rd_tile),
        .store_rd_valid(t_store_rd_valid)
    );

    // ------------------------------------------------------------------
    // Barrier. INIT from cmdproc; arrive_a = LOAD, arrive_b = MMA;
    // add_tx/sub_tx = LOAD; wait_query from cmdproc.
    // ------------------------------------------------------------------
    barrier #(
        .NUM_BARRIERS(NUM_BARRIERS)
    ) u_barrier (
        .clk                  (clk),
        .reset                (reset),
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
        .wait_done            (br_wait_done),
        .bars_pending         (bars_pending),
        .bars_expected        (bars_expected),
        .bars_tx_pending      (bars_tx_pending),
        .bars_phase           (bars_phase)
    );

    // ------------------------------------------------------------------
    // Surface internal signals as outputs.
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
