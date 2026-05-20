// load.sv — DMA engine: gmem -> smem. One in-flight transfer at a time.
//
// SV implementation of pymodel.load.Load. See pymodel/load.py for the canonical
// spec; this module must match it cycle-by-cycle on the engine-contract signals:
//
//     accept, busy, done, add_tx_*, sub_tx_*, arrive_*
//
// CYCLE ACCOUNTING (must match pymodel exactly)
//
//   pymodel uses back-door gmem.dump / smem.load with 1 beat transferred per
//   tick while a cmd is current. The accept tick also pops the FIFO (if cur
//   was None) and transfers the first beat, so a 1-beat LOAD can complete the
//   same cycle issue_en is taken.
//
//   This RTL mirrors pymodel's tick body in priority order:
//     1. ACCEPT: push issue_cmd into in_fifo; pulse accept + add_tx (same cycle).
//     2. POP: if cur is None and in_fifo non-empty, take next cmd into cur,
//        reset bytes_transferred = 0. (POP happens AFTER the push, so a
//        same-cycle issue can be popped immediately into cur.)
//     3. TRANSFER: if cur valid, bytes_transferred += BEAT_BYTES.
//     4. COMPLETION: if bytes_transferred >= cur.bytes, pulse sub_tx + arrive
//        + done, clear cur.
//
//   busy = 1 iff (cur_valid || fifo_count > 0) after step 4.
//
// DATA PIPELINE (independent — drives real gmem/smem ports)
//
//   pymodel uses back-door access. The SV engine drives the real gmem read
//   port (1-cycle latency, but inter-module NBA adds another, so ~3 cycle
//   effective latency) and smem write port. The smem writes consequently lag
//   the corresponding logical beat by several cycles. This is invisible to
//   the cycle-by-cycle compare (which only watches accept/busy/done/barrier
//   pulses), but the TB must wait extra cycles after busy=0 before checking
//   smem contents.
//
//   The pipeline maintains its OWN small FIFO of cmds that have been popped
//   logically but not yet drained by the pipe. (For back-to-back short
//   commands, logical pops faster than pipe drains, so a buffer is required
//   to avoid clobbering pipe state.)
//
//   Per pipe cmd: rd_issued counts bytes issued via gmem.rd_en; wr_done counts
//   bytes written into smem. A 3-stage shift-register skid carries the smem
//   target address from the cycle gmem.rd_en is driven to the cycle
//   gmem.rd_valid comes back (3 edges away — see "EFFECTIVE GMEM LATENCY"
//   below). When wr_done reaches the cmd's byte total, the cmd retires from
//   the pipe and the next pipe_q entry (if any) is adopted.
//
// EFFECTIVE GMEM LATENCY (3 cycles, NOT 1)
//
//   The pymodel says gmem has 1-cycle read latency, which is correct in
//   isolation. But with the engine driving gmem via NBA (registered output),
//   the rd_en value isn't visible to gmem until the NEXT edge — adding 1
//   extra cycle. Likewise, gmem's rd_valid (also NBA) isn't visible to the
//   engine until the NEXT edge after gmem commits it. End-to-end:
//
//     Edge T   : engine NBA rd_en<=1.
//     Edge T+1 : gmem reads rd_en=1, NBA rd_pending<=1.
//     Edge T+2 : gmem reads rd_pending=1, NBA rd_valid<=1.
//     Edge T+3 : engine reads rd_valid=1, can now drive smem.wr.
//
//   Hence the 3-stage skid: stage 0 (just issued), stage 1 (1 cycle old),
//   stage 2 (2 cycles old — matches rd_valid arriving at the same edge).
//
// FIFO STRUCTURE
//
//   Both the logical input FIFO and the pipe queue use ring buffers of depth
//   INSTR_FIFO_DEPTH. head/tail/count counters. We trust the spec that cmdproc
//   never pushes when full.

