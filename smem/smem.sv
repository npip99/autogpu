// smem.sv — banked on-chip scratchpad.
//
// 32-bank structurally-banked memory, modeled to mirror a B200-style SMEM.
// Each bank is one 4-byte (32-bit) dword wide. The dword-index decode is
// plain round-robin: `bank_of(addr) = (addr / 4) % 32 = addr[6:2]`.
//
// As of Phase 7f, each bank is a `sram_1rw` instance (process-portable
// behavioral 1RW SRAM). Real silicon will substitute a vendor SRAM macro
// via `tech/<process>/sram_1rw.sv`. The wrapper here owns the per-port
// arbitration, the same-cycle write-forwarding mux on the drain path, and
// the address decode.
//
//   wr  addr     -> 4 consecutive banks  (BEAT_BYTES = 16, 4 dwords)
//   rd_a addr    -> 8 consecutive banks  (MMA_M     = 32, 8 dwords)
//   rd_b addr    -> 8 consecutive banks  (MMA_N     = 32, 8 dwords)
//
// CONFLICT and STALL PROTOCOL
// ===========================
//   Priority (fixed): LOAD_WR > MMA_RD_A > MMA_RD_B. Lower-priority losers
//   STALL: their request is not honored this cycle and they must re-issue
//   next. Bank overlap is detected via the aligned-address trick — two
//   ports' bank ranges overlap iff their 8-bank-group indices `addr[6:5]`
//   agree (LOAD_WR sits within one group; RD_A/RD_B each occupy a group).
//
//     load_wr_stall_out   = 1'b0
//     mma_rd_a_stall_out  = rd_a_en && wr_en && (group(rd_a_addr) == group(wr_addr))
//     mma_rd_b_stall_out  = rd_b_en && (
//                              (wr_en   && group(rd_b_addr) == group(wr_addr))
//                           || (rd_a_en && group(rd_b_addr) == group(rd_a_addr))
//                         )
//
//   Stall outputs are COMBINATIONAL on the cycle's inputs. Consumers
//   sample stall the cycle the memory module sees the request, then
//   choose not to advance internal counters on a stall.
//
// BANK PORT PROTOCOL (sram_1rw constraint: at most ONE access per cycle)
// =====================================================================
//   For each bank b, at most one of the three port classes may drive it
//   on any given cycle. The stall logic above guarantees this. Per cycle:
//     - if scrub_en  : en=1, we=1, wdata=0, addr=scrub_addr.
//     - else if wr_en in bank's range : en=1, we=1, write the dword.
//     - else if rd_a_en captured for bank : en=1, we=0, addr=rd_a word.
//     - else if rd_b_en captured for bank : en=1, we=0, addr=rd_b word.
//     - else: en=0.
//   Bank rdata is registered (1-cycle latency, native sram_1rw behavior).
//
// READ PATH — TWO-EDGE LATENCY (UNCHANGED FROM PRE-7f)
// =====================================================
//   Edge T  : rd_a_en sampled. Drive bank's en=1, we=0, addr=word.
//             Capture rd_a_pending_addr (used by the drain forwarding mux).
//             Bank flops rdata <= mem[word]; visible after edge T.
//   Edge T+1: drain combinationally gathers all 8 banks' rdata into
//             `rd_a_beat`, applies byte-level forwarding from current-cycle
//             wr_data if overlap, then `rd_a_data <= rd_a_beat`. Visible
//             after edge T+1.
//   Total latency: 2 edges, matching the pre-refactor behavior. The
//   pymodel `pymodel/smem.py` describes the same observable contract.
//
// WRITE FORWARDING (drain path)
// =============================
//   Per spec: LOAD_WR commits BEFORE the drain of a previously-captured
//   pending MMA_RD_*. Implemented as a byte-by-byte forwarding mux on the
//   drain path. For each byte position, choose the wr_data byte if the
//   address sits inside [wr_addr, wr_addr+BEAT_BYTES); else read from the
//   bank's rdata. Real SRAM macros do not forward, so this mux is wrapper
//   logic — sram_1rw itself does not implement forwarding.
//
// BACKDOOR (cocotb)
// =================
//   Per-bank storage lives inside `gen_banks[b].u_sram.mem[w]` (the
//   sram_1rw instance). A read-only `mem[]` byte view is exposed at the
//   smem.sv level for cocotb hierarchical reads
//   (dut.u_smem.mem[byte_idx].value). The view samples bank rdata via
//   $past-style mirroring is not synthesizable; instead the wrapper
//   maintains a parallel `bank_mem[NUM_BANKS][NUM_WORDS_PER_BANK]`
//   shadow that tracks every write into the SRAMs. This shadow is for
//   sim-time observability only and is `verilator public`.
//
// RESET / SCRUB
// =============
//   reset is dominant: clears pending state + registered outputs. Bank
//   contents preserved (matches gmem.sv / tmem.sv semantics). The
//   post-power-on zeroing of bank contents is performed by the SCRUB
//   PORT (driven by reset_seq) — when scrub_en=1, ALL 32 banks are
//   written to 0 at scrub_addr (the per-bank word index). scrub_en is
//   mutually exclusive with wr_en / rd_a_en / rd_b_en (chip_in_reset
//   gates those off upstream).

