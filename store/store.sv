// store.sv -- STORE engine: drains a compute_array slot to GMEM.
//
// SV implementation of pymodel.store.Store. The pymodel uses back-door
// access (compute_array.get_tile + byte-by-byte beat emit); this RTL is
// cycle-accurate against the drain-stream interface and emits identical
// gmem bytes.
//
// SYNC MODEL
//   STORE is synchronous in v1: cmdproc holds issue_en until done pulses.
//   We ignore re-issues while busy.
//
// INTERFACE
//   STORE asks compute_array to drain a slot row-by-row:
//     - drives drain_issue + drain_slot,
//     - receives MMA_N*32 bits/cycle on
//       drain_row_valid + drain_row_data + drain_row_idx for MMA_M cycles,
//       with drain_last marking the final row.
//   Rows land in 4 × tile_buf_8row banks (drain_row_idx[4:3] selects bank,
//   drain_row_idx[2:0] selects row within bank).
//
//   STORE then streams the tile out to GMEM at BEAT_BYTES per cycle,
//   reading one row at a time from the banks (registered 1-cycle read),
//   optionally fp8-encoding inline, and slicing the row into BEAT-sized
//   beats. No intermediate bytes_buf — encoding happens on-the-fly.
//
// PIPELINE
//   S_IDLE → (issue_en) → S_GATHER → (drain_last) → S_DRAIN → S_IDLE
//
//   S_GATHER (MMA_M cycles + 1):
//     - On each drain_row_valid, write the row to the addressed bank.
//     - When drain_last seen, latch gather_done.
//     - In the cycle after gather_done is latched, pre-issue rd_en for
//       row 0 (bank 0). State transitions to S_DRAIN the same cycle so
//       rd_data is registered to row-0 contents one cycle into S_DRAIN.
//
//   S_DRAIN (total_beats cycles):
//     - Each cycle emits one BEAT_BYTES write to GMEM.
//     - Combinational 4:1 mux on bank rd_data picks the active row.
//     - For fp8 dtype, 32 fp8_encode units convert the active row to a
//       256-bit byte stream; otherwise the raw 1024-bit row is used.
//     - Beat slice = data[beat_in_row*128 +: 128].
//     - On the LAST beat of the current row (and not the last row),
//       pre-issue rd_en for the next row so its rd_data is ready when
//       beat_in_row wraps to 0.
//     - On the very last beat: pulse done, state → S_IDLE.
//
// FP8 ENCODE (dtype=1)
//   Combinational: 32 instances of fp8_encode (one per column), down from
//   1024 in the old monolithic implementation. The encoders fan in from
//   the muxed row read, not from a 32k-bit packed buffer.

