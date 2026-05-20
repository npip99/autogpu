// cmdproc.sv — instruction memory + decoder + dispatcher with ALU & control flow.
//
// SV implementation of pymodel.cmdproc.CmdProc. See pymodel/cmdproc.py for the
// canonical spec. Contract-level signals (engine start pulses, barrier
// init/query, idle) match the pymodel for the same program; exact cycle
// counts differ because cross-module handoffs are registered (see
// DEVELOPMENT.md §"Cross-module registered-handoff latency").
//
// ============================================================================
// ISA (15 opcodes — must match pymodel.cmdproc)
// ============================================================================
//   Engine ops:   BAR_INIT (0x00), LOAD (0x01), MMA (0x02), STORE (0x03),
//                 WAIT (0x04)
//   Control flow: REPEAT (0x05), END (0x06), BRZ (0x0C), BRNZ (0x0D),
//                 JMP (0x0E)
//   ALU:          SET_REG (0x07), ADD (0x08), ADDI (0x09), SUB (0x0A),
//                 AND (0x0B)
//
// Opcodes 0x00..0x06 are mirrored in config.py. 0x07..0x0E are SV-internal
// (and the cocotb TB defines matching Python constants for packing).
//
// ============================================================================
// REGISTER FILE (8 general-purpose 32-bit registers)
// ============================================================================
//   regs[0..7] — initialized to 0 on reset.
//   ALU ops write back combinationally during tick, registered on next edge.
//   A dependent read (e.g. ADDI r1,r1,1 ; ADDI r0,r1,0) sees the OLD r1 on
//   the SAME cycle as the write — that is intentional and matches pymodel's
//   per-tick semantics (each cycle dispatches at most one instruction).
//
// ============================================================================
// LOOP STACK
// ============================================================================
//   Depth NUM_LOOP_STACK (4). Each frame = (body_start_pc, iter_max, parent_iter).
//   iter_reg tracks innermost loop's counter. REPEAT pushes a frame and sets
//   iter_reg=0; END increments iter_reg, jumps back if iter_reg<iter_max
//   else pops the frame and restores parent_iter.
//   REPEAT 0 → scan-forward to matching END (state S_SCANNING_END).
//
// ============================================================================
// OPERAND VARIANTS (each address operand carries a 2-bit "mode" field)
// ============================================================================
//   MODE_IMM       (0)  — literal base
//   MODE_ITER_ADDR (1)  — base + iter_reg * stride
//   MODE_REG_REF   (2)  — regs[reg_idx]
//   MODE_REG_OFF   (3)  — base + regs[reg_idx]
//
//   MMA accum has a 1-bit mode: 0=IMM (use accum_imm), 1=ITER_NONZERO
//   (resolves to 1 if iter_reg!=0 else 0).
//
// ============================================================================
// INSTRUCTION ENCODING (256-bit packed bus)
// ============================================================================
//   Bit range       Width  Field
//   --------------  -----  -----------------------------------------------------
//   [  7:  0]         8    op
//   [ 15:  8]         8    byte1   = bar_id (engine ops) | rd (ALU) | tmem_slot (STORE)
//   [ 23: 16]         8    byte2   = a_reg / ra / reg (used as low operand-reg idx)
//   [ 31: 24]         8    byte3   = b_reg / rb
//   [ 33: 32]         2    a_mode  = mode for LOAD.gmem / MMA.a_smem / STORE.gmem
//   [ 35: 34]         2    b_mode  = mode for LOAD.smem / MMA.b_smem
//   [ 36: 36]         1    accum_mode (MMA: 0=imm, 1=iter_nonzero)
//   [ 37: 37]         1    dtype / expected_phase / accum_imm bit
//   [ 39: 38]         2    pad
//   [ 47: 40]         8    d_tmem_slot (MMA) / spare
//   [ 79: 48]        32    field0 — generic 32-bit immediate
//                          BAR_INIT: count (low 16 used)
//                          LOAD/MMA: a/gmem base
//                          STORE:    gmem base
//                          WAIT:     expected_phase (low bit)
//                          REPEAT:   count
//                          SET_REG:  value
//                          ADDI:     imm (signed)
//                          BRZ/BRNZ/JMP: offset (signed)
//   [111: 80]        32    field1 — a/gmem stride (LOAD/MMA/STORE)
//   [143:112]        32    field2 — smem/b base (LOAD/MMA)
//   [175:144]        32    field3 — smem/b stride (LOAD/MMA)
//   [207:176]        32    field4 — bytes_n (LOAD)
//   [255:208]        48    pad
//
//   INSTR_WIDTH = 256.
//
// ============================================================================
// FSM
// ============================================================================
//   S_IDLE                    — dispatch instr at pc (if pc < imem_len).
//   S_WAITING_FOR_WAIT_DONE   — drive query, wait for barrier_wait_done.
//   S_WAITING_FOR_STORE_DONE  — wait for store_done.
//   S_SCANNING_END            — scan imem forward to matching END for REPEAT 0.
//
// PULSE / HOLD POLICY: identical to prior cmdproc — engine drives pulse for
// one cycle. query_* drives combinationally during S_WAITING_FOR_WAIT_DONE.

