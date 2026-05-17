// smem.sv — banked on-chip scratchpad.
//
// 32-bank structurally-banked memory, modeled to mirror a B200-style SMEM.
// Each bank is one 4-byte (32-bit) dword wide. The dword-index decode is
// plain round-robin: `bank_of(addr) = (addr / 4) % 32 = addr[6:2]`.
//
//   wr  addr     -> 4 consecutive banks  (BEAT_BYTES = 16, 4 dwords)
//   rd_a addr    -> 8 consecutive banks  (MMA_M     = 32, 8 dwords)
//   rd_b addr    -> 8 consecutive banks  (MMA_N     = 32, 8 dwords)
//
// Per-port "active bank mask" is the set of banks each port wants this cycle.
// Because LOAD_WR is BEAT_BYTES-aligned, MMA_RD_A is MMA_M-aligned, and
// MMA_RD_B is MMA_N-aligned, each port's bank mask is a contiguous run of
// banks starting at a power-of-two-aligned base.
//
// CONFLICT and STALL PROTOCOL
// ===========================
//   For each of the 32 banks, count how many ports want it (1RW SRAMs:
//   max 1 access/cycle/bank). If any bank has >1 requester this cycle, a
//   conflict exists.
//
//   PRIORITY (fixed): LOAD_WR > MMA_RD_A > MMA_RD_B. Lower-priority losers
//   STALL (request not honored this cycle, consumer must re-issue next).
//
//     load_wr_stall_out   = 1'b0                           // top priority
//     mma_rd_a_stall_out  = rd_a_en && wr_en
//                           && (rd_a_addr[6:5] == wr_addr[6:5])
//     mma_rd_b_stall_out  = rd_b_en && (
//                              (wr_en   && rd_b_addr[6:5] == wr_addr[6:5])
//                           || (rd_a_en && rd_b_addr[6:5] == rd_a_addr[6:5])
//                         )
//
//   The "[6:5]" trick exploits alignment: with our aligned ports, two ports'
//   bank ranges overlap iff their bank-group indices `addr[6:5]` agree.
//   - LOAD_WR (16B aligned) occupies 4 banks within ONE 8-bank group
//     (group = wr_addr[6:5]).
//   - MMA_RD_A / MMA_RD_B (32B aligned) each occupy ONE entire 8-bank group.
//   So conflict ⇔ matching group index.
//
//   Stall outputs are COMBINATIONAL on the cycle's inputs. Consumers
//   sample stall the cycle the memory module sees the request, then choose
//   not to advance internal counters on a stall. (See load.sv / mma.sv.)
//
// COMMIT RULES under stall
// ========================
//   * LOAD_WR: never stalls; if wr_en=1, write commits.
//   * MMA_RD_A: if stalled, the pending-read slot is NOT updated (consumer
//     keeps the request asserted next cycle and we'll capture it then).
//   * MMA_RD_B: same.
//
// PORT PARITY with pymodel
// ========================
//   Ports / names / packing match pymodel.smem.SMEM exactly (so
//   common.tb_utils.step_and_compare with string-keyed inputs/outputs works).
//   Byte packing: byte k lives in bits [k*8 +: 8] (little-endian within a
//   beat / read window).
//
// STORAGE
// =======
//   32 separate `logic [31:0] bank_mem[NUM_WORDS_PER_BANK]` arrays.
//   NUM_WORDS_PER_BANK = SMEM_BYTES / 32 / 4.
//   Word index within a bank = `addr[CLOG2_SMEM-1:7]`.
//   Per-bank: at most one read OR one write per cycle (1RW). Conflict
//   detection ensures we never schedule more than one.
//
//   A backdoor `mem[]` byte view (read-only, combinationally derived from
//   `bank_mem[][]`) is exposed for hierarchical access from cocotb TBs.
//   Backdoor TB WRITES must address `bank_mem[bank][word]` directly.
//
// RESET
// =====
//   Dominant. Clears pending state and registered outputs; bank contents
//   preserved (matches gmem.sv / tmem.sv semantics). The post-power-on
//   zeroing of bank contents is now performed by the SCRUB PORT below
//   (driven by reset_seq), not by an `initial begin` block.
//
// SCRUB PORT
// ==========
//   Driven by reset_seq during the post-power-on scrub window. Replaces
//   the simulation-only `initial begin` zero-init. When scrub_en=1, ALL
//   32 banks are written to 0 at scrub_addr (the per-bank word index).
//
//   scrub_en is mutually exclusive with wr_en / rd_a_en / rd_b_en —
//   chip_in_reset gates those off upstream, so the precondition holds
//   without internal arbitration.

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

    // SCRUB (reset-only; driven by reset_seq)
    input  logic                          scrub_en,
    input  logic [31:0]                   scrub_addr,  // per-bank word index

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
    localparam int BANK_BITS          = 5;  // log2(NUM_BANKS) = 5
    // Width of the word-in-bank index.
    localparam int WORD_BITS          = $clog2(NUM_WORDS_PER_BANK);

    // Number of dwords per port operation.
    localparam int WR_DWORDS          = BEAT_BYTES / 4;   // 4
    localparam int RDA_DWORDS         = MMA_M      / 4;   // 8
    localparam int RDB_DWORDS         = MMA_N      / 4;   // 8

    // ------------------------------------------------------------------
    // 32 banks of NUM_WORDS_PER_BANK 32-bit words each.
    // ------------------------------------------------------------------
    logic [31:0] bank_mem [NUM_BANKS][NUM_WORDS_PER_BANK];

    // NOTE: bank_mem is no longer zero-initialized at sim startup. The
    // reset_seq module is responsible for scrubbing all banks via the
    // SCRUB PORT (scrub_en + scrub_addr) before chip_in_reset deasserts.
    // This mirrors real silicon: FFs / SRAMs power up to indeterminate
    // values, and the reset sequencer must walk them to a known state.

    // ------------------------------------------------------------------
    // Backdoor byte view: read-only combinational alias of bank_mem.
    // Provided for cocotb hierarchical reads (dut.u_smem.mem[byte_idx].value).
    // Writes through this alias are NOT supported — TBs that need to
    // back-door a tile into smem must write to dut.u_smem.bank_mem[b][w].
    // ------------------------------------------------------------------
    logic [7:0] mem [SMEM_BYTES];
    always_comb begin
        for (int i = 0; i < SMEM_BYTES; i++) begin
            // byte i lives at:
            //   bank        = (i / 4) % 32  = i[6:2]
            //   word        = i / 128       = i[13:7]   (for SMEM_BYTES=16384)
            //   byte_in_dw  = i % 4          = i[1:0]
            mem[i] = bank_mem
                [(i >> 2) & (NUM_BANKS-1)]
                [(i >> (2 + BANK_BITS))]
                [(i & 3) * 8 +: 8];
        end
    end

    // ------------------------------------------------------------------
    // Bank decode helpers.
    //
    // bank_of(addr) = addr[6:2]. With our alignment guarantees, a port's
    // requested bank set is exactly the run of `N` banks starting at the
    // port's base bank — which is also expressible as the 8-bank group
    // `addr[6:5]` (when N==8) or sits entirely within one 8-bank group
    // (when N==4 i.e. LOAD_WR).
    // ------------------------------------------------------------------
    function automatic logic [BANK_BITS-1:0] bank_of(input logic [31:0] addr);
        return addr[6:2];
    endfunction

    // 8-bank-group index of an aligned address.
    function automatic logic [1:0] group_of(input logic [31:0] addr);
        return addr[6:5];
    endfunction

    // ------------------------------------------------------------------
    // Combinational conflict / stall logic.
    //
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

    // ------------------------------------------------------------------
    // Pending-read state for the registered 1-cycle read latency. We
    // capture a new pending read iff (rd_*_en && !stall).
    // ------------------------------------------------------------------
    logic        rd_a_pending_valid;
    logic [31:0] rd_a_pending_addr;
    logic        rd_b_pending_valid;
    logic [31:0] rd_b_pending_addr;

    // ------------------------------------------------------------------
    // Per-port read drain with per-byte write-forwarding.
    //
    // Per spec (matches pymodel commit phase): LOAD_WR commits BEFORE the
    // drain of a previously-captured pending MMA_RD_*. So a pending read
    // whose bytes overlap a same-cycle LOAD_WR returns the NEW data. We
    // implement this as a byte-by-byte forwarding mux on the drain path:
    // for each byte position, choose the wr_data byte if the address sits
    // inside [wr_addr, wr_addr+BEAT_BYTES); else read from bank_mem.
    //
    // (Bank conflicts can't simultaneously stall RD_A *and* deliver a
    // drained read of the same byte being written — the pending read was
    // captured a cycle ago when there was no conflict. This forwarding is
    // for the legal write-then-drain case only.)
    // ------------------------------------------------------------------
    function automatic logic [7:0] read_byte_at(input logic [31:0] addr);
        return bank_mem
            [(addr >> 2) & (NUM_BANKS-1)]
            [(addr >> (2 + BANK_BITS))]
            [(addr & 3) * 8 +: 8];
    endfunction

    logic [MMA_M*8-1:0] rd_a_beat;
    always_comb begin
        logic [31:0] rb;
        logic        fwd;
        rd_a_beat = '0;
        for (int i = 0; i < MMA_M; i++) begin
            rb  = rd_a_pending_addr + i;
            fwd = wr_en && (rb >= wr_addr) && (rb < wr_addr + BEAT_BYTES);
            if (fwd) begin
                rd_a_beat[i*8 +: 8] = wr_data[(rb - wr_addr)*8 +: 8];
            end else begin
                rd_a_beat[i*8 +: 8] = read_byte_at(rb);
            end
        end
    end

    logic [MMA_N*8-1:0] rd_b_beat;
    always_comb begin
        logic [31:0] rb;
        logic        fwd;
        rd_b_beat = '0;
        for (int i = 0; i < MMA_N; i++) begin
            rb  = rd_b_pending_addr + i;
            fwd = wr_en && (rb >= wr_addr) && (rb < wr_addr + BEAT_BYTES);
            if (fwd) begin
                rd_b_beat[i*8 +: 8] = wr_data[(rb - wr_addr)*8 +: 8];
            end else begin
                rd_b_beat[i*8 +: 8] = read_byte_at(rb);
            end
        end
    end

    // ------------------------------------------------------------------
    // Sequential commit.
    //
    // 1. LOAD_WR commits unconditionally (never stalls). Per-dword write
    //    into the matching banks.
    // 2. Drain previous-cycle pending MMA_RD_A: produce rd_a_data/valid.
    // 3. Drain previous-cycle pending MMA_RD_B similarly.
    // 4. Capture new pending reads, gated on !stall.
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        // SCRUB PORT — independent of reset. reset_seq drives scrub_en
        // during the post-power-on scrub window (while chip_in_reset is
        // high). Writes ALL 32 banks at scrub_addr in parallel to 0.
        // This is the synthesizable replacement for the old `initial`
        // zero-init. scrub_en is mutually exclusive with wr_en (upstream
        // is held in reset), so there's no conflict with the LOAD_WR
        // commit below.
        if (scrub_en) begin
            for (int b = 0; b < NUM_BANKS; b++) begin
                bank_mem[b][scrub_addr[WORD_BITS-1:0]] <= 32'd0;
            end
        end

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
            // 1. Commit write (one dword per active bank).
            if (wr_en) begin
                for (int d = 0; d < WR_DWORDS; d++) begin
                    automatic logic [31:0] dword_addr = wr_addr + d * 4;
                    automatic logic [BANK_BITS-1:0] b   = bank_of(dword_addr);
                    automatic logic [WORD_BITS-1:0] w   = dword_addr[2 + BANK_BITS +: WORD_BITS];
                    bank_mem[b][w] <= wr_data[d*32 +: 32];
                end
            end

            // 2. Drain MMA_RD_A.
            if (rd_a_pending_valid) begin
                rd_a_data  <= rd_a_beat;
                rd_a_valid <= 1'b1;
            end else begin
                rd_a_data  <= '0;
                rd_a_valid <= 1'b0;
            end

            // 3. Drain MMA_RD_B.
            if (rd_b_pending_valid) begin
                rd_b_data  <= rd_b_beat;
                rd_b_valid <= 1'b1;
            end else begin
                rd_b_data  <= '0;
                rd_b_valid <= 1'b0;
            end

            // 4. Capture new pending reads (only if not stalled).
            if (rd_a_en && !mma_rd_a_stall_out) begin
                rd_a_pending_valid <= 1'b1;
                rd_a_pending_addr  <= rd_a_addr;
            end else begin
                rd_a_pending_valid <= 1'b0;
            end

            if (rd_b_en && !mma_rd_b_stall_out) begin
                rd_b_pending_valid <= 1'b1;
                rd_b_pending_addr  <= rd_b_addr;
            end else begin
                rd_b_pending_valid <= 1'b0;
            end
        end
    end

endmodule