module store #(
    parameter int MMA_M      = 32,
    parameter int MMA_N      = 32,
    parameter int BEAT_BYTES = 16
) (
    input  logic                          clk,
    input  logic                          reset,

    // Issue
    input  logic                          issue_en,
    input  logic [31:0]                   tmem_slot,
    input  logic [31:0]                   gmem_ptr,
    input  logic                          dtype,    // 0 = fp32, 1 = fp8

    // Drain interface to compute_array (issue side)
    output logic                          drain_issue,
    output logic [31:0]                   drain_slot,

    // Drain row stream from compute_array (response side).
    input  logic                          drain_row_valid,
    input  logic [MMA_N*32-1:0]           drain_row_data,
    input  logic [$clog2(MMA_M)-1:0]      drain_row_idx,
    input  logic                          drain_last,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic                          drain_done,
    /* verilator lint_on UNUSEDSIGNAL */

    // To GMEM write port
    output logic                          wr_en,
    output logic [31:0]                   wr_addr,
    output logic [BEAT_BYTES*8-1:0]       wr_data,

    // Status
    output logic                          busy,
    output logic                          done
);

    // ------------------------------------------------------------------
    // Derived sizes
    // ------------------------------------------------------------------
    localparam int NUM_ELEMS    = MMA_M * MMA_N;
    localparam int TILE_BYTES   = NUM_ELEMS * 4;             // fp32 path
    localparam int FP8_BYTES    = NUM_ELEMS;                 // fp8 path
    localparam int ROW_W        = MMA_N * 32;                // 1024
    localparam int BANKS        = 4;
    localparam int ROWS_PER_BANK = MMA_M / BANKS;            // 8
    localparam int BANK_SEL_BITS = $clog2(BANKS);            // 2
    localparam int BANK_ROW_BITS = $clog2(ROWS_PER_BANK);    // 3
    localparam int ROW_IDX_BITS  = $clog2(MMA_M);            // 5
    localparam int BEAT_BITS     = BEAT_BYTES * 8;           // 128
    localparam int FP32_BEATS_PER_ROW = ROW_W / BEAT_BITS;   // 8
    localparam int FP8_BEATS_PER_ROW  = (MMA_N * 8) / BEAT_BITS; // 2
    localparam int MAX_BEATS_PER_ROW  = FP32_BEATS_PER_ROW;  // 8
    localparam int BEAT_IDX_BITS = $clog2(MAX_BEATS_PER_ROW); // 3

    // ------------------------------------------------------------------
    // FSM
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        S_IDLE   = 2'd0,
        S_GATHER = 2'd1,
        S_DRAIN  = 2'd2
    } state_t;

    state_t state;

    // Latched operands.
    logic [31:0] saved_gmem_ptr;
    logic        saved_dtype;

    // Counters
    logic [ROW_IDX_BITS-1:0]  cur_row;
    logic [BEAT_IDX_BITS-1:0] beat_in_row;
    logic [31:0]              bytes_written;
    logic [31:0]              total_bytes;
    logic [BEAT_IDX_BITS-1:0] beats_per_row_m1;  // beats_per_row - 1

    // Gather completion latch
    logic gather_done;

    // ------------------------------------------------------------------
    // 4 × tile_buf_8row banks
    // ------------------------------------------------------------------
    logic [BANKS-1:0]              bank_wr_en;
    logic [BANK_ROW_BITS-1:0]      bank_wr_row;
    logic [ROW_W-1:0]              bank_wr_data;

    logic [BANKS-1:0]              bank_rd_en;
    logic [BANK_ROW_BITS-1:0]      bank_rd_row;
    logic [ROW_W-1:0]              bank_rd_data [BANKS];

    genvar gb;
    generate
        for (gb = 0; gb < BANKS; gb++) begin : gen_banks
            tile_buf_8row #(
                .N_ROWS (ROWS_PER_BANK),
                .ROW_W  (ROW_W)
            ) u_bank (
                .clk     (clk),
                .reset   (reset),
                .wr_en   (bank_wr_en[gb]),
                .wr_row  (bank_wr_row),
                .wr_data (bank_wr_data),
                .rd_en   (bank_rd_en[gb]),
                .rd_row  (bank_rd_row),
                .rd_data (bank_rd_data[gb])
            );
        end
    endgenerate

    // ------------------------------------------------------------------
    // Bank port driving
    // ------------------------------------------------------------------
    // Write side: drain_row_idx top 2 bits select bank, bottom 3 bits
    // select row within bank. Only the selected bank's wr_en pulses.
    logic [BANK_SEL_BITS-1:0]   wr_bank_sel;
    assign wr_bank_sel  = drain_row_idx[ROW_IDX_BITS-1 -: BANK_SEL_BITS];
    assign bank_wr_row  = drain_row_idx[BANK_ROW_BITS-1:0];
    assign bank_wr_data = drain_row_data;

    // Read side: the "next row to fetch" — pre-issued so its data is on
    // bank_rd_data the following cycle.
    logic [ROW_IDX_BITS-1:0]    next_rd_row;
    logic                       next_rd_en;
    logic [BANK_SEL_BITS-1:0]   next_rd_bank;

    assign next_rd_bank = next_rd_row[ROW_IDX_BITS-1 -: BANK_SEL_BITS];
    assign bank_rd_row  = next_rd_row[BANK_ROW_BITS-1:0];

    integer wbi;
    integer rbi;
    always_comb begin
        // Defaults
        for (wbi = 0; wbi < BANKS; wbi++) begin
            bank_wr_en[wbi] = 1'b0;
        end
        for (rbi = 0; rbi < BANKS; rbi++) begin
            bank_rd_en[rbi] = 1'b0;
        end

        // GATHER: route incoming row to the addressed bank.
        if (state == S_GATHER && drain_row_valid) begin
            bank_wr_en[wr_bank_sel] = 1'b1;
        end

        // Pre-issued read goes to one bank.
        if (next_rd_en) begin
            bank_rd_en[next_rd_bank] = 1'b1;
        end
    end

    // ------------------------------------------------------------------
    // Combinational pre-issue read scheduling
    // ------------------------------------------------------------------
    // We pre-issue the NEXT row's read one cycle before its data is
    // needed, so that the registered read latency is hidden.
    //
    //   - Entering S_DRAIN: pre-issue row 0.
    //   - In S_DRAIN on the LAST beat of cur_row (and not the last row
    //     of the tile): pre-issue cur_row+1.
    always_comb begin
        next_rd_en  = 1'b0;
        next_rd_row = '0;

        if (state == S_GATHER && gather_done) begin
            // This cycle is the GATHER→DRAIN handoff. Pre-issue row 0.
            next_rd_en  = 1'b1;
            next_rd_row = '0;
        end else if (state == S_DRAIN) begin
            // Last beat of current row → pre-issue next row.
            if (beat_in_row == beats_per_row_m1 &&
                cur_row != ROW_IDX_BITS'(MMA_M - 1)) begin
                next_rd_en  = 1'b1;
                next_rd_row = cur_row + ROW_IDX_BITS'(1);
            end
        end
    end

    // ------------------------------------------------------------------
    // Combinational row select for emit + fp8 encode
    // ------------------------------------------------------------------
    logic [BANK_SEL_BITS-1:0] cur_row_bank;
    logic [ROW_W-1:0]         cur_row_data;
    logic [FP8_BYTES*8-1:0]   fp8_row_bytes_unused;  // wide alias not used
    logic [MMA_N*8-1:0]       fp8_row_data;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [ROW_W-1:0]         emit_data_wide;
    /* verilator lint_on UNUSEDSIGNAL */

    assign cur_row_bank = cur_row[ROW_IDX_BITS-1 -: BANK_SEL_BITS];
    assign cur_row_data = bank_rd_data[cur_row_bank];

    // fp8 encode array — MMA_N (=32) instances, one per column.
    genvar ge;
    generate
        for (ge = 0; ge < MMA_N; ge++) begin : gen_enc
            fp8_encode u_enc (
                .fp32 (cur_row_data[ge*32 +: 32]),
                .fp8  (fp8_row_data[ge*8 +: 8])
            );
        end
    endgenerate

    // Emit data is either the raw fp32 row (1024b) or the fp8-encoded
    // row sign-extended to 1024b (only the low 256b is meaningful for
    // fp8; beats > beats_per_row_m1 are never sliced).
    always_comb begin
        if (saved_dtype == 1'b0) begin
            emit_data_wide = cur_row_data;
        end else begin
            emit_data_wide = {{(ROW_W - MMA_N*8){1'b0}}, fp8_row_data};
        end
    end

    // ------------------------------------------------------------------
    // Sequential logic
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (reset) begin
            state            <= S_IDLE;
            busy             <= 1'b0;
            done             <= 1'b0;
            drain_issue      <= 1'b0;
            drain_slot       <= 32'd0;
            wr_en            <= 1'b0;
            wr_addr          <= 32'd0;
            wr_data          <= '0;
            saved_gmem_ptr   <= 32'd0;
            saved_dtype      <= 1'b0;
            cur_row          <= '0;
            beat_in_row      <= '0;
            bytes_written    <= 32'd0;
            total_bytes      <= 32'd0;
            beats_per_row_m1 <= '0;
            gather_done      <= 1'b0;
        end else begin
            // Defaults
            done        <= 1'b0;
            drain_issue <= 1'b0;
            drain_slot  <= 32'd0;
            wr_en       <= 1'b0;
            wr_addr     <= 32'd0;
            wr_data     <= '0;

            unique case (state)
                S_IDLE: begin
                    if (issue_en) begin
                        saved_gmem_ptr   <= gmem_ptr;
                        saved_dtype      <= dtype;
                        drain_issue      <= 1'b1;
                        drain_slot       <= tmem_slot;
                        total_bytes      <= dtype ? FP8_BYTES : TILE_BYTES;
                        beats_per_row_m1 <= dtype
                            ? BEAT_IDX_BITS'(FP8_BEATS_PER_ROW - 1)
                            : BEAT_IDX_BITS'(FP32_BEATS_PER_ROW - 1);
                        cur_row          <= '0;
                        beat_in_row      <= '0;
                        bytes_written    <= 32'd0;
                        gather_done      <= 1'b0;
                        busy             <= 1'b1;
                        state            <= S_GATHER;
                    end
                end

                S_GATHER: begin
                    // Bank writes are driven combinationally above.
                    if (drain_row_valid && drain_last) begin
                        gather_done <= 1'b1;
                    end
                    // Once we've latched gather_done, transition. The
                    // pre-issue read for row 0 fires combinationally this
                    // same cycle, so rd_data is row-0 contents next cycle.
                    if (gather_done) begin
                        state       <= S_DRAIN;
                        gather_done <= 1'b0;
                    end
                end

                S_DRAIN: begin
                    // Emit one beat per cycle.
                    wr_en   <= 1'b1;
                    wr_addr <= saved_gmem_ptr + bytes_written;
                    wr_data <= emit_data_wide[beat_in_row * BEAT_BITS +: BEAT_BITS];

                    if (beat_in_row == beats_per_row_m1) begin
                        // End of current row.
                        beat_in_row <= '0;
                        if (cur_row == ROW_IDX_BITS'(MMA_M - 1)) begin
                            // End of tile.
                            done          <= 1'b1;
                            busy          <= 1'b0;
                            state         <= S_IDLE;
                            bytes_written <= 32'd0;
                            cur_row       <= '0;
                        end else begin
                            cur_row       <= cur_row + ROW_IDX_BITS'(1);
                            bytes_written <= bytes_written + BEAT_BYTES;
                        end
                    end else begin
                        beat_in_row   <= beat_in_row + BEAT_IDX_BITS'(1);
                        bytes_written <= bytes_written + BEAT_BYTES;
                    end
                end

                default: begin
                    state <= S_IDLE;
                end
            endcase
        end
    end

endmodule
