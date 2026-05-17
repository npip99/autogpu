// cmdproc.sv — instruction FIFO + decoder + dispatcher.
//
// SV implementation of pymodel.cmdproc.CmdProc. See pymodel/cmdproc.py for the
// canonical spec. The contract-level signals (engine start pulses, barrier
// init/query, idle) must match the pymodel for the same program; exact cycle
// counts differ because RTL handshakes between engines go through registered
// crossings while the pymodel uses back-door access (see DEVELOPMENT.md
// §"Cross-module registered-handoff latency").
//
// INSTRUCTION ENCODING (packed bus, INSTR_WIDTH bits — see localparam below)
//
//   Bit range          Width  Field
//   -----------------  -----  ---------------------------------------------
//   [  2:  0]            3    op  (OP_BAR_INIT=0, OP_LOAD=1, OP_MMA=2,
//                                  OP_STORE=3, OP_WAIT=4)
//   [ 10:  3]            8    bar_id
//   [ 26: 11]           16    count          (BAR_INIT)
//   [ 58: 27]           32    gmem_ptr       (LOAD / STORE)
//   [ 90: 59]           32    smem_ptr       (LOAD)
//   [122: 91]           32    bytes_n        (LOAD)
//   [154:123]           32    a_smem_offset  (MMA)
//   [186:155]           32    b_smem_offset  (MMA)
//   [194:187]            8    d_tmem_slot    (MMA)
//   [195:195]            1    accum          (MMA)
//   [203:196]            8    tmem_slot      (STORE)
//   [204:204]            1    dtype          (STORE)
//   [205:205]            1    expected_phase (WAIT)
//
//   INSTR_WIDTH = 206 (rounded to 224 for byte alignment).
//
// FSM
//   IDLE                    — pop instruction (if FIFO non-empty), dispatch.
//   WAITING_FOR_WAIT_DONE   — drive query, wait for barrier.wait_done.
//   WAITING_FOR_STORE_DONE  — hold store_issue_en, wait for store.done.
//
// PULSE / HOLD POLICY
//   - init_en, mma_start, load_issue_en, store_issue_en : 1-cycle pulses on
//     dispatch. (store.sv latches issue_en in S_IDLE and ignores it while
//     busy; pymodel.cmdproc also pulses store_issue_en for one cycle. The
//     task brief mentioned "HOLD store_issue_en until store.done" but that
//     causes a re-issue race when store_done lands: cmdproc still drives
//     store_issue_en=1 during the cycle after store returns to S_IDLE,
//     making store re-fire. Pulsing matches pymodel and works correctly.)
//   - query_bar_id / query_expected_phase : combinational while in WAIT state.
//
// IDLE OUTPUT
//   idle = (state == IDLE) && (fifo_count == 0). This is the cmdproc-local
//   idle the host can poll; the sim-level idle additionally requires all
//   engines busy=0, which the surrounding TB checks.

