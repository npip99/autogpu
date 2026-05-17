// load_tb_top.sv — testbench wrapper that instantiates load + gmem + smem + barrier
// and wires them as they will be in the full system.
//
// The cocotb testbench drives:
//   - clk, reset
//   - issue_en, gmem_ptr, smem_ptr, bytes_n, bar_id (LOAD command issue)
//   - barrier init port (bar_init_en, bar_init_bar_id, bar_init_count)
//   - smem MMA read ports left tied off (we only need LOAD_WR here)
//   - gmem write port can be tied off (backdoor preload via cocotb force is
//     used for the seed pattern; here, however, we expose a synchronous gmem
//     write port so the TB can preload deterministically via cycles).
//
// The TB observes:
//   - load.busy, done, accept, add_tx_*, sub_tx_*, arrive_*
//   - bars_pending, bars_expected, bars_tx_pending, bars_phase
//   - smem contents — exposed via a back-door bus that taps into smem.mem
//
// Memory contents preload: we expose `gmem_be_wr_en/addr/data` and similar
// for smem, but they aren't necessary. Instead, the TB uses cocotb's
// hierarchical access (dut.u_gmem.mem[k] = ...) to backdoor-load gmem; that
// reads more naturally than driving wr_en for ~thousands of bytes.
//
// Wiring summary:
//   load.gmem_rd_en/addr   --> gmem.rd_en/addr
//   gmem.rd_data/valid     --> load.gmem_rd_data/valid
//   load.smem_wr_en/addr/data --> smem.wr_en/wr_addr/wr_data (LOAD_WR port)
//   load.add_tx_/sub_tx_/arrive_ --> barrier.add_tx_*/sub_tx_*/arrive_en_a (LOAD channel)

