// chip_tb_top.sv — NON-SYNTHESIZABLE testbench wrapper for chip_top.
//
// Drops the synthesizable die (`chip_top`) onto a board with a behavioral
// off-chip DRAM model (`gmem`) and exposes a flat port surface to the
// cocotb harness. Translates port names where helpful (the chip's
// `instr_push_*` is conventionally `push_*` in TB code; we keep both).
//
// HIERARCHY (cocotb backdoor handles):
//   chip_tb_top
//   ├── u_chip       — synthesizable chip_top
//   │    ├── u_smem      (32 sram_1rw banks; access via .bank_mem[b][w]
//   │    │                shadow, or .gen_banks[b].u_sram.mem[w] direct)
//   │    ├── u_compute_array (1024 mac_tmem_cell leaves; per-cell storage
//   │    │                accessible via
//   │    │                .gen_row[i].gen_col[j].u_cell.storage[slot])
//   │    └── ... other on-chip submodules ...
//   └── u_gmem       — behavioral DRAM model (.mem[byte_addr])

module chip_tb_top #(
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
    input  logic                          reset,           // pin reset_in

    // Instruction push (TB side).
    input  logic                          push_en,
    input  logic [255:0]                  push_instr,

    // Cmdproc-observable drives.
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

    output logic                          load_busy,
    output logic                          load_done,
    output logic                          load_accept,
    output logic                          mma_busy,
    output logic                          mma_done,
    output logic                          store_busy,
    output logic                          store_done,

    output logic                          idle,
    output logic                          sys_idle,

    output logic                          chip_in_reset,
    output logic                          scrub_done
);

    // Off-chip MC bus — chip_top <-> behavioral gmem.
    logic                       mc_wr_en;
    logic [31:0]                mc_wr_addr;
    logic [BEAT_BYTES*8-1:0]    mc_wr_data;
    logic                       mc_rd_en;
    logic [31:0]                mc_rd_addr;
    logic [BEAT_BYTES*8-1:0]    mc_rd_data;
    logic                       mc_rd_valid;

    chip_top #(
        .MMA_M           (MMA_M),
        .MMA_N           (MMA_N),
        .MMA_K           (MMA_K),
        .TMEM_SLOTS      (TMEM_SLOTS),
        .SMEM_BYTES      (SMEM_BYTES),
        .BEAT_BYTES      (BEAT_BYTES),
        .NUM_BARRIERS    (NUM_BARRIERS),
        .INSTR_FIFO_DEPTH(INSTR_FIFO_DEPTH)
    ) u_chip (
        .clk                 (clk),
        .reset_in            (reset),

        .instr_push_en       (push_en),
        .instr_push_data     (push_instr),

        .mc_wr_en            (mc_wr_en),
        .mc_wr_addr          (mc_wr_addr),
        .mc_wr_data          (mc_wr_data),
        .mc_rd_en            (mc_rd_en),
        .mc_rd_addr          (mc_rd_addr),
        .mc_rd_data          (mc_rd_data),
        .mc_rd_valid         (mc_rd_valid),

        .chip_in_reset       (chip_in_reset),
        .sys_idle            (sys_idle),
        .scrub_done          (scrub_done),

        .init_en             (init_en),
        .init_bar_id         (init_bar_id),
        .init_count          (init_count),
        .query_bar_id        (query_bar_id),
        .query_expected_phase(query_expected_phase),

        .mma_start           (mma_start),
        .mma_a_smem_offset   (mma_a_smem_offset),
        .mma_b_smem_offset   (mma_b_smem_offset),
        .mma_d_tmem_slot     (mma_d_tmem_slot),
        .mma_accum           (mma_accum),
        .mma_bar_id          (mma_bar_id),

        .load_issue_en       (load_issue_en),
        .load_gmem_ptr       (load_gmem_ptr),
        .load_smem_ptr       (load_smem_ptr),
        .load_bytes_n        (load_bytes_n),
        .load_bar_id         (load_bar_id),

        .store_issue_en      (store_issue_en),
        .store_tmem_slot     (store_tmem_slot),
        .store_gmem_ptr      (store_gmem_ptr),
        .store_dtype         (store_dtype),

        .load_busy           (load_busy),
        .load_done           (load_done),
        .load_accept         (load_accept),
        .mma_busy            (mma_busy),
        .mma_done            (mma_done),
        .store_busy          (store_busy),
        .store_done          (store_done),
        .idle                (idle)
    );

    // Off-chip behavioral DRAM. TB backdoors data through u_gmem.mem[]
    // exactly as in the pre-7f cmdproc_tb_top.
    gmem #(
        .GMEM_BYTES(GMEM_BYTES),
        .BEAT_BYTES(BEAT_BYTES)
    ) u_gmem (
        .clk      (clk),
        .reset    (reset),
        .rd_en    (mc_rd_en),
        .rd_addr  (mc_rd_addr),
        .wr_en    (mc_wr_en),
        .wr_addr  (mc_wr_addr),
        .wr_data  (mc_wr_data),
        .rd_data  (mc_rd_data),
        .rd_valid (mc_rd_valid)
    );

endmodule