module load #(
    parameter int BEAT_BYTES       = 16,
    parameter int INSTR_FIFO_DEPTH = 8
) (
    input  logic                       clk,
    input  logic                       reset,

    // Command issue port (from cmdproc).
    input  logic                       issue_en,
    input  logic [31:0]                gmem_ptr,
    input  logic [31:0]                smem_ptr,
    input  logic [31:0]                bytes_n,
    input  logic [31:0]                bar_id,

    // gmem read port. Engine drives rd_en/rd_addr; gmem returns rd_data/rd_valid.
    output logic                       gmem_rd_en,
    output logic [31:0]                gmem_rd_addr,
    input  logic [BEAT_BYTES*8-1:0]    gmem_rd_data,
    input  logic                       gmem_rd_valid,

    // smem LOAD_WR port.
    output logic                       smem_wr_en,
    output logic [31:0]                smem_wr_addr,
    output logic [BEAT_BYTES*8-1:0]    smem_wr_data,
    // Combinational stall from SMEM. With the SMEM's fixed-priority
    // arbiter (LOAD_WR > RD_A > RD_B), LOAD_WR is top priority and never
    // stalls — `smem_wr_stall_in` is always 0 in practice. We accept the
    // pin for wiring parity / future-compatibility but do not use it.
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic                       smem_wr_stall_in,
    /* verilator lint_on UNUSEDSIGNAL */

    // Barrier drives.
    output logic                       add_tx_en,
    output logic [31:0]                add_tx_bar_id,
    output logic [31:0]                add_tx_bytes,
    output logic                       sub_tx_en,
    output logic [31:0]                sub_tx_bar_id,
    output logic [31:0]                sub_tx_bytes,
    output logic                       arrive_en,
    output logic [31:0]                arrive_bar_id,

    // Status.
    output logic                       busy,
    output logic                       done,
    output logic                       accept
);

    // ------------------------------------------------------------------
    // Logical input FIFO storage (matches pymodel.in_fifo).
    // ------------------------------------------------------------------

    logic [31:0] fifo_gmem [INSTR_FIFO_DEPTH];
    logic [31:0] fifo_smem [INSTR_FIFO_DEPTH];
    logic [31:0] fifo_bytes[INSTR_FIFO_DEPTH];
    logic [31:0] fifo_bar  [INSTR_FIFO_DEPTH];

    localparam int FIFO_IDX_W = (INSTR_FIFO_DEPTH <= 1) ? 1 : $clog2(INSTR_FIFO_DEPTH);
    localparam int FIFO_CNT_W = FIFO_IDX_W + 1;

    logic [FIFO_IDX_W-1:0] fifo_head;
    logic [FIFO_IDX_W-1:0] fifo_tail;
    logic [FIFO_CNT_W-1:0] fifo_count;

    // Pipe queue: cmds that have been logically popped but not yet drained.
    logic [31:0] pq_gmem [INSTR_FIFO_DEPTH];
    logic [31:0] pq_smem [INSTR_FIFO_DEPTH];
    logic [31:0] pq_bytes[INSTR_FIFO_DEPTH];

    logic [FIFO_IDX_W-1:0] pq_head;
    logic [FIFO_IDX_W-1:0] pq_tail;
    logic [FIFO_CNT_W-1:0] pq_count;

    // ------------------------------------------------------------------
    // Logical current command (mirrored against pymodel).
    // ------------------------------------------------------------------

    logic                  cur_valid;
    logic [31:0]           cur_gmem;
    logic [31:0]           cur_smem;
    logic [31:0]           cur_bytes;
    logic [31:0]           cur_bar;
    logic [31:0]           cur_bytes_xferred;

    // ------------------------------------------------------------------
    // Pipeline current command (drives real gmem/smem ports).
    // ------------------------------------------------------------------

    logic                  pipe_valid;
    logic [31:0]           pipe_gmem;
    logic [31:0]           pipe_smem;
    logic [31:0]           pipe_bytes;
    logic [31:0]           rd_issued;
    logic [31:0]           wr_done;

    // Read pipeline skid (3-deep shift register). The effective latency from
    // load.gmem_rd_en (NBA) to load observing gmem_rd_valid is 3 cycles
    // because of inter-module NBA crossings (load NBA → gmem sees next edge
    // → gmem capture → gmem drain → load sees rd_valid). This shift register
    // mirrors that pipeline so the smem target captured the cycle rd_en was
    // driven re-emerges 3 cycles later, in sync with rd_data/rd_valid.
    //
    // Each stage holds {valid, smem_addr}. Stage 0 is the just-issued read
    // (this cycle's rd_en); stage 2 is the one whose rd_valid is arriving
    // THIS cycle.
    logic                  skid_v0, skid_v1, skid_v2;
    logic [31:0]           skid_a0, skid_a1, skid_a2;

    initial begin
        for (int i = 0; i < INSTR_FIFO_DEPTH; i++) begin
            fifo_gmem[i]  = 32'd0;
            fifo_smem[i]  = 32'd0;
            fifo_bytes[i] = 32'd0;
            fifo_bar[i]   = 32'd0;
            pq_gmem[i]    = 32'd0;
            pq_smem[i]    = 32'd0;
            pq_bytes[i]   = 32'd0;
        end
    end

    always_ff @(posedge clk) begin
        if (reset) begin
            fifo_head         <= '0;
            fifo_tail         <= '0;
            fifo_count        <= '0;
            pq_head           <= '0;
            pq_tail           <= '0;
            pq_count          <= '0;

            cur_valid         <= 1'b0;
            cur_gmem          <= 32'd0;
            cur_smem          <= 32'd0;
            cur_bytes         <= 32'd0;
            cur_bar           <= 32'd0;
            cur_bytes_xferred <= 32'd0;

            pipe_valid        <= 1'b0;
            pipe_gmem         <= 32'd0;
            pipe_smem         <= 32'd0;
            pipe_bytes        <= 32'd0;
            rd_issued         <= 32'd0;
            wr_done           <= 32'd0;
            skid_v0           <= 1'b0;
            skid_v1           <= 1'b0;
            skid_v2           <= 1'b0;
            skid_a0           <= 32'd0;
            skid_a1           <= 32'd0;
            skid_a2           <= 32'd0;

            busy              <= 1'b0;
            done              <= 1'b0;
            accept            <= 1'b0;
            add_tx_en         <= 1'b0;
            add_tx_bar_id     <= 32'd0;
            add_tx_bytes      <= 32'd0;
            sub_tx_en         <= 1'b0;
            sub_tx_bar_id     <= 32'd0;
            sub_tx_bytes      <= 32'd0;
            arrive_en         <= 1'b0;
            arrive_bar_id     <= 32'd0;

            gmem_rd_en        <= 1'b0;
            gmem_rd_addr      <= 32'd0;
            smem_wr_en        <= 1'b0;
            smem_wr_addr      <= 32'd0;
            smem_wr_data      <= '0;
        end else begin
            // ---------------- Next-state scratch ----------------
            automatic logic [FIFO_IDX_W-1:0] n_head     = fifo_head;
            automatic logic [FIFO_IDX_W-1:0] n_tail     = fifo_tail;
            automatic logic [FIFO_CNT_W-1:0] n_count    = fifo_count;

            automatic logic [FIFO_IDX_W-1:0] n_pq_head  = pq_head;
            automatic logic [FIFO_IDX_W-1:0] n_pq_tail  = pq_tail;
            automatic logic [FIFO_CNT_W-1:0] n_pq_count = pq_count;

            automatic logic                  n_curv     = cur_valid;
            automatic logic [31:0]           n_curg     = cur_gmem;
            automatic logic [31:0]           n_curs     = cur_smem;
            automatic logic [31:0]           n_curB     = cur_bytes;
            automatic logic [31:0]           n_curb     = cur_bar;
            automatic logic [31:0]           n_curx     = cur_bytes_xferred;

            automatic logic                  n_pipev    = pipe_valid;
            automatic logic [31:0]           n_pipeg    = pipe_gmem;
            automatic logic [31:0]           n_pipes    = pipe_smem;
            automatic logic [31:0]           n_pipeB    = pipe_bytes;
            automatic logic [31:0]           n_rdi      = rd_issued;
            automatic logic [31:0]           n_wrd      = wr_done;
            // 3-stage skid: shift each cycle. Start with the shifted values.
            automatic logic                  n_skidv0   = 1'b0;
            automatic logic                  n_skidv1   = skid_v0;
            automatic logic                  n_skidv2   = skid_v1;
            automatic logic [31:0]           n_skida0   = 32'd0;
            automatic logic [31:0]           n_skida1   = skid_a0;
            automatic logic [31:0]           n_skida2   = skid_a1;

            // Default output pulses.
            automatic logic                  o_accept   = 1'b0;
            automatic logic                  o_done     = 1'b0;
            automatic logic                  o_addtx    = 1'b0;
            automatic logic [31:0]           o_addtx_id = 32'd0;
            automatic logic [31:0]           o_addtx_b  = 32'd0;
            automatic logic                  o_subtx    = 1'b0;
            automatic logic [31:0]           o_subtx_id = 32'd0;
            automatic logic [31:0]           o_subtx_b  = 32'd0;
            automatic logic                  o_arr      = 1'b0;
            automatic logic [31:0]           o_arr_id   = 32'd0;

            automatic logic                  o_rden     = 1'b0;
            automatic logic [31:0]           o_rdaddr   = 32'd0;
            automatic logic                  o_wren     = 1'b0;
            automatic logic [31:0]           o_wraddr   = 32'd0;
            automatic logic [BEAT_BYTES*8-1:0] o_wrdata = '0;

            // Scratch for pop step. Initialize to 0 to avoid X propagation
            // when no logical pop happens this cycle.
            automatic logic                  take_from_input = 1'b0;
            automatic logic [31:0]           pop_gmem        = 32'd0;
            automatic logic [31:0]           pop_smem        = 32'd0;
            automatic logic [31:0]           pop_bytes       = 32'd0;
            automatic logic [31:0]           pop_bar         = 32'd0;

            // ----------------- 1. ACCEPT (push input FIFO) -----------------
            if (issue_en) begin
                fifo_gmem[n_tail]  <= gmem_ptr;
                fifo_smem[n_tail]  <= smem_ptr;
                fifo_bytes[n_tail] <= bytes_n;
                fifo_bar[n_tail]   <= bar_id;
                n_tail  = n_tail + 1'b1;
                n_count = n_count + 1'b1;
                o_accept   = 1'b1;
                o_addtx    = 1'b1;
                o_addtx_id = bar_id;
                o_addtx_b  = bytes_n;
            end

            // ----------------- 2. POP (start new cur) -----------------
            // pymodel evaluates AFTER the push, so a same-cycle issue can be
            // popped immediately. Reading the FIFO storage right after writing
            // it via NBA returns the OLD value, so we have two cases:
            //   (a) The just-pushed cmd is the one to pop (FIFO was empty).
            //       Take operands from the input directly.
            //   (b) The FIFO already had entries — pop from storage normally.
            if (!n_curv && n_count != 0) begin
                take_from_input = issue_en && (fifo_count == 0);
                if (take_from_input) begin
                    pop_gmem  = gmem_ptr;
                    pop_smem  = smem_ptr;
                    pop_bytes = bytes_n;
                    pop_bar   = bar_id;
                end else begin
                    pop_gmem  = fifo_gmem [fifo_head];
                    pop_smem  = fifo_smem [fifo_head];
                    pop_bytes = fifo_bytes[fifo_head];
                    pop_bar   = fifo_bar  [fifo_head];
                end
                n_head  = n_head + 1'b1;
                n_count = n_count - 1'b1;

                n_curv = 1'b1;
                n_curg = pop_gmem;
                n_curs = pop_smem;
                n_curB = pop_bytes;
                n_curb = pop_bar;
                n_curx = 32'd0;

                // Enqueue into pipe queue too (NBA write).
                pq_gmem [n_pq_tail] <= pop_gmem;
                pq_smem [n_pq_tail] <= pop_smem;
                pq_bytes[n_pq_tail] <= pop_bytes;
                n_pq_tail  = n_pq_tail + 1'b1;
                n_pq_count = n_pq_count + 1'b1;
            end

            // ----------------- 3. TRANSFER (logical) -----------------
            if (n_curv) begin
                n_curx = n_curx + BEAT_BYTES;
            end

            // ----------------- 4. COMPLETION (logical) -----------------
            if (n_curv && (n_curx >= n_curB)) begin
                o_subtx    = 1'b1;
                o_subtx_id = n_curb;
                o_subtx_b  = n_curB;
                o_arr      = 1'b1;
                o_arr_id   = n_curb;
                o_done     = 1'b1;
                n_curv = 1'b0;
                n_curg = 32'd0;
                n_curs = 32'd0;
                n_curB = 32'd0;
                n_curb = 32'd0;
                n_curx = 32'd0;
            end

            // ----------------- Pipeline: drain rd_valid -> smem.wr -----------
            // Stage 2 (3 cycles old at start of this edge) pairs with the
            // rd_valid arriving this cycle.
            if (gmem_rd_valid && skid_v2) begin
                o_wren   = 1'b1;
                o_wraddr = skid_a2;
                o_wrdata = gmem_rd_data;
                n_wrd    = n_wrd + BEAT_BYTES;
            end

            // ----------------- Pipeline: retire current cmd if drained -------
            if (n_pipev && (n_wrd >= n_pipeB)) begin
                n_pipev = 1'b0;
                n_pipeg = 32'd0;
                n_pipes = 32'd0;
                n_pipeB = 32'd0;
                n_rdi   = 32'd0;
                n_wrd   = 32'd0;
            end

            // ----------------- Pipeline: adopt next pipe_q entry if idle -----
            // For a same-cycle "logical pop & immediate pipe start" pattern,
            // we want the read to issue THIS cycle. Use the just-pushed
            // pq entry when pq was previously empty.
            if (!n_pipev && n_pq_count != 0) begin
                automatic logic pq_take_from_pop;
                pq_take_from_pop = (pq_count == 0) && (n_pq_count == 1);
                if (pq_take_from_pop) begin
                    // The single entry in pq was just enqueued this cycle; use
                    // the pop_* values directly (NBA write hasn't committed).
                    n_pipeg = pop_gmem;
                    n_pipes = pop_smem;
                    n_pipeB = pop_bytes;
                end else begin
                    n_pipeg = pq_gmem [pq_head];
                    n_pipes = pq_smem [pq_head];
                    n_pipeB = pq_bytes[pq_head];
                end
                n_pq_head  = n_pq_head + 1'b1;
                n_pq_count = n_pq_count - 1'b1;
                n_pipev = 1'b1;
                n_rdi   = 32'd0;
                n_wrd   = 32'd0;
            end

            // ----------------- Pipeline: issue next gmem read ---------------
            if (n_pipev && (n_rdi < n_pipeB)) begin
                o_rden   = 1'b1;
                o_rdaddr = n_pipeg + n_rdi;
                // Push into stage 0 of the skid; it'll shift to stage 1 next
                // cycle and stage 2 the cycle after that, by which time the
                // matching gmem.rd_valid arrives.
                n_skidv0 = 1'b1;
                n_skida0 = n_pipes + n_rdi;
                n_rdi    = n_rdi + BEAT_BYTES;
            end

            // ----------------- Commit -----------------
            fifo_head          <= n_head;
            fifo_tail          <= n_tail;
            fifo_count         <= n_count;
            pq_head            <= n_pq_head;
            pq_tail            <= n_pq_tail;
            pq_count           <= n_pq_count;

            cur_valid          <= n_curv;
            cur_gmem           <= n_curg;
            cur_smem           <= n_curs;
            cur_bytes          <= n_curB;
            cur_bar            <= n_curb;
            cur_bytes_xferred  <= n_curx;

            pipe_valid         <= n_pipev;
            pipe_gmem          <= n_pipeg;
            pipe_smem          <= n_pipes;
            pipe_bytes         <= n_pipeB;
            rd_issued          <= n_rdi;
            wr_done            <= n_wrd;
            skid_v0            <= n_skidv0;
            skid_v1            <= n_skidv1;
            skid_v2            <= n_skidv2;
            skid_a0            <= n_skida0;
            skid_a1            <= n_skida1;
            skid_a2            <= n_skida2;

            // pymodel: busy = 1 iff (cur not None or fifo non-empty), evaluated
            // at end of tick (after step 4). Mirror with next-state values.
            busy               <= (n_curv || (n_count != 0)) ? 1'b1 : 1'b0;
            done               <= o_done;
            accept             <= o_accept;

            add_tx_en          <= o_addtx;
            add_tx_bar_id      <= o_addtx_id;
            add_tx_bytes       <= o_addtx_b;
            sub_tx_en          <= o_subtx;
            sub_tx_bar_id      <= o_subtx_id;
            sub_tx_bytes       <= o_subtx_b;
            arrive_en          <= o_arr;
            arrive_bar_id      <= o_arr_id;

            gmem_rd_en         <= o_rden;
            gmem_rd_addr       <= o_rdaddr;
            smem_wr_en         <= o_wren;
            smem_wr_addr       <= o_wraddr;
            smem_wr_data       <= o_wrdata;
        end
    end

endmodule