module cmdproc #(
    parameter int INSTR_FIFO_DEPTH = 256,
    parameter int NUM_BARRIERS     = 8
) (
    input  logic                       clk,
    input  logic                       reset,

    // Instruction push interface (TB / host side).
    input  logic                       push_en,
    input  logic [223:0]               push_instr,    // packed; see header

    // Engine completion signals (registered, observed 1 cycle after engine drives).
    input  logic                       load_busy,
    input  logic                       load_done,
    input  logic                       load_accept,
    input  logic                       mma_busy,
    input  logic                       mma_done,
    input  logic                       store_busy,
    input  logic                       store_done,

    // Barrier wait-query response (combinational).
    input  logic                       barrier_wait_done,

    // To barrier (INIT + WAIT_QUERY).
    output logic                       init_en,
    output logic [31:0]                init_bar_id,
    output logic [15:0]                init_count,
    output logic [31:0]                query_bar_id,
    output logic                       query_expected_phase,

    // To MMA engine.
    output logic                       mma_start,
    output logic [31:0]                mma_a_smem_offset,
    output logic [31:0]                mma_b_smem_offset,
    output logic [31:0]                mma_d_tmem_slot,
    output logic                       mma_accum,
    output logic [31:0]                mma_bar_id,

    // To LOAD engine.
    output logic                       load_issue_en,
    output logic [31:0]                load_gmem_ptr,
    output logic [31:0]                load_smem_ptr,
    output logic [31:0]                load_bytes_n,
    output logic [31:0]                load_bar_id,

    // To STORE engine.
    output logic                       store_issue_en,
    output logic [31:0]                store_tmem_slot,
    output logic [31:0]                store_gmem_ptr,
    output logic                       store_dtype,

    // Status.
    output logic                       idle
);

    // ------------------------------------------------------------------
    // Opcodes (must match config.py).
    // ------------------------------------------------------------------
    localparam logic [2:0] OP_BAR_INIT = 3'd0;
    localparam logic [2:0] OP_LOAD     = 3'd1;
    localparam logic [2:0] OP_MMA      = 3'd2;
    localparam logic [2:0] OP_STORE    = 3'd3;
    localparam logic [2:0] OP_WAIT     = 3'd4;

    // ------------------------------------------------------------------
    // FSM states.
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        S_IDLE                   = 2'd0,
        S_WAITING_FOR_WAIT_DONE  = 2'd1,
        S_WAITING_FOR_STORE_DONE = 2'd2
    } state_t;
    state_t state;

    // Latched WAIT operands.
    logic [31:0] wait_bar_id_r;
    logic        wait_expected_phase_r;

    // ------------------------------------------------------------------
    // Instruction FIFO (ring buffer).
    // ------------------------------------------------------------------
    logic [223:0] fifo_mem [INSTR_FIFO_DEPTH];

    localparam int FIFO_IDX_W = (INSTR_FIFO_DEPTH <= 1) ? 1 : $clog2(INSTR_FIFO_DEPTH);
    localparam int FIFO_CNT_W = FIFO_IDX_W + 1;

    logic [FIFO_IDX_W-1:0] fifo_head;
    logic [FIFO_IDX_W-1:0] fifo_tail;
    logic [FIFO_CNT_W-1:0] fifo_count;

    initial begin
        for (int i = 0; i < INSTR_FIFO_DEPTH; i++) begin
            fifo_mem[i] = 224'd0;
        end
    end

    // ------------------------------------------------------------------
    // Decode helpers (combinational, on a 224-bit instruction word).
    // ------------------------------------------------------------------
    function automatic logic [2:0]  dec_op            (input logic [223:0] w); return w[  2:  0]; endfunction
    function automatic logic [7:0]  dec_bar_id        (input logic [223:0] w); return w[ 10:  3]; endfunction
    function automatic logic [15:0] dec_count         (input logic [223:0] w); return w[ 26: 11]; endfunction
    function automatic logic [31:0] dec_gmem_ptr      (input logic [223:0] w); return w[ 58: 27]; endfunction
    function automatic logic [31:0] dec_smem_ptr      (input logic [223:0] w); return w[ 90: 59]; endfunction
    function automatic logic [31:0] dec_bytes_n       (input logic [223:0] w); return w[122: 91]; endfunction
    function automatic logic [31:0] dec_a_smem_offset (input logic [223:0] w); return w[154:123]; endfunction
    function automatic logic [31:0] dec_b_smem_offset (input logic [223:0] w); return w[186:155]; endfunction
    function automatic logic [7:0]  dec_d_tmem_slot   (input logic [223:0] w); return w[194:187]; endfunction
    function automatic logic        dec_accum         (input logic [223:0] w); return w[195];     endfunction
    function automatic logic [7:0]  dec_tmem_slot     (input logic [223:0] w); return w[203:196]; endfunction
    function automatic logic        dec_dtype         (input logic [223:0] w); return w[204];     endfunction
    function automatic logic        dec_expected_phase(input logic [223:0] w); return w[205];     endfunction

    // ------------------------------------------------------------------
    // Combinational outputs that depend on state (query path).
    // ------------------------------------------------------------------
    // query_* are driven combinationally while in WAITING_FOR_WAIT_DONE so the
    // barrier sees them this cycle and replies on its combinational wait_done.
    always_comb begin
        if (state == S_WAITING_FOR_WAIT_DONE) begin
            query_bar_id         = wait_bar_id_r;
            query_expected_phase = wait_expected_phase_r;
        end else begin
            query_bar_id         = 32'd0;
            query_expected_phase = 1'b0;
        end
    end

    // ------------------------------------------------------------------
    // idle output: state==IDLE AND FIFO empty. Engine-busy is folded in
    // by the TB / top-level (cmdproc cannot observe engines combinationally;
    // it sees their registered busy/done signals 1 cycle late).
    // ------------------------------------------------------------------
    assign idle = (state == S_IDLE) && (fifo_count == 0);

    // ------------------------------------------------------------------
    // Sequential logic.
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (reset) begin
            state                  <= S_IDLE;
            fifo_head              <= '0;
            fifo_tail              <= '0;
            fifo_count             <= '0;
            wait_bar_id_r          <= 32'd0;
            wait_expected_phase_r  <= 1'b0;

            init_en                <= 1'b0;
            init_bar_id            <= 32'd0;
            init_count             <= 16'd0;

            mma_start              <= 1'b0;
            mma_a_smem_offset      <= 32'd0;
            mma_b_smem_offset      <= 32'd0;
            mma_d_tmem_slot        <= 32'd0;
            mma_accum              <= 1'b0;
            mma_bar_id             <= 32'd0;

            load_issue_en          <= 1'b0;
            load_gmem_ptr          <= 32'd0;
            load_smem_ptr          <= 32'd0;
            load_bytes_n           <= 32'd0;
            load_bar_id            <= 32'd0;

            store_issue_en         <= 1'b0;
            store_tmem_slot        <= 32'd0;
            store_gmem_ptr         <= 32'd0;
            store_dtype            <= 1'b0;
        end else begin
            // ---------- Next-state scratch ----------
            automatic state_t                  n_state    = state;
            automatic logic [FIFO_IDX_W-1:0]   n_head     = fifo_head;
            automatic logic [FIFO_IDX_W-1:0]   n_tail     = fifo_tail;
            automatic logic [FIFO_CNT_W-1:0]   n_count    = fifo_count;
            automatic logic [31:0]             n_wbar     = wait_bar_id_r;
            automatic logic                    n_wphase   = wait_expected_phase_r;

            // Default pulses: clear every cycle (registered).
            automatic logic                    o_init_en        = 1'b0;
            automatic logic [31:0]             o_init_bar_id    = 32'd0;
            automatic logic [15:0]             o_init_count     = 16'd0;

            automatic logic                    o_mma_start      = 1'b0;
            automatic logic [31:0]             o_mma_a          = 32'd0;
            automatic logic [31:0]             o_mma_b          = 32'd0;
            automatic logic [31:0]             o_mma_d          = 32'd0;
            automatic logic                    o_mma_accum      = 1'b0;
            automatic logic [31:0]             o_mma_bar        = 32'd0;

            automatic logic                    o_load_en        = 1'b0;
            automatic logic [31:0]             o_load_g         = 32'd0;
            automatic logic [31:0]             o_load_s         = 32'd0;
            automatic logic [31:0]             o_load_b         = 32'd0;
            automatic logic [31:0]             o_load_bar       = 32'd0;

            automatic logic                    o_store_en       = 1'b0;
            automatic logic [31:0]             o_store_slot     = 32'd0;
            automatic logic [31:0]             o_store_gptr     = 32'd0;
            automatic logic                    o_store_dt       = 1'b0;

            // 1. Push from TB. Always accept (FIFO assumed not full).
            if (push_en) begin
                fifo_mem[n_tail] <= push_instr;
                n_tail  = n_tail + 1'b1;
                n_count = n_count + 1'b1;
            end

            // 2. Handle wait release (combinational query is live; check
            //    barrier_wait_done in current state).
            if (n_state == S_WAITING_FOR_WAIT_DONE) begin
                if (barrier_wait_done) begin
                    n_state = S_IDLE;
                end
            end

            // 3. Handle STORE completion. We DON'T re-drive store_issue_en
            //    while waiting — store latches issue_en in its S_IDLE state
            //    and ignores it once busy; pymodel pulses for one cycle too.
            if (n_state == S_WAITING_FOR_STORE_DONE) begin
                if (store_done) begin
                    n_state = S_IDLE;
                end
            end

            // 4. Dispatch next instruction if IDLE and FIFO non-empty.
            //    Reading fifo_mem on the SAME cycle as a push (NBA write)
            //    returns the OLD value, so a same-cycle push into an empty
            //    FIFO must dispatch from push_instr directly.
            if (n_state == S_IDLE && n_count != 0) begin
                automatic logic [223:0] instr;
                automatic logic         take_from_push;
                take_from_push = push_en && (fifo_count == 0);
                if (take_from_push) begin
                    instr = push_instr;
                end else begin
                    instr = fifo_mem[fifo_head];
                end
                n_head  = n_head + 1'b1;
                n_count = n_count - 1'b1;

                unique case (dec_op(instr))
                    OP_BAR_INIT: begin
                        o_init_en     = 1'b1;
                        o_init_bar_id = {24'd0, dec_bar_id(instr)};
                        o_init_count  = dec_count(instr);
                    end
                    OP_LOAD: begin
                        o_load_en  = 1'b1;
                        o_load_g   = dec_gmem_ptr(instr);
                        o_load_s   = dec_smem_ptr(instr);
                        o_load_b   = dec_bytes_n(instr);
                        o_load_bar = {24'd0, dec_bar_id(instr)};
                    end
                    OP_MMA: begin
                        o_mma_start = 1'b1;
                        o_mma_a     = dec_a_smem_offset(instr);
                        o_mma_b     = dec_b_smem_offset(instr);
                        o_mma_d     = {24'd0, dec_d_tmem_slot(instr)};
                        o_mma_accum = dec_accum(instr);
                        o_mma_bar   = {24'd0, dec_bar_id(instr)};
                    end
                    OP_STORE: begin
                        o_store_en   = 1'b1;
                        o_store_slot = {24'd0, dec_tmem_slot(instr)};
                        o_store_gptr = dec_gmem_ptr(instr);
                        o_store_dt   = dec_dtype(instr);
                        n_state      = S_WAITING_FOR_STORE_DONE;
                    end
                    OP_WAIT: begin
                        n_wbar   = {24'd0, dec_bar_id(instr)};
                        n_wphase = dec_expected_phase(instr);
                        n_state  = S_WAITING_FOR_WAIT_DONE;
                    end
                    default: begin
                        // Unknown opcode: leave state unchanged.
                    end
                endcase
            end

            // ---------- Commit ----------
            state                 <= n_state;
            fifo_head             <= n_head;
            fifo_tail             <= n_tail;
            fifo_count            <= n_count;
            wait_bar_id_r         <= n_wbar;
            wait_expected_phase_r <= n_wphase;

            init_en               <= o_init_en;
            init_bar_id           <= o_init_bar_id;
            init_count            <= o_init_count;

            mma_start             <= o_mma_start;
            mma_a_smem_offset     <= o_mma_a;
            mma_b_smem_offset     <= o_mma_b;
            mma_d_tmem_slot       <= o_mma_d;
            mma_accum             <= o_mma_accum;
            mma_bar_id            <= o_mma_bar;

            load_issue_en         <= o_load_en;
            load_gmem_ptr         <= o_load_g;
            load_smem_ptr         <= o_load_s;
            load_bytes_n          <= o_load_b;
            load_bar_id           <= o_load_bar;

            store_issue_en        <= o_store_en;
            store_tmem_slot       <= o_store_slot;
            store_gmem_ptr        <= o_store_gptr;
            store_dtype           <= o_store_dt;
        end
    end

endmodule
