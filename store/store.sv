// store.sv -- STORE engine: drains a compute_array slot to GMEM.
//
// SV implementation of pymodel.store.Store. See pymodel/store.py for the
// canonical spec; this module must match it cycle-by-cycle.
//
// SYNC MODEL
//   STORE is synchronous in v1: cmdproc holds issue_en until done pulses.
//   We ignore re-issues while busy. (Cmdproc is responsible for not issuing
//   another STORE while this one is busy, but we defensively ignore.)
//
// INTERFACE (Phase 7h-3 — drain-stream)
//   STORE no longer reads a 32k-bit tile from TMEM in one shot. Instead it
//   asks compute_array to drain a slot row-by-row:
//     - drives drain_issue + drain_slot,
//     - receives one row of MMA_N * 32 bits per cycle (1 row/cycle for
//       MMA_M cycles) on drain_row_valid + drain_row_data + drain_row_idx,
//       with drain_last marking the final row.
//   Internally we accumulate the rows into a packed tile buffer, then drain
//   to GMEM at BEAT_BYTES per cycle. Simple v1: gather all rows, then drain
//   serially. The MMA_M-cycle gather phase + the K-beat drain phase do not
//   pipeline against each other in this version (the task description
//   permits this).
//
// PIPELINE
//   Cycle 0 (busy=0, issue_en=1):
//     - latch saved (slot, gmem_ptr, dtype)
//     - drive compute_array.drain_issue + drain_slot
//     - busy <= 1, state <= GATHER
//   Cycles 1..M+latency (state=GATHER):
//     - Every cycle drain_row_valid is high, write that row's MMA_N*32 bits
//       into tile_buf at slot drain_row_idx.
//     - When drain_last fires (last row valid), capture it and on the next
//       cycle enter FORMAT.
//   Cycle FORMAT (state=FORMAT):
//     - Pack tile_buf into bytes_buf per dtype:
//         dtype==0 (fp32): bytes_buf bits = tile_buf bits verbatim
//         dtype==1 (fp8):  one fp8_encode per element → bytes_buf low N*8 bits
//     - bytes_written <= 0, state <= DRAIN
//   Cycles 0..K-1 (state=DRAIN, K = total_bytes / BEAT_BYTES):
//     - gmem.wr_en = 1, wr_addr = gmem_ptr + bytes_written
//     - wr_data = bytes_buf[bytes_written*8 +: BEAT_BYTES*8]
//     - bytes_written <= bytes_written + BEAT_BYTES
//     - when bytes_written + BEAT_BYTES == total_bytes:
//         done <= 1, busy <= 0, state <= IDLE
//
// FP8 ENCODE (dtype=1):
//   Combinational: see common/fp8_encode.sv. The MMA_M*MMA_N encoders run
//   in parallel on tile_buf bits during the FORMAT cycle. (Same fan-out as
//   before; only the source signal changed from t_store_rd_tile to the
//   internal tile_buf.)

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
    // `drain_done` is an info pulse one cycle after drain_last; store uses
    // drain_last instead (when the final row's data is on the bus) to
    // gate the transition into S_FORMAT, so we silence the unused-input lint.
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
    localparam int TILE_BYTES   = NUM_ELEMS * 4;  // fp32 path
    localparam int FP8_BYTES    = NUM_ELEMS;      // fp8 path
    localparam int MAX_BYTES    = TILE_BYTES;     // buf sized to fp32

    // ------------------------------------------------------------------
    // FSM
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        S_IDLE   = 2'd0,
        S_GATHER = 2'd1,
        S_FORMAT = 2'd2,
        S_DRAIN  = 2'd3
    } state_t;

    state_t state;

    // Latched operands.
    logic [31:0] saved_gmem_ptr;
    logic        saved_dtype;

    // Gathered tile buffer (MMA_M rows of MMA_N fp32 words = 32k bits).
    // Filled row-by-row from drain_row_data; element [i][j] at bit
    // (i*MMA_N + j)*32 — same convention as the old TMEM tile packing.
    /* verilator lint_off WIDTHCONCAT */
    logic [MMA_M*MMA_N*32-1:0] tile_buf;
    /* verilator lint_on WIDTHCONCAT */

    // Drain buffer (sized to max output: TILE_BYTES bytes).
    /* verilator lint_off WIDTHCONCAT */
    logic [MAX_BYTES*8-1:0] bytes_buf;
    /* verilator lint_on WIDTHCONCAT */

    logic [31:0] total_bytes;
    logic [31:0] bytes_written;

    // Track that drain_last was seen — gather completes once the final
    // row has been latched into tile_buf.
    logic gather_done;

    // ------------------------------------------------------------------
    // Combinational fp8-encode array. Inputs are the tile bits from
    // tile_buf, one fp32 word per element. Outputs are concatenated into
    // `fp8_bytes` (LSB-first, one byte per element).
    // ------------------------------------------------------------------
    logic [FP8_BYTES*8-1:0] fp8_bytes;

    genvar gi;
    generate
        for (gi = 0; gi < NUM_ELEMS; gi++) begin : gen_enc
            fp8_encode u_enc (
                .fp32 (tile_buf[gi*32 +: 32]),
                .fp8  (fp8_bytes[gi*8 +: 8])
            );
        end
    endgenerate

    // ------------------------------------------------------------------
    // Pack bytes_buf at S_FORMAT based on dtype.
    //   fp32 path: copy tile_buf into bytes_buf verbatim (LE).
    //   fp8  path: copy fp8_bytes into the low NUM_ELEMS*8 bits of bytes_buf.
    // ------------------------------------------------------------------
    function automatic logic [MAX_BYTES*8-1:0] pack_bytes(
        input logic [MMA_M*MMA_N*32-1:0] tile,
        input logic [FP8_BYTES*8-1:0]    encoded,
        input logic                       d
    );
        logic [MAX_BYTES*8-1:0] out;
        int idx;
        begin
            /* verilator lint_off WIDTHCONCAT */
            out = '0;
            /* verilator lint_on WIDTHCONCAT */
            if (d == 1'b0) begin
                // fp32: low NUM_ELEMS*32 bits copy the tile verbatim.
                for (idx = 0; idx < NUM_ELEMS; idx++) begin
                    out[idx*32 +: 32] = tile[idx*32 +: 32];
                end
            end else begin
                // fp8: low NUM_ELEMS*8 bits = encoded[*].
                for (idx = 0; idx < NUM_ELEMS; idx++) begin
                    out[idx*8 +: 8] = encoded[idx*8 +: 8];
                end
            end
            return out;
        end
    endfunction

    // ------------------------------------------------------------------
    // Sequential logic.
    // ------------------------------------------------------------------
    integer rj;
    always_ff @(posedge clk) begin
        if (reset) begin
            state          <= S_IDLE;
            busy           <= 1'b0;
            done           <= 1'b0;
            drain_issue    <= 1'b0;
            drain_slot     <= 32'd0;
            wr_en          <= 1'b0;
            wr_addr        <= 32'd0;
            wr_data        <= '0;
            saved_gmem_ptr <= 32'd0;
            saved_dtype    <= 1'b0;
            /* verilator lint_off WIDTHCONCAT */
            tile_buf       <= '0;
            bytes_buf      <= '0;
            /* verilator lint_on WIDTHCONCAT */
            total_bytes    <= 32'd0;
            bytes_written  <= 32'd0;
            gather_done    <= 1'b0;
        end else begin
            done          <= 1'b0;
            drain_issue   <= 1'b0;
            drain_slot    <= 32'd0;
            wr_en         <= 1'b0;
            wr_addr       <= 32'd0;
            wr_data       <= '0;

            unique case (state)
                S_IDLE: begin
                    if (issue_en) begin
                        saved_gmem_ptr <= gmem_ptr;
                        saved_dtype    <= dtype;
                        drain_issue    <= 1'b1;
                        drain_slot     <= tmem_slot;
                        total_bytes    <= dtype ? FP8_BYTES : TILE_BYTES;
                        bytes_written  <= 32'd0;
                        gather_done    <= 1'b0;
                        busy           <= 1'b1;
                        state          <= S_GATHER;
                    end
                end

                S_GATHER: begin
                    // Gather rows as they arrive. compute_array drives
                    // drain_row_valid for MMA_M consecutive cycles after
                    // the issue (starting at issue+2).
                    if (drain_row_valid) begin
                        // Write the row into tile_buf at the indicated row.
                        // Element [i][j] sits at bit (i*MMA_N+j)*32; one row
                        // is MMA_N*32 contiguous bits starting at
                        // (row_idx*MMA_N)*32.
                        for (rj = 0; rj < MMA_N; rj++) begin
                            tile_buf[(drain_row_idx*MMA_N + rj)*32 +: 32]
                                <= drain_row_data[rj*32 +: 32];
                        end
                        if (drain_last) begin
                            gather_done <= 1'b1;
                        end
                    end
                    // Transition out of GATHER once the final row's NBA has
                    // committed. drain_last is registered with the same
                    // edge as the final row data, so we move to FORMAT one
                    // cycle later (when gather_done has been latched high).
                    if (gather_done) begin
                        state <= S_FORMAT;
                    end
                end

                S_FORMAT: begin
                    // One-cycle re-pack: tile_buf -> bytes_buf per dtype.
                    bytes_buf <= pack_bytes(tile_buf, fp8_bytes, saved_dtype);
                    state     <= S_DRAIN;
                end

                S_DRAIN: begin
                    wr_en   <= 1'b1;
                    wr_addr <= saved_gmem_ptr + bytes_written;
                    wr_data <= bytes_buf[bytes_written*8 +: BEAT_BYTES*8];

                    if (bytes_written + BEAT_BYTES >= total_bytes) begin
                        done          <= 1'b1;
                        busy          <= 1'b0;
                        state         <= S_IDLE;
                        bytes_written <= 32'd0;
                        gather_done   <= 1'b0;
                    end else begin
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
