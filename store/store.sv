// store.sv -- STORE engine: drains a TMEM slot to GMEM.
//
// SV implementation of pymodel.store.Store. See pymodel/store.py for the
// canonical spec; this module must match it cycle-by-cycle.
//
// SYNC MODEL
//   STORE is synchronous in v1: cmdproc holds issue_en until done pulses.
//   We ignore re-issues while busy. (Cmdproc is responsible for not issuing
//   another STORE while this one is busy, but we defensively ignore.)
//
// PIPELINE
//   Cycle 0 (busy=0, issue_en=1):
//     - latch saved (slot, gmem_ptr, dtype)
//     - drive tmem.store_rd_en + slot
//     - busy <= 1, state <= WAIT_RD
//   Cycle 1 (state=WAIT_RD):
//     - tmem.store_rd_valid==1 in this cycle
//     - capture tmem.store_rd_tile into tile_buf
//     - (if dtype==1) encode each fp32 element to fp8 byte via the
//       parallel `fp8_encode` array (combinational) -> bytes_buf
//       (if dtype==0) bytes_buf = tile_buf bit-pattern verbatim (LE)
//     - bytes_written <= 0, state <= DRAIN
//   Cycle 2..K (state=DRAIN, K = total_bytes / BEAT_BYTES):
//     - gmem.wr_en = 1, wr_addr = gmem_ptr + bytes_written
//     - wr_data = bytes_buf[bytes_written*8 +: BEAT_BYTES*8]
//     - bytes_written <= bytes_written + BEAT_BYTES
//     - when bytes_written + BEAT_BYTES == total_bytes:
//         done <= 1, busy <= 0, state <= IDLE
//
// FP8 ENCODE (dtype=1):
//   Combinational: see common/fp8_encode.sv. The 1024 (MMA_M x MMA_N)
//   encoders run in parallel on the captured tile bits.

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

    // From TMEM STORE_RD port
    input  logic [MMA_M*MMA_N*32-1:0]     store_rd_tile,
    input  logic                          store_rd_valid,

    // To TMEM STORE_RD port
    output logic                          store_rd_en,
    output logic [31:0]                   store_rd_slot,

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
        S_WAIT_RD = 2'd1,
        S_DRAIN  = 2'd2
    } state_t;

    state_t state;

    // Latched operands.
    logic [31:0] saved_gmem_ptr;
    logic        saved_dtype;

    // Drain buffer (sized to max output: TILE_BYTES bytes).
    /* verilator lint_off WIDTHCONCAT */
    logic [MAX_BYTES*8-1:0] bytes_buf;
    /* verilator lint_on WIDTHCONCAT */

    logic [31:0] total_bytes;
    logic [31:0] bytes_written;

    // ------------------------------------------------------------------
    // Combinational fp8-encode array. Inputs are the tile bits from
    // the TMEM read port (store_rd_tile), one fp32 word per element.
    // Outputs are concatenated into `fp8_bytes` (LSB-first, one byte
    // per element).
    // ------------------------------------------------------------------
    logic [FP8_BYTES*8-1:0] fp8_bytes;

    genvar gi;
    generate
        for (gi = 0; gi < NUM_ELEMS; gi++) begin : gen_enc
            fp8_encode u_enc (
                .fp32 (store_rd_tile[gi*32 +: 32]),
                .fp8  (fp8_bytes[gi*8 +: 8])
            );
        end
    endgenerate

    // ------------------------------------------------------------------
    // Pack bytes_buf at S_WAIT_RD based on dtype.
    //   fp32 path: copy store_rd_tile into bytes_buf verbatim (LE).
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
    always_ff @(posedge clk) begin
        if (reset) begin
            state          <= S_IDLE;
            busy           <= 1'b0;
            done           <= 1'b0;
            store_rd_en    <= 1'b0;
            store_rd_slot  <= 32'd0;
            wr_en          <= 1'b0;
            wr_addr        <= 32'd0;
            wr_data        <= '0;
            saved_gmem_ptr <= 32'd0;
            saved_dtype    <= 1'b0;
            /* verilator lint_off WIDTHCONCAT */
            bytes_buf      <= '0;
            /* verilator lint_on WIDTHCONCAT */
            total_bytes    <= 32'd0;
            bytes_written  <= 32'd0;
        end else begin
            done          <= 1'b0;
            store_rd_en   <= 1'b0;
            store_rd_slot <= 32'd0;
            wr_en         <= 1'b0;
            wr_addr       <= 32'd0;
            wr_data       <= '0;

            unique case (state)
                S_IDLE: begin
                    if (issue_en) begin
                        saved_gmem_ptr <= gmem_ptr;
                        saved_dtype    <= dtype;
                        store_rd_en    <= 1'b1;
                        store_rd_slot  <= tmem_slot;
                        total_bytes    <= dtype ? FP8_BYTES : TILE_BYTES;
                        bytes_written  <= 32'd0;
                        busy           <= 1'b1;
                        state          <= S_WAIT_RD;
                    end
                end

                S_WAIT_RD: begin
                    if (store_rd_valid) begin
                        bytes_buf <= pack_bytes(store_rd_tile, fp8_bytes, saved_dtype);
                        state     <= S_DRAIN;
                    end
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