module cmdproc #(
    parameter int INSTR_FIFO_DEPTH = 64,     // also used as imem capacity
    parameter int NUM_LOOP_STACK   = 4
) (
    input  logic                       clk,
    input  logic                       reset,

    // Instruction push interface (TB / host side).
    input  logic                       push_en,
    input  logic [255:0]               push_instr,    // packed; see header

    // Engine completion signals (registered, observed 1 cycle late).
    // Currently advisory only: cmdproc uses barrier_wait_done and the
    // S_WAITING_FOR_STORE_DONE FSM hop on store_done; the other status
    // pins exist for future refinement (e.g., ALU-driven busy-polling)
    // and are wired through but not consumed today.
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic                       load_busy,
    input  logic                       load_done,
    input  logic                       load_accept,
    input  logic                       mma_busy,
    input  logic                       mma_done,
    input  logic                       store_busy,
    /* verilator lint_on UNUSEDSIGNAL */
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
    // Opcodes (0x00..0x06 must match config.py).
    // ------------------------------------------------------------------
    localparam logic [7:0] OP_BAR_INIT = 8'h00;
    localparam logic [7:0] OP_LOAD     = 8'h01;
    localparam logic [7:0] OP_MMA      = 8'h02;
    localparam logic [7:0] OP_STORE    = 8'h03;
    localparam logic [7:0] OP_WAIT     = 8'h04;
    localparam logic [7:0] OP_REPEAT   = 8'h05;
    localparam logic [7:0] OP_END      = 8'h06;
    localparam logic [7:0] OP_SET_REG  = 8'h07;
    localparam logic [7:0] OP_ADD      = 8'h08;
    localparam logic [7:0] OP_ADDI     = 8'h09;
    localparam logic [7:0] OP_SUB      = 8'h0A;
    localparam logic [7:0] OP_AND      = 8'h0B;
    localparam logic [7:0] OP_BRZ      = 8'h0C;
    localparam logic [7:0] OP_BRNZ     = 8'h0D;
    localparam logic [7:0] OP_JMP      = 8'h0E;

    // Operand modes.
    localparam logic [1:0] MODE_IMM       = 2'd0;
    localparam logic [1:0] MODE_ITER_ADDR = 2'd1;
    localparam logic [1:0] MODE_REG_REF   = 2'd2;
    localparam logic [1:0] MODE_REG_OFF   = 2'd3;

    // ------------------------------------------------------------------
    // FSM states.
    // ------------------------------------------------------------------
    typedef enum logic [1:0] {
        S_IDLE                   = 2'd0,
        S_WAITING_FOR_WAIT_DONE  = 2'd1,
        S_WAITING_FOR_STORE_DONE = 2'd2,
        S_SCANNING_END           = 2'd3
    } state_t;
    state_t state;

    // Latched WAIT operands.
    logic [31:0] wait_bar_id_r;
    logic        wait_expected_phase_r;

    // Latched REPEAT-0 scan state.
    logic [15:0] scan_depth_r;   // nesting depth; finished when reaches 0

    // ------------------------------------------------------------------
    // Instruction memory (random-access, indexed by pc).
    // ------------------------------------------------------------------
    logic [255:0] imem [INSTR_FIFO_DEPTH];

    localparam int PC_W = (INSTR_FIFO_DEPTH <= 1) ? 1 : $clog2(INSTR_FIFO_DEPTH);
    localparam int LEN_W = PC_W + 1;

    logic [LEN_W-1:0] pc;         // program counter
    logic [LEN_W-1:0] imem_len;   // number of valid instructions (write index)

    initial begin
        for (int i = 0; i < INSTR_FIFO_DEPTH; i++) begin
            imem[i] = 256'd0;
        end
    end

    // ------------------------------------------------------------------
    // Register file.
    // ------------------------------------------------------------------
    logic [31:0] regs [0:7];

    // ------------------------------------------------------------------
    // Loop stack.
    // ------------------------------------------------------------------
    logic [LEN_W-1:0] ls_body_start  [0:NUM_LOOP_STACK-1];
    logic [31:0]      ls_iter_max    [0:NUM_LOOP_STACK-1];
    logic [31:0]      ls_parent_iter [0:NUM_LOOP_STACK-1];
    logic [$clog2(NUM_LOOP_STACK+1)-1:0] loop_depth;  // 0..NUM_LOOP_STACK
    logic [31:0]      iter_reg;

    // ------------------------------------------------------------------
    // Decode helpers (combinational, on a 256-bit instruction word).
    // Each helper extracts one field; the unused upper bits are intentional
    // (the encoding leaves room for future fields and 48 bits of pad).
    // ------------------------------------------------------------------
    /* verilator lint_off UNUSEDSIGNAL */
    function automatic logic [7:0]  dec_op            (input logic [255:0] w); return w[  7:  0]; endfunction
    function automatic logic [7:0]  dec_byte1         (input logic [255:0] w); return w[ 15:  8]; endfunction
    function automatic logic [7:0]  dec_byte2         (input logic [255:0] w); return w[ 23: 16]; endfunction
    function automatic logic [7:0]  dec_byte3         (input logic [255:0] w); return w[ 31: 24]; endfunction
    function automatic logic [1:0]  dec_a_mode        (input logic [255:0] w); return w[ 33: 32]; endfunction
    function automatic logic [1:0]  dec_b_mode        (input logic [255:0] w); return w[ 35: 34]; endfunction
    function automatic logic        dec_accum_mode    (input logic [255:0] w); return w[36];      endfunction
    function automatic logic        dec_flag1         (input logic [255:0] w); return w[37];      endfunction
    function automatic logic [7:0]  dec_d_tmem_slot   (input logic [255:0] w); return w[ 47: 40]; endfunction
    function automatic logic [31:0] dec_field0        (input logic [255:0] w); return w[ 79: 48]; endfunction
    function automatic logic [31:0] dec_field1        (input logic [255:0] w); return w[111: 80]; endfunction
    function automatic logic [31:0] dec_field2        (input logic [255:0] w); return w[143:112]; endfunction
    function automatic logic [31:0] dec_field3        (input logic [255:0] w); return w[175:144]; endfunction
    function automatic logic [31:0] dec_field4        (input logic [255:0] w); return w[207:176]; endfunction

    // Pre-sliced subfields. Avoid `dec_byteN(w)[2:0]` etc. at call sites:
    // Yosys's Verilog-2005 frontend rejects part-select on function-call return
    // (legal SV-2012, but breaks the sky130 synth flow). Pre-slice here instead.
    function automatic logic [2:0]  dec_reg_d         (input logic [255:0] w); return w[ 10:  8]; endfunction
    function automatic logic [2:0]  dec_reg_a         (input logic [255:0] w); return w[ 18: 16]; endfunction
    function automatic logic [2:0]  dec_reg_b         (input logic [255:0] w); return w[ 26: 24]; endfunction
    function automatic logic [15:0] dec_imm16         (input logic [255:0] w); return w[ 63: 48]; endfunction
    function automatic logic        dec_phase         (input logic [255:0] w); return w[48];      endfunction
    /* verilator lint_on UNUSEDSIGNAL */

    // ------------------------------------------------------------------
    // Operand resolution helpers (combinational).
    // ------------------------------------------------------------------
    function automatic logic [31:0] resolve_addr(
        input logic [1:0]  mode,
        input logic [31:0] base,
        input logic [31:0] stride,
        input logic [2:0]  reg_idx,
        input logic [31:0] iter_r,
        input logic [31:0] r0, input logic [31:0] r1,
        input logic [31:0] r2, input logic [31:0] r3,
        input logic [31:0] r4, input logic [31:0] r5,
        input logic [31:0] r6, input logic [31:0] r7
    );
        logic [31:0] reg_val;
        case (reg_idx)
            3'd0: reg_val = r0;
            3'd1: reg_val = r1;
            3'd2: reg_val = r2;
            3'd3: reg_val = r3;
            3'd4: reg_val = r4;
            3'd5: reg_val = r5;
            3'd6: reg_val = r6;
            3'd7: reg_val = r7;
        endcase
        case (mode)
            MODE_IMM:       resolve_addr = base;
            MODE_ITER_ADDR: resolve_addr = base + iter_r * stride;
            MODE_REG_REF:   resolve_addr = reg_val;
            MODE_REG_OFF:   resolve_addr = base + reg_val;
            default:        resolve_addr = base;
        endcase
    endfunction

    // ------------------------------------------------------------------
    // Combinational outputs that depend on state (query path).
    // ------------------------------------------------------------------
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
    // idle output: state==IDLE AND pc has reached imem_len.
    // ------------------------------------------------------------------
    assign idle = (state == S_IDLE) && (pc >= imem_len);

    // ------------------------------------------------------------------
    // Sequential logic.
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (reset) begin
            state                  <= S_IDLE;
            pc                     <= '0;
            imem_len               <= '0;
            wait_bar_id_r          <= 32'd0;
            wait_expected_phase_r  <= 1'b0;
            scan_depth_r           <= 16'd0;

            iter_reg               <= 32'd0;
            loop_depth             <= '0;
            for (int i = 0; i < 8; i++) begin
                regs[i] <= 32'd0;
            end
            for (int i = 0; i < NUM_LOOP_STACK; i++) begin
                ls_body_start[i]  <= '0;
                ls_iter_max[i]    <= 32'd0;
                ls_parent_iter[i] <= 32'd0;
            end

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
            automatic logic [LEN_W-1:0]        n_pc       = pc;
            automatic logic [LEN_W-1:0]        n_imem_len = imem_len;
            automatic logic [31:0]             n_wbar     = wait_bar_id_r;
            automatic logic                    n_wphase   = wait_expected_phase_r;
            automatic logic [15:0]             n_scan     = scan_depth_r;

            automatic logic [31:0]             n_iter_reg = iter_reg;
            automatic logic [$clog2(NUM_LOOP_STACK+1)-1:0] n_loop_depth = loop_depth;

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

            // Reg write scratch (only one ALU op per cycle).
            automatic logic        reg_wr_en  = 1'b0;
            automatic logic [2:0]  reg_wr_idx = 3'd0;
            automatic logic [31:0] reg_wr_val = 32'd0;

            // Loop stack scratch writes (REPEAT push / END pop touch one frame).
            automatic logic                                ls_push_en   = 1'b0;
            automatic logic [LEN_W-1:0]                    ls_push_body = '0;
            automatic logic [31:0]                         ls_push_imax = 32'd0;
            automatic logic [31:0]                         ls_push_pit  = 32'd0;
            // ls_pop_en is a marker flag — set on END but unread; the pop
            // is implemented via the n_loop_depth decrement and the
            // n_iter_reg restore. Kept for readability of the case branch.
            /* verilator lint_off UNUSEDSIGNAL */
            automatic logic                                ls_pop_en    = 1'b0;
            /* verilator lint_on UNUSEDSIGNAL */

            // 1. Push from TB. Append at imem[imem_len].
            if (push_en) begin
                imem[n_imem_len[PC_W-1:0]] <= push_instr;
                n_imem_len = n_imem_len + 1'b1;
            end

            // 2. Handle wait release.
            if (n_state == S_WAITING_FOR_WAIT_DONE) begin
                if (barrier_wait_done) begin
                    n_state = S_IDLE;
                end
            end

            // 3. Handle STORE completion.
            if (n_state == S_WAITING_FOR_STORE_DONE) begin
                if (store_done) begin
                    n_state = S_IDLE;
                end
            end

            // 4. Handle REPEAT-0 scan-forward. Each cycle inspects one instr
            //    and always advances pc (depth tracks nested REPEATs).
            if (n_state == S_SCANNING_END) begin
                if (n_pc < n_imem_len) begin
                    automatic logic [255:0] s_instr;
                    automatic logic [7:0]   s_op;
                    s_instr = imem[n_pc[PC_W-1:0]];
                    s_op    = dec_op(s_instr);
                    if (s_op == OP_REPEAT) begin
                        n_scan = n_scan + 16'd1;
                        n_pc   = n_pc + 1'b1;
                    end else if (s_op == OP_END) begin
                        if (n_scan == 16'd1) begin
                            // Matched: advance past END and return to IDLE.
                            n_pc    = n_pc + 1'b1;
                            n_state = S_IDLE;
                        end else begin
                            n_scan = n_scan - 16'd1;
                            n_pc   = n_pc + 1'b1;
                        end
                    end else begin
                        n_pc = n_pc + 1'b1;
                    end
                end
            end

            // 5. Dispatch next instruction if IDLE and pc < imem_len.
            //    Reading imem on the SAME cycle as a push (NBA write) returns
            //    the OLD value, so a same-cycle push at pc==imem_len must
            //    dispatch from push_instr directly.
            if (n_state == S_IDLE && n_pc < n_imem_len) begin
                automatic logic [255:0] instr;
                automatic logic         take_from_push;
                take_from_push = push_en && (pc == imem_len) && (n_pc == imem_len);
                if (take_from_push) begin
                    instr = push_instr;
                end else begin
                    instr = imem[n_pc[PC_W-1:0]];
                end

                unique case (dec_op(instr))
                    // -------------------- Engine ops --------------------
                    OP_BAR_INIT: begin
                        o_init_en     = 1'b1;
                        o_init_bar_id = {24'd0, dec_byte1(instr)};
                        o_init_count  = dec_imm16(instr);
                        n_pc          = n_pc + 1'b1;
                    end
                    OP_LOAD: begin
                        o_load_en  = 1'b1;
                        o_load_g   = resolve_addr(
                            dec_a_mode(instr),
                            dec_field0(instr), dec_field1(instr),
                            dec_reg_a(instr),
                            n_iter_reg,
                            regs[0], regs[1], regs[2], regs[3],
                            regs[4], regs[5], regs[6], regs[7]);
                        o_load_s   = resolve_addr(
                            dec_b_mode(instr),
                            dec_field2(instr), dec_field3(instr),
                            dec_reg_b(instr),
                            n_iter_reg,
                            regs[0], regs[1], regs[2], regs[3],
                            regs[4], regs[5], regs[6], regs[7]);
                        o_load_b   = dec_field4(instr);
                        o_load_bar = {24'd0, dec_byte1(instr)};
                        n_pc       = n_pc + 1'b1;
                    end
                    OP_MMA: begin
                        o_mma_start = 1'b1;
                        o_mma_a     = resolve_addr(
                            dec_a_mode(instr),
                            dec_field0(instr), dec_field1(instr),
                            dec_reg_a(instr),
                            n_iter_reg,
                            regs[0], regs[1], regs[2], regs[3],
                            regs[4], regs[5], regs[6], regs[7]);
                        o_mma_b     = resolve_addr(
                            dec_b_mode(instr),
                            dec_field2(instr), dec_field3(instr),
                            dec_reg_b(instr),
                            n_iter_reg,
                            regs[0], regs[1], regs[2], regs[3],
                            regs[4], regs[5], regs[6], regs[7]);
                        o_mma_d     = {24'd0, dec_d_tmem_slot(instr)};
                        // accum: 0=imm bit (dec_flag1), 1=iter_nonzero
                        if (dec_accum_mode(instr)) begin
                            o_mma_accum = (n_iter_reg != 32'd0);
                        end else begin
                            o_mma_accum = dec_flag1(instr);
                        end
                        o_mma_bar   = {24'd0, dec_byte1(instr)};
                        n_pc        = n_pc + 1'b1;
                    end
                    OP_STORE: begin
                        o_store_en   = 1'b1;
                        o_store_slot = {24'd0, dec_byte1(instr)};
                        o_store_gptr = resolve_addr(
                            dec_a_mode(instr),
                            dec_field0(instr), dec_field1(instr),
                            dec_reg_a(instr),
                            n_iter_reg,
                            regs[0], regs[1], regs[2], regs[3],
                            regs[4], regs[5], regs[6], regs[7]);
                        o_store_dt   = dec_flag1(instr);
                        n_state      = S_WAITING_FOR_STORE_DONE;
                        n_pc         = n_pc + 1'b1;
                    end
                    OP_WAIT: begin
                        n_wbar   = {24'd0, dec_byte1(instr)};
                        n_wphase = dec_phase(instr);
                        n_state  = S_WAITING_FOR_WAIT_DONE;
                        n_pc     = n_pc + 1'b1;
                    end

                    // -------------------- Control flow --------------------
                    OP_REPEAT: begin
                        automatic logic [31:0] rep_count;
                        rep_count = dec_field0(instr);
                        if (rep_count != 32'd0) begin
                            ls_push_en   = 1'b1;
                            ls_push_body = n_pc + 1'b1;
                            ls_push_imax = rep_count;
                            ls_push_pit  = n_iter_reg;
                            n_iter_reg   = 32'd0;
                            n_loop_depth = n_loop_depth + 1'b1;
                            n_pc         = n_pc + 1'b1;
                        end else begin
                            // REPEAT 0 → scan to matching END. Begin at pc+1
                            // with depth=1.
                            n_pc    = n_pc + 1'b1;
                            n_scan  = 16'd1;
                            n_state = S_SCANNING_END;
                        end
                    end
                    OP_END: begin
                        if (n_loop_depth != 0) begin
                            // Increment innermost iter; jump back or pop.
                            automatic logic [$clog2(NUM_LOOP_STACK)-1:0] top;
                            automatic logic [31:0] new_iter;
                            // top_wide is one bit wider than `top` so the
                            // `n_loop_depth - 1` subtraction can't underflow;
                            // we truncate to log2(stack) for the array index.
                            /* verilator lint_off UNUSEDSIGNAL */
                            automatic logic [$clog2(NUM_LOOP_STACK+1)-1:0] top_wide;
                            /* verilator lint_on UNUSEDSIGNAL */
                            top_wide = n_loop_depth - 1'b1;
                            top = top_wide[$clog2(NUM_LOOP_STACK)-1:0];
                            new_iter = n_iter_reg + 32'd1;
                            if (new_iter < ls_iter_max[top]) begin
                                n_iter_reg = new_iter;
                                n_pc       = ls_body_start[top];
                            end else begin
                                // Pop frame.
                                ls_pop_en    = 1'b1;
                                n_iter_reg   = ls_parent_iter[top];
                                n_loop_depth = n_loop_depth - 1'b1;
                                n_pc         = n_pc + 1'b1;
                            end
                        end else begin
                            // Stray END — no-op.
                            n_pc = n_pc + 1'b1;
                        end
                    end
                    OP_BRZ: begin
                        automatic logic [2:0]  ra_idx;
                        automatic logic [31:0] ra_val;
                        // br_target is a 32-bit signed sum; only the low
                        // LEN_W bits (pc width) are consumed by n_pc.
                        /* verilator lint_off UNUSEDSIGNAL */
                        automatic logic [31:0] br_target;
                        /* verilator lint_on UNUSEDSIGNAL */
                        ra_idx = dec_reg_a(instr);
                        case (ra_idx)
                            3'd0: ra_val = regs[0]; 3'd1: ra_val = regs[1];
                            3'd2: ra_val = regs[2]; 3'd3: ra_val = regs[3];
                            3'd4: ra_val = regs[4]; 3'd5: ra_val = regs[5];
                            3'd6: ra_val = regs[6]; 3'd7: ra_val = regs[7];
                        endcase
                        if (ra_val == 32'd0) begin
                            br_target = $signed({{(32-LEN_W){1'b0}}, n_pc}) + $signed(dec_field0(instr));
                            n_pc = br_target[LEN_W-1:0];
                        end else begin
                            n_pc = n_pc + 1'b1;
                        end
                    end
                    OP_BRNZ: begin
                        automatic logic [2:0]  ra_idx;
                        automatic logic [31:0] ra_val;
                        /* verilator lint_off UNUSEDSIGNAL */
                        automatic logic [31:0] br_target;
                        /* verilator lint_on UNUSEDSIGNAL */
                        ra_idx = dec_reg_a(instr);
                        case (ra_idx)
                            3'd0: ra_val = regs[0]; 3'd1: ra_val = regs[1];
                            3'd2: ra_val = regs[2]; 3'd3: ra_val = regs[3];
                            3'd4: ra_val = regs[4]; 3'd5: ra_val = regs[5];
                            3'd6: ra_val = regs[6]; 3'd7: ra_val = regs[7];
                        endcase
                        if (ra_val != 32'd0) begin
                            br_target = $signed({{(32-LEN_W){1'b0}}, n_pc}) + $signed(dec_field0(instr));
                            n_pc = br_target[LEN_W-1:0];
                        end else begin
                            n_pc = n_pc + 1'b1;
                        end
                    end
                    OP_JMP: begin
                        /* verilator lint_off UNUSEDSIGNAL */
                        automatic logic [31:0] jmp_target;
                        /* verilator lint_on UNUSEDSIGNAL */
                        jmp_target = $signed({{(32-LEN_W){1'b0}}, n_pc}) + $signed(dec_field0(instr));
                        n_pc = jmp_target[LEN_W-1:0];
                    end

                    // -------------------- ALU --------------------
                    OP_SET_REG: begin
                        reg_wr_en  = 1'b1;
                        reg_wr_idx = dec_reg_d(instr);
                        reg_wr_val = dec_field0(instr);
                        n_pc       = n_pc + 1'b1;
                    end
                    OP_ADD: begin
                        automatic logic [2:0] ra_idx, rb_idx;
                        automatic logic [31:0] ra_val, rb_val;
                        ra_idx = dec_reg_a(instr);
                        rb_idx = dec_reg_b(instr);
                        case (ra_idx)
                            3'd0: ra_val = regs[0]; 3'd1: ra_val = regs[1];
                            3'd2: ra_val = regs[2]; 3'd3: ra_val = regs[3];
                            3'd4: ra_val = regs[4]; 3'd5: ra_val = regs[5];
                            3'd6: ra_val = regs[6]; 3'd7: ra_val = regs[7];
                        endcase
                        case (rb_idx)
                            3'd0: rb_val = regs[0]; 3'd1: rb_val = regs[1];
                            3'd2: rb_val = regs[2]; 3'd3: rb_val = regs[3];
                            3'd4: rb_val = regs[4]; 3'd5: rb_val = regs[5];
                            3'd6: rb_val = regs[6]; 3'd7: rb_val = regs[7];
                        endcase
                        reg_wr_en  = 1'b1;
                        reg_wr_idx = dec_reg_d(instr);
                        reg_wr_val = ra_val + rb_val;
                        n_pc       = n_pc + 1'b1;
                    end
                    OP_ADDI: begin
                        automatic logic [2:0] ra_idx;
                        automatic logic [31:0] ra_val;
                        ra_idx = dec_reg_a(instr);
                        case (ra_idx)
                            3'd0: ra_val = regs[0]; 3'd1: ra_val = regs[1];
                            3'd2: ra_val = regs[2]; 3'd3: ra_val = regs[3];
                            3'd4: ra_val = regs[4]; 3'd5: ra_val = regs[5];
                            3'd6: ra_val = regs[6]; 3'd7: ra_val = regs[7];
                        endcase
                        reg_wr_en  = 1'b1;
                        reg_wr_idx = dec_reg_d(instr);
                        reg_wr_val = ra_val + dec_field0(instr);
                        n_pc       = n_pc + 1'b1;
                    end
                    OP_SUB: begin
                        automatic logic [2:0] ra_idx, rb_idx;
                        automatic logic [31:0] ra_val, rb_val;
                        ra_idx = dec_reg_a(instr);
                        rb_idx = dec_reg_b(instr);
                        case (ra_idx)
                            3'd0: ra_val = regs[0]; 3'd1: ra_val = regs[1];
                            3'd2: ra_val = regs[2]; 3'd3: ra_val = regs[3];
                            3'd4: ra_val = regs[4]; 3'd5: ra_val = regs[5];
                            3'd6: ra_val = regs[6]; 3'd7: ra_val = regs[7];
                        endcase
                        case (rb_idx)
                            3'd0: rb_val = regs[0]; 3'd1: rb_val = regs[1];
                            3'd2: rb_val = regs[2]; 3'd3: rb_val = regs[3];
                            3'd4: rb_val = regs[4]; 3'd5: rb_val = regs[5];
                            3'd6: rb_val = regs[6]; 3'd7: rb_val = regs[7];
                        endcase
                        reg_wr_en  = 1'b1;
                        reg_wr_idx = dec_reg_d(instr);
                        reg_wr_val = ra_val - rb_val;
                        n_pc       = n_pc + 1'b1;
                    end
                    OP_AND: begin
                        automatic logic [2:0] ra_idx, rb_idx;
                        automatic logic [31:0] ra_val, rb_val;
                        ra_idx = dec_reg_a(instr);
                        rb_idx = dec_reg_b(instr);
                        case (ra_idx)
                            3'd0: ra_val = regs[0]; 3'd1: ra_val = regs[1];
                            3'd2: ra_val = regs[2]; 3'd3: ra_val = regs[3];
                            3'd4: ra_val = regs[4]; 3'd5: ra_val = regs[5];
                            3'd6: ra_val = regs[6]; 3'd7: ra_val = regs[7];
                        endcase
                        case (rb_idx)
                            3'd0: rb_val = regs[0]; 3'd1: rb_val = regs[1];
                            3'd2: rb_val = regs[2]; 3'd3: rb_val = regs[3];
                            3'd4: rb_val = regs[4]; 3'd5: rb_val = regs[5];
                            3'd6: rb_val = regs[6]; 3'd7: rb_val = regs[7];
                        endcase
                        reg_wr_en  = 1'b1;
                        reg_wr_idx = dec_reg_d(instr);
                        reg_wr_val = ra_val & rb_val;
                        n_pc       = n_pc + 1'b1;
                    end

                    default: begin
                        // Unknown opcode: skip.
                        n_pc = n_pc + 1'b1;
                    end
                endcase
            end

            // ---------- Commit ----------
            state                 <= n_state;
            pc                    <= n_pc;
            imem_len              <= n_imem_len;
            wait_bar_id_r         <= n_wbar;
            wait_expected_phase_r <= n_wphase;
            scan_depth_r          <= n_scan;
            iter_reg              <= n_iter_reg;
            loop_depth            <= n_loop_depth;

            if (reg_wr_en) begin
                regs[reg_wr_idx] <= reg_wr_val;
            end

            if (ls_push_en) begin
                // Push at index loop_depth (the OLD depth — new entry sits
                // at this slot; n_loop_depth is now old+1).
                ls_body_start [loop_depth[$clog2(NUM_LOOP_STACK)-1:0]] <= ls_push_body;
                ls_iter_max   [loop_depth[$clog2(NUM_LOOP_STACK)-1:0]] <= ls_push_imax;
                ls_parent_iter[loop_depth[$clog2(NUM_LOOP_STACK)-1:0]] <= ls_push_pit;
            end
            // ls_pop_en doesn't need a write: n_loop_depth already decremented,
            // stale slot will be overwritten by next push.

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