module load_tb_top #(
    parameter int BEAT_BYTES       = 16,
    parameter int NUM_BARRIERS     = 8,
    parameter int INSTR_FIFO_DEPTH = 256,
    parameter int GMEM_BYTES       = 16777216,
    parameter int SMEM_BYTES       = 16384,
    parameter int MMA_M            = 32,
    parameter int MMA_N            = 32
) (
    input  logic                       clk,
    input  logic                       reset,

    // LOAD command issue.
    input  logic                       issue_en,
    input  logic [31:0]                gmem_ptr,
    input  logic [31:0]                smem_ptr,
    input  logic [31:0]                bytes_n,
    input  logic [31:0]                bar_id,

    // Barrier INIT (used by the TB to set up barriers).
    input  logic                       bar_init_en,
    input  logic [31:0]                bar_init_bar_id,
    input  logic [15:0]                bar_init_count,

    // Observable status (LOAD engine).
    output logic                       busy,
    output logic                       done,
    output logic                       accept,
    output logic                       add_tx_en,
    output logic [31:0]                add_tx_bar_id,
    output logic [31:0]                add_tx_bytes,
    output logic                       sub_tx_en,
    output logic [31:0]                sub_tx_bar_id,
    output logic [31:0]                sub_tx_bytes,
    output logic                       arrive_en,
    output logic [31:0]                arrive_bar_id,

    // Observable barrier state (packed per-bar arrays).
    output logic [NUM_BARRIERS*16-1:0] bars_pending,
    output logic [NUM_BARRIERS*16-1:0] bars_expected,
    output logic [NUM_BARRIERS*32-1:0] bars_tx_pending,
    output logic [NUM_BARRIERS-1:0]    bars_phase
);

    // -----------------------------------------------------------------
    // Wires between modules.
    // -----------------------------------------------------------------

    // load <-> gmem
    logic                       l_gmem_rd_en;
    logic [31:0]                l_gmem_rd_addr;
    logic [BEAT_BYTES*8-1:0]    g_rd_data;
    logic                       g_rd_valid;

    // load -> smem (LOAD_WR)
    logic                       l_smem_wr_en;
    logic [31:0]                l_smem_wr_addr;
    logic [BEAT_BYTES*8-1:0]    l_smem_wr_data;
    // smem -> load (combinational stall; always 0 in practice given LOAD's
    // top priority — included for wiring parity).
    logic                       smem_load_wr_stall;
    logic                       smem_rd_a_stall_unused;
    logic                       smem_rd_b_stall_unused;
    /* verilator lint_off UNUSEDSIGNAL */
    logic                       smem_unused_sink;
    assign smem_unused_sink = smem_rd_a_stall_unused | smem_rd_b_stall_unused;
    /* verilator lint_on UNUSEDSIGNAL */

    // load -> barrier
    logic                       l_add_tx_en;
    logic [31:0]                l_add_tx_bar_id;
    logic [31:0]                l_add_tx_bytes;
    logic                       l_sub_tx_en;
    logic [31:0]                l_sub_tx_bar_id;
    logic [31:0]                l_sub_tx_bytes;
    logic                       l_arrive_en;
    logic [31:0]                l_arrive_bar_id;

    // -----------------------------------------------------------------
    // load engine
    // -----------------------------------------------------------------
    load #(
        .BEAT_BYTES      (BEAT_BYTES),
        .NUM_BARRIERS    (NUM_BARRIERS),
        .INSTR_FIFO_DEPTH(INSTR_FIFO_DEPTH)
    ) u_load (
        .clk           (clk),
        .reset         (reset),
        .issue_en      (issue_en),
        .gmem_ptr      (gmem_ptr),
        .smem_ptr      (smem_ptr),
        .bytes_n       (bytes_n),
        .bar_id        (bar_id),
        .gmem_rd_en    (l_gmem_rd_en),
        .gmem_rd_addr  (l_gmem_rd_addr),
        .gmem_rd_data  (g_rd_data),
        .gmem_rd_valid (g_rd_valid),
        .smem_wr_en    (l_smem_wr_en),
        .smem_wr_addr  (l_smem_wr_addr),
        .smem_wr_data  (l_smem_wr_data),
        .smem_wr_stall_in (smem_load_wr_stall),
        .add_tx_en     (l_add_tx_en),
        .add_tx_bar_id (l_add_tx_bar_id),
        .add_tx_bytes  (l_add_tx_bytes),
        .sub_tx_en     (l_sub_tx_en),
        .sub_tx_bar_id (l_sub_tx_bar_id),
        .sub_tx_bytes  (l_sub_tx_bytes),
        .arrive_en     (l_arrive_en),
        .arrive_bar_id (l_arrive_bar_id),
        .busy          (busy),
        .done          (done),
        .accept        (accept)
    );

    // -----------------------------------------------------------------
    // gmem
    // -----------------------------------------------------------------
    gmem #(
        .GMEM_BYTES (GMEM_BYTES),
        .BEAT_BYTES (BEAT_BYTES)
    ) u_gmem (
        .clk      (clk),
        .reset    (reset),
        .rd_en    (l_gmem_rd_en),
        .rd_addr  (l_gmem_rd_addr),
        .wr_en    (1'b0),
        .wr_addr  (32'd0),
        .wr_data  ('0),
        .rd_data  (g_rd_data),
        .rd_valid (g_rd_valid)
    );

    // -----------------------------------------------------------------
    // smem (LOAD_WR driven by load; MMA read ports tied off).
    // -----------------------------------------------------------------
    smem #(
        .SMEM_BYTES (SMEM_BYTES),
        .BEAT_BYTES (BEAT_BYTES),
        .MMA_M      (MMA_M),
        .MMA_N      (MMA_N)
    ) u_smem (
        .clk        (clk),
        .reset      (reset),
        .wr_en      (l_smem_wr_en),
        .wr_addr    (l_smem_wr_addr),
        .wr_data    (l_smem_wr_data),
        .rd_a_en    (1'b0),
        .rd_a_addr  (32'd0),
        .rd_b_en    (1'b0),
        .rd_b_addr  (32'd0),
        .rd_a_data  (),
        .rd_a_valid (),
        .rd_b_data  (),
        .rd_b_valid (),
        .load_wr_stall_out  (smem_load_wr_stall),
        .mma_rd_a_stall_out (smem_rd_a_stall_unused),
        .mma_rd_b_stall_out (smem_rd_b_stall_unused)
    );

    // -----------------------------------------------------------------
    // barrier (LOAD drives add_tx/sub_tx/arrive_a; MMA channel tied off).
    // -----------------------------------------------------------------
    barrier #(
        .NUM_BARRIERS (NUM_BARRIERS)
    ) u_barrier (
        .clk                  (clk),
        .reset                (reset),
        .init_en              (bar_init_en),
        .init_bar_id          (bar_init_bar_id),
        .init_count           (bar_init_count),
        .arrive_en_a          (l_arrive_en),
        .arrive_bar_id_a      (l_arrive_bar_id),
        .arrive_en_b          (1'b0),
        .arrive_bar_id_b      (32'd0),
        .add_tx_en            (l_add_tx_en),
        .add_tx_bar_id        (l_add_tx_bar_id),
        .add_tx_bytes         (l_add_tx_bytes),
        .sub_tx_en            (l_sub_tx_en),
        .sub_tx_bar_id        (l_sub_tx_bar_id),
        .sub_tx_bytes         (l_sub_tx_bytes),
        .query_bar_id         (32'd0),
        .query_expected_phase (1'b0),
        .wait_done            (),
        .bars_pending         (bars_pending),
        .bars_expected        (bars_expected),
        .bars_tx_pending      (bars_tx_pending),
        .bars_phase           (bars_phase)
    );

    // Surface the load's barrier-drive signals so the TB can sample them
    // for direct compare against pymodel.load.Load attributes.
    assign add_tx_en     = l_add_tx_en;
    assign add_tx_bar_id = l_add_tx_bar_id;
    assign add_tx_bytes  = l_add_tx_bytes;
    assign sub_tx_en     = l_sub_tx_en;
    assign sub_tx_bar_id = l_sub_tx_bar_id;
    assign sub_tx_bytes  = l_sub_tx_bytes;
    assign arrive_en     = l_arrive_en;
    assign arrive_bar_id = l_arrive_bar_id;

endmodule