module smem #(
    parameter int SMEM_BYTES = 16384,
    parameter int BEAT_BYTES = 16,
    parameter int MMA_M      = 32,
    parameter int MMA_N      = 32
) (
    input  logic                          clk,
    input  logic                          reset,

    // LOAD_WR
    input  logic                          wr_en,
    input  logic [31:0]                   wr_addr,
    input  logic [BEAT_BYTES*8-1:0]       wr_data,

    // MMA_RD_A
    input  logic                          rd_a_en,
    input  logic [31:0]                   rd_a_addr,

    // MMA_RD_B
    input  logic                          rd_b_en,
    input  logic [31:0]                   rd_b_addr,

    // SCRUB (reset-only; driven by reset_seq). The wire is sized for
    // parameter-uniformity at chip_top; internally we use only the low
    // WORD_BITS bits to index into a bank.
    input  logic                          scrub_en,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [31:0]                   scrub_addr,
    /* verilator lint_on UNUSEDSIGNAL */

    // Outputs (registered)
    output logic [MMA_M*8-1:0]            rd_a_data,
    output logic                          rd_a_valid,
    output logic [MMA_N*8-1:0]            rd_b_data,
    output logic                          rd_b_valid,

    // Stall outputs (combinational, one per port).
    output logic                          load_wr_stall_out,
    output logic                          mma_rd_a_stall_out,
    output logic                          mma_rd_b_stall_out
);

    // ------------------------------------------------------------------
    // Bank-storage parameters.
    // ------------------------------------------------------------------
    localparam int NUM_BANKS          = 32;
    localparam int BYTES_PER_DWORD    = 4;
    localparam int NUM_WORDS_PER_BANK = SMEM_BYTES / NUM_BANKS / BYTES_PER_DWORD;
    localparam int BANK_BITS          = 5;
    localparam int WORD_BITS          = $clog2(NUM_WORDS_PER_BANK);

    localparam int WR_DWORDS          = BEAT_BYTES / 4;   // 4
    localparam int RDA_DWORDS         = MMA_M      / 4;   // 8
    localparam int RDB_DWORDS         = MMA_N      / 4;   // 8

    // ------------------------------------------------------------------
    // Bank decode helpers. Functions take a full 32-bit address but use
    // only a slice; the unused bits are not a bug.
    // ------------------------------------------------------------------
    /* verilator lint_off UNUSEDSIGNAL */
    function automatic logic [BANK_BITS-1:0] bank_of(input logic [31:0] addr);
        return addr[6:2];
    endfunction

    function automatic logic [1:0] group_of(input logic [31:0] addr);
        return addr[6:5];
    endfunction
    /* verilator lint_on UNUSEDSIGNAL */

    // ------------------------------------------------------------------
    // Combinational conflict / stall logic.
    // Priority: LOAD_WR (top) > MMA_RD_A > MMA_RD_B.
    // ------------------------------------------------------------------
    logic wr_rd_a_conflict;
    logic wr_rd_b_conflict;
    logic rd_a_rd_b_conflict;

    always_comb begin
        wr_rd_a_conflict   = wr_en && rd_a_en && (group_of(wr_addr)   == group_of(rd_a_addr));
        wr_rd_b_conflict   = wr_en && rd_b_en && (group_of(wr_addr)   == group_of(rd_b_addr));
        rd_a_rd_b_conflict = rd_a_en && rd_b_en && (group_of(rd_a_addr) == group_of(rd_b_addr));

        load_wr_stall_out  = 1'b0;
        mma_rd_a_stall_out = wr_rd_a_conflict;
        mma_rd_b_stall_out = wr_rd_b_conflict || rd_a_rd_b_conflict;
    end

    // Effective per-port "accepted this cycle" gating.
    logic rd_a_accept;
    logic rd_b_accept;
    assign rd_a_accept = rd_a_en && !mma_rd_a_stall_out;
    assign rd_b_accept = rd_b_en && !mma_rd_b_stall_out;

    // ------------------------------------------------------------------
    // Per-bank port drives. For each bank b, compute (en, we, addr, wdata)
    // based on which port (if any) is targeting that bank this cycle.
    //
    // Bank coverage (precomputed combinationally):
    //   - LOAD_WR covers 4 consecutive banks starting at bank_of(wr_addr).
    //     Equivalently: bank b is covered iff bank_of(wr_addr) <= b <
    //     bank_of(wr_addr) + 4. With BEAT_BYTES=16 alignment we know this
    //     fits within a single 8-bank group.
    //   - RD_A covers 8 consecutive banks starting at bank_of(rd_a_addr) =
    //     {group_of(rd_a_addr), 3'b000}. Equivalently, the entire 8-bank
    //     group whose index equals group_of(rd_a_addr).
    //   - RD_B: same as RD_A.
    //
    // The per-bank word index within the bank is `addr[2+BANK_BITS +:
    // WORD_BITS]` (the dword-index above the bank-id bits).
    // ------------------------------------------------------------------
    // Bank-driver signals (bank_rdata is declared below alongside the
    // smem_bank instantiation block, since bank_rdata now comes from
    // the per-bank OR of the bank's gated outputs, not directly from
    // the SRAM macro).
    logic                       bank_en   [NUM_BANKS];
    logic                       bank_we   [NUM_BANKS];
    logic [WORD_BITS-1:0]       bank_addr [NUM_BANKS];
    logic [31:0]                bank_wdata[NUM_BANKS];

    // Per-bank arbitration. SCRUB > LOAD_WR > RD_A > RD_B. The stall logic
    // above guarantees only one port "covers" any given bank on a cycle
    // (other than during a stall cycle, which we resolve here by simply
    // honoring the higher-priority requester).
    //
    // Style note: all locals declared and pre-initialized at the top of the
    // always_comb. No `for (int x = ...)` (use module-level `int`). No
    // declarations inside conditional branches. This avoids spurious latch
    // inference under Yosys; see DEVELOPMENT.md §"Synthesis-friendly SV".
    int b_iter;
    int d_iter;
    logic [31:0]          dword_addr;
    logic [BANK_BITS-1:0] b_idx;
    logic [WORD_BITS-1:0] w_idx;

    always_comb begin
        // Every local must be assigned BEFORE any conditional branch so Yosys
        // does not infer a latch (it can't statically prove the for-loop
        // counter and helpers are all covered otherwise).
        b_iter     = 0;
        d_iter     = 0;
        dword_addr = '0;
        b_idx      = '0;
        w_idx      = '0;

        for (b_iter = 0; b_iter < NUM_BANKS; b_iter++) begin
            bank_en[b_iter]    = 1'b0;
            bank_we[b_iter]    = 1'b0;
            bank_addr[b_iter]  = '0;
            bank_wdata[b_iter] = '0;
        end

        if (scrub_en) begin
            for (b_iter = 0; b_iter < NUM_BANKS; b_iter++) begin
                bank_en[b_iter]    = 1'b1;
                bank_we[b_iter]    = 1'b1;
                bank_addr[b_iter]  = scrub_addr[WORD_BITS-1:0];
                bank_wdata[b_iter] = 32'd0;
            end
        end else begin
            // LOAD_WR: 4 dwords, one per consecutive bank.
            if (wr_en) begin
                for (d_iter = 0; d_iter < WR_DWORDS; d_iter++) begin
                    dword_addr   = wr_addr + 32'(d_iter * 4);
                    b_idx        = bank_of(dword_addr);
                    w_idx        = dword_addr[2 + BANK_BITS +: WORD_BITS];
                    bank_en[b_idx]    = 1'b1;
                    bank_we[b_idx]    = 1'b1;
                    bank_addr[b_idx]  = w_idx;
                    bank_wdata[b_idx] = wr_data[d_iter*32 +: 32];
                end
            end

            // RD_A (only on banks not claimed by LOAD_WR above; the stall
            // logic ensures no overlap when rd_a_accept is true).
            if (rd_a_accept) begin
                for (d_iter = 0; d_iter < RDA_DWORDS; d_iter++) begin
                    dword_addr = rd_a_addr + 32'(d_iter * 4);
                    b_idx      = bank_of(dword_addr);
                    w_idx      = dword_addr[2 + BANK_BITS +: WORD_BITS];
                    if (!bank_en[b_idx]) begin
                        bank_en[b_idx]   = 1'b1;
                        bank_we[b_idx]   = 1'b0;
                        bank_addr[b_idx] = w_idx;
                    end
                end
            end

            // RD_B (lowest priority).
            if (rd_b_accept) begin
                for (d_iter = 0; d_iter < RDB_DWORDS; d_iter++) begin
                    dword_addr = rd_b_addr + 32'(d_iter * 4);
                    b_idx      = bank_of(dword_addr);
                    w_idx      = dword_addr[2 + BANK_BITS +: WORD_BITS];
                    if (!bank_en[b_idx]) begin
                        bank_en[b_idx]   = 1'b1;
                        bank_we[b_idx]   = 1'b0;
                        bank_addr[b_idx] = w_idx;
                    end
                end
            end
        end
    end

    // ------------------------------------------------------------------
    // Per-bank rd_a / rd_b "I'm contributing to dword d this cycle".
    //
    // For our cyclic access pattern (8 consecutive banks starting at
    // bank_of(rd_*_pending_addr)), bank b contributes to output dword
    //   d = (b - bank_of(rd_*_pending_addr)) mod 32
    // when d < 8. We pass (active, dword_idx) to each smem_bank so the
    // bank itself can decide which of its 8 gated outputs to drive.
    //
    // This computation is per-bank, combinational; precomputed here so
    // the bank macros' inputs are clean per-bank signals.
    // ------------------------------------------------------------------
    logic [BANK_BITS-1:0] rd_a_base_bank;
    logic [BANK_BITS-1:0] rd_b_base_bank;
    logic                 rd_a_valid_for_banks;
    logic                 rd_b_valid_for_banks;
    assign rd_a_base_bank       = bank_of(rd_a_pending_addr);
    assign rd_b_base_bank       = bank_of(rd_b_pending_addr);
    assign rd_a_valid_for_banks = rd_a_pending_valid;
    assign rd_b_valid_for_banks = rd_b_pending_valid;

    logic                 bank_rd_a_active   [NUM_BANKS];
    logic [2:0]           bank_rd_a_dword_idx[NUM_BANKS];
    logic                 bank_rd_b_active   [NUM_BANKS];
    logic [2:0]           bank_rd_b_dword_idx[NUM_BANKS];

    always_comb begin
        logic [BANK_BITS-1:0] off_a, off_b;
        for (int b = 0; b < NUM_BANKS; b++) begin
            off_a = BANK_BITS'(b) - rd_a_base_bank;
            off_b = BANK_BITS'(b) - rd_b_base_bank;
            // Lower 3 bits are the dword position; high bits == 0 means
            // bank b is in the 8-bank read window.
            bank_rd_a_active[b]    = rd_a_valid_for_banks && (off_a[BANK_BITS-1:3] == 2'b00);
            bank_rd_a_dword_idx[b] = off_a[2:0];
            bank_rd_b_active[b]    = rd_b_valid_for_banks && (off_b[BANK_BITS-1:3] == 2'b00);
            bank_rd_b_dword_idx[b] = off_b[2:0];
        end
    end

    // ------------------------------------------------------------------
    // 32 smem_bank macros. Each owns its own fakeram + per-output-dword
    // gating logic. Hardened LEF (smem_bank.lef); chip-level OR-tree
    // below consolidates per-dword contributions across all 32 banks.
    //
    // No central rdata mux at smem level — the 1024 bank-rdata wires
    // that used to fan to a central mux are consumed inside each macro.
    // ------------------------------------------------------------------
    logic [31:0] bank_rd_a_out [NUM_BANKS][8];
    logic [31:0] bank_rd_b_out [NUM_BANKS][8];

    genvar gb;
    generate
        for (gb = 0; gb < NUM_BANKS; gb++) begin : gen_banks
            smem_bank #(
                .WORDS(NUM_WORDS_PER_BANK)
            ) u_bank (
                .clk            (clk),
                .en             (bank_en[gb]),
                .we             (bank_we[gb]),
                .addr           (bank_addr[gb]),
                .wdata          (bank_wdata[gb]),
                .rd_a_active    (bank_rd_a_active[gb]),
                .rd_a_dword_idx (bank_rd_a_dword_idx[gb]),
                .rd_b_active    (bank_rd_b_active[gb]),
                .rd_b_dword_idx (bank_rd_b_dword_idx[gb]),
                .rd_a_out       (bank_rd_a_out[gb]),
                .rd_b_out       (bank_rd_b_out[gb])
            );
        end
    endgenerate

    // Backdoor read-path for the previous monolithic mux pattern (other
    // smem internals reference `bank_rdata`). Kept for cocotb backdoor
    // compatibility and for the bank_mem shadow consistency check below.
    logic [31:0] bank_rdata [NUM_BANKS];
    generate
        for (gb = 0; gb < NUM_BANKS; gb++) begin : gen_bank_rdata_shim
            // Pull the rdata from whichever output the bank just drove
            // (only one of rd_a_out[0..7] / rd_b_out[0..7] is non-zero
            // per cycle, and only when the bank is actively reading;
            // OR them all together to get bank_rdata).
            always_comb begin
                bank_rdata[gb] = 32'd0;
                for (int d = 0; d < 8; d++) begin
                    bank_rdata[gb] |= bank_rd_a_out[gb][d] | bank_rd_b_out[gb][d];
                end
            end
        end
    endgenerate

    // ------------------------------------------------------------------
    // Sim-only shadow `bank_mem[NUM_BANKS][NUM_WORDS_PER_BANK]` used for
    // cocotb backdoor reads/writes. Mirrors every cycle's commit:
    //   - SCRUB cycles: zero scrub_addr word in every bank.
    //   - LOAD_WR cycles: write the four affected (bank, word) cells.
    // The drain path does NOT read from this shadow — it reads from the
    // real sram_1rw rdata outputs.
    //
    // For cocotb backdoor writes from a TB, write to bank_mem[b][w]
    // directly AND also to gen_banks[b].u_sram.mem[w] (a helper does
    // both). The shadow is needed because Verilator + cocotb access
    // through generate blocks is fiddly and the mma/smem testbenches
    // already rely on the bank_mem[b][w] handle.
    // ------------------------------------------------------------------
    /* verilator public_module */
    logic [31:0] bank_mem [NUM_BANKS][NUM_WORDS_PER_BANK] /* verilator public */;

    always_ff @(posedge clk) begin
        for (int b = 0; b < NUM_BANKS; b++) begin
            if (bank_en[b] && bank_we[b]) begin
                bank_mem[b][bank_addr[b]] <= bank_wdata[b];
            end
        end
    end

    // Backdoor byte view: read-only combinational alias of bank_mem.
    // Used by cocotb hierarchical reads (dut.u_smem.mem[byte_idx].value);
    // has no synthesizable readers, so the UNUSEDSIGNAL lint is suppressed.
    /* verilator lint_off UNUSEDSIGNAL */
    logic [7:0] mem [SMEM_BYTES];
    /* verilator lint_on UNUSEDSIGNAL */
    always_comb begin
        for (int i = 0; i < SMEM_BYTES; i++) begin
            mem[i] = bank_mem
                [(i >> 2) & (NUM_BANKS-1)]
                [(i >> (2 + BANK_BITS))]
                [(i & 3) * 8 +: 8];
        end
    end

    // ------------------------------------------------------------------
    // Pending-read state. We need the captured ADDRESSES (not just the
    // bank rdata) for two reasons:
    //   1. Drain-time byte-level forwarding mux compares pending_addr+i
    //      against the current cycle's [wr_addr, wr_addr+BEAT_BYTES).
    //   2. Determining which 8 banks' rdata to assemble into the result.
    // ------------------------------------------------------------------
    logic        rd_a_pending_valid;
    logic [31:0] rd_a_pending_addr;
    logic        rd_b_pending_valid;
    logic [31:0] rd_b_pending_addr;

    // ------------------------------------------------------------------
    // Drain — combinational gather from bank rdata + byte forwarding from
    // current-cycle wr_data. The pending read was captured one cycle ago
    // (at edge T) and the bank rdata flopped at edge T; we are now between
    // edge T and edge T+1, building the beat for the registered output.
    // ------------------------------------------------------------------
    // Beat assembly is split into RDA_DWORDS + RDB_DWORDS explicit
    // smem_dword_mux instances (4 bytes / 32 bits each). Each instance is
    // kept_hierarchy so ORFS macro_placement can place them at distinct
    // physical locations along the consumer edge — see
    // tech/asap7/orfs/smem.macro_placement.tcl. Without this hierarchy
    // discipline yosys flattens the 32-byte beat into a single central
    // mux, the 1024 bank_rdata wires all converge there, and asap7
    // detail-route can't escape the resulting congestion. See
    // tech/asap7/problems/B1_smem_bank_rdata_congestion.md.
    logic [MMA_M*8-1:0] rd_a_beat;
    logic [MMA_N*8-1:0] rd_b_beat;

    // Write-forward mask: one barrel-shift instead of MMA comparators.
    //
    // pos = wr_addr - rd_pending_addr (signed). Tells us where the 16-byte
    // write window sits inside the MMA-byte read window. If overlap, the
    // mask is a contiguous run of BEAT_BYTES 1s positioned at offset pos.
    //
    //   pos >=  MMA  : write past end of read    → no overlap, mask = 0
    //   pos <= -BB   : write before start of read → no overlap, mask = 0
    //   pos in [0, MMA)         : mask = 16'hFFFF << pos      (clipped to MMA bits)
    //   pos in [-(BB-1), 0)     : mask = 16'hFFFF >> (-pos)
    //
    // Byte index into wr_data for beat i (only valid when mask[i] = 1):
    //   byte_idx = (i - pos) mod BEAT_BYTES  — only 4 LSBs needed.
    //
    // 272 -> ~32 $alu cells: one 32-bit subtract + one 32-bit barrel-shift
    // per read port, then 4-bit muxes per beat. SHARE pass no longer OOMs.
    logic signed [31:0]      pos_a, pos_b;
    logic [31:0]             neg_pos_a, neg_pos_b;
    logic                    in_range_a, in_range_b;
    logic [MMA_M-1:0]        fwd_mask_a;
    logic [MMA_N-1:0]        fwd_mask_b;
    localparam int LOG2_MMA_M = $clog2(MMA_M);
    localparam int LOG2_MMA_N = $clog2(MMA_N);

    always_comb begin
        pos_a       = $signed(wr_addr) - $signed(rd_a_pending_addr);
        neg_pos_a   = -pos_a;
        in_range_a  = wr_en && (pos_a > -32'(BEAT_BYTES)) && (pos_a < 32'(MMA_M));
        if (!in_range_a) begin
            fwd_mask_a = '0;
        end else if (pos_a >= 0) begin
            fwd_mask_a = MMA_M'(32'h0000FFFF) << pos_a[LOG2_MMA_M-1:0];
        end else begin
            fwd_mask_a = MMA_M'(32'h0000FFFF) >> neg_pos_a[LOG2_MMA_M-1:0];
        end
    end

    always_comb begin
        pos_b       = $signed(wr_addr) - $signed(rd_b_pending_addr);
        neg_pos_b   = -pos_b;
        in_range_b  = wr_en && (pos_b > -32'(BEAT_BYTES)) && (pos_b < 32'(MMA_N));
        if (!in_range_b) begin
            fwd_mask_b = '0;
        end else if (pos_b >= 0) begin
            fwd_mask_b = MMA_N'(32'h0000FFFF) << pos_b[LOG2_MMA_N-1:0];
        end else begin
            fwd_mask_b = MMA_N'(32'h0000FFFF) >> neg_pos_b[LOG2_MMA_N-1:0];
        end
    end

    // Pre-compute the per-byte byte_idx_in_wr for both ports. The dword
    // mux is purely combinational on its inputs — keeping the byte_idx
    // arithmetic at the parent means every dword instance sees the same
    // wr_data + a tiny 4-bit index per byte, so yosys can't share the
    // arithmetic ALU across instances (which would defeat the hierarchy).
    logic [3:0] byte_idx_in_wr_a [MMA_M];
    logic [3:0] byte_idx_in_wr_b [MMA_N];

    always_comb begin
        for (int i = 0; i < MMA_M; i++) begin
            byte_idx_in_wr_a[i] = (4'(i) - pos_a[3:0]) & 4'hF;
        end
    end

    always_comb begin
        for (int i = 0; i < MMA_N; i++) begin
            byte_idx_in_wr_b[i] = (4'(i) - pos_b[3:0]) & 4'hF;
        end
    end

    // Per-dword OR-tree consolidation: for each output dword d, OR all
    // 32 banks' rd_*_out[d] contributions. Only the one bank that was
    // selected for dword d this cycle has a non-zero contribution; the
    // other 31 are 0 by the smem_bank gating. So OR ≡ mux without the
    // central convergence point — yosys will synthesize this as a tree
    // of OR gates that can be placed across the chip near the banks.
    //
    // After the OR, byte-level write-forwarding overlays wr_data bytes
    // where fwd_mask_*[i] is 1 (same forwarding semantics as the prior
    // dword_mux path).
    logic [31:0] rda_or [RDA_DWORDS];
    logic [31:0] rdb_or [RDB_DWORDS];

    always_comb begin
        for (int d = 0; d < RDA_DWORDS; d++) begin
            rda_or[d] = 32'd0;
            for (int b = 0; b < NUM_BANKS; b++) begin
                rda_or[d] |= bank_rd_a_out[b][d];
            end
        end
    end

    always_comb begin
        for (int d = 0; d < RDB_DWORDS; d++) begin
            rdb_or[d] = 32'd0;
            for (int b = 0; b < NUM_BANKS; b++) begin
                rdb_or[d] |= bank_rd_b_out[b][d];
            end
        end
    end

    // Byte-level overlay: for each output byte i in 0..MMA_M-1, take the
    // wr_data byte if fwd_mask_a[i] else the bank-OR-tree byte.
    always_comb begin
        for (int i = 0; i < MMA_M; i++) begin
            if (fwd_mask_a[i]) begin
                // byte_idx = (i - pos_a) mod 16; only need 4 LSBs.
                rd_a_beat[i*8 +: 8] = wr_data[byte_idx_in_wr_a[i] * 8 +: 8];
            end else begin
                // Dword d = i / 4; byte position within dword = i % 4.
                rd_a_beat[i*8 +: 8] = rda_or[i >> 2][(i & 32'h3) * 8 +: 8];
            end
        end
    end

    always_comb begin
        for (int i = 0; i < MMA_N; i++) begin
            if (fwd_mask_b[i]) begin
                rd_b_beat[i*8 +: 8] = wr_data[byte_idx_in_wr_b[i] * 8 +: 8];
            end else begin
                rd_b_beat[i*8 +: 8] = rdb_or[i >> 2][(i & 32'h3) * 8 +: 8];
            end
        end
    end

    // ------------------------------------------------------------------
    // Sequential commit:
    //   - Capture new pending reads (gated by !stall).
    //   - Drain previous-cycle pending reads into registered outputs.
    // The bank's own rdata flop is the "drain capture"; this `always_ff`
    // only handles the wrapper-level pending tracking and the output
    // registers.
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (reset) begin
            rd_a_pending_valid <= 1'b0;
            rd_a_pending_addr  <= 32'd0;
            rd_b_pending_valid <= 1'b0;
            rd_b_pending_addr  <= 32'd0;
            rd_a_data          <= '0;
            rd_a_valid         <= 1'b0;
            rd_b_data          <= '0;
            rd_b_valid         <= 1'b0;
        end else begin
            // Drain (current cycle's bank rdata is ready since the bank
            // was issued a read last cycle).
            if (rd_a_pending_valid) begin
                rd_a_data  <= rd_a_beat;
                rd_a_valid <= 1'b1;
            end else begin
                rd_a_data  <= '0;
                rd_a_valid <= 1'b0;
            end

            if (rd_b_pending_valid) begin
                rd_b_data  <= rd_b_beat;
                rd_b_valid <= 1'b1;
            end else begin
                rd_b_data  <= '0;
                rd_b_valid <= 1'b0;
            end

            // Capture new pending reads (matches accepted-by-bank cycle).
            if (rd_a_accept) begin
                rd_a_pending_valid <= 1'b1;
                rd_a_pending_addr  <= rd_a_addr;
            end else begin
                rd_a_pending_valid <= 1'b0;
            end

            if (rd_b_accept) begin
                rd_b_pending_valid <= 1'b1;
                rd_b_pending_addr  <= rd_b_addr;
            end else begin
                rd_b_pending_valid <= 1'b0;
            end
        end
    end

endmodule
