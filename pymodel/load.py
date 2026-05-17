"""
load — DMA: gmem → smem. Streaming, one in-flight transaction at a time.

PURPOSE
    Executes the LOAD instruction. Reads `bytes` from gmem starting at
    `gmem_ptr`, writes them to smem starting at `smem_ptr`. Signals the
    given barrier with tx-count accounting plus one arrival.

ASYNC MODEL
    LOAD issues are non-blocking from cmdproc's POV. The engine has an
    INPUT FIFO of pending commands; cmdproc drops a command and advances.
    The engine drains the FIFO one at a time. Internally, at most one DMA
    transaction is in flight (no MSHR-style outstanding requests in v1).

INPUTS (sampled at tick start)
    issue_en      : 1-bit  — cmdproc asserts when pushing a new command
    issue_cmd     : { gmem_ptr, smem_ptr, bytes, bar_id }
    gmem.rd_data  : BEAT_BYTES bytes (registered output of gmem read port)
    gmem.rd_valid : 1-bit

OUTPUTS (registered)
    accept        : 1-bit — pulses cycle issue_en is accepted into input FIFO
    busy          : 1-bit — high while any command is queued or executing
    done          : 1-bit — pulses one cycle when current transaction completes
    gmem.rd_en, rd_addr
    smem.LOAD_WR.wr_en, wr_addr, wr_data
    barrier.add_tx_en,  add_tx_bar_id,  add_tx_bytes  (on issue of new command)
    barrier.sub_tx_en,  sub_tx_bar_id,  sub_tx_bytes  (on completion)
    barrier.arrive_en,  arrive_bar_id                 (on completion)

INTERNAL STATE
    in_fifo       : queue[ command ], depth INSTR_FIFO_DEPTH-ish, init empty
    cur           : currently-executing command or None
    bytes_read    : bytes already read from gmem in current transaction
    bytes_written : bytes already written to smem in current transaction
    skid          : small byte buffer for in-flight bytes returning from gmem
                    (size: a few BEAT_BYTES, just to cover gmem read latency)

BEHAVIOR (per tick, two-phase)
    sample : capture issue_en+issue_cmd; capture gmem.rd_data+rd_valid
    commit :
        1. Accept new command:
             if issue_en and in_fifo not full:
                in_fifo.push(issue_cmd)
                accept <= 1
                # Issue-time barrier update happens HERE, atomic with the push:
                barrier.add_tx(issue_cmd.bar_id, issue_cmd.bytes)
        2. Begin next command:
             if cur is None and in_fifo not empty:
                cur = in_fifo.pop()
                bytes_read = 0
                bytes_written = 0
                skid clear
        3. Issue gmem reads:
             if cur and bytes_read < cur.bytes:
                rd_en = 1
                rd_addr = cur.gmem_ptr + bytes_read
                bytes_read += BEAT_BYTES
        4. Receive gmem data:
             if gmem.rd_valid:
                append rd_data to skid
        5. Drain skid into smem:
             if skid has BEAT_BYTES available and cur.bytes_written + BEAT_BYTES <= cur.bytes:
                smem.LOAD_WR.wr_en = 1
                smem.LOAD_WR.wr_addr = cur.smem_ptr + bytes_written
                smem.LOAD_WR.wr_data = skid.pop_front(BEAT_BYTES)
                bytes_written += BEAT_BYTES
        6. Complete current command:
             if bytes_written == cur.bytes:
                barrier.sub_tx(cur.bar_id, cur.bytes)
                barrier.arrive(cur.bar_id)
                done <= 1
                cur = None

ENGINE CONTRACT (key sequencing rules)
    - add_tx is issued at command-ACCEPT time (the cycle issue_en arrives,
      not the cycle execution begins). This is critical for correctness:
      WAIT must not flip the barrier prematurely while a queued LOAD has
      yet to be issued.
    - sub_tx and arrive both fire on the same cycle when the last byte is
      written. Order within the cycle: sub_tx before arrive (so the flip
      rule sees the final tx_pending = 0 in time).

INVARIANTS
    - cur.bytes % BEAT_BYTES == 0 (LOAD only handles whole-beat transfers in v1).
    - gmem_ptr and smem_ptr are BEAT_BYTES-aligned.
    - in_fifo overflow asserts (cmdproc should never push when full;
      caller checks accept signal first, ideally).

HANDSHAKE
    Cmdproc issues: drives issue_en + issue_cmd. Engine asserts `accept`
    when it takes the command into in_fifo.
    Completion: done pulses one cycle; barrier sees add_tx at issue,
    sub_tx+arrive at completion.

TEST CASES (pymodel/tests/test_load.py)
    1. single_load: preload gmem with a known pattern, issue LOAD of 1024 bytes,
       run until done, check smem contents match.
    2. barrier_accounting: barrier add_tx fires the cycle issue is accepted;
       sub_tx + arrive fire on the completion cycle. tx_pending returns to 0.
    3. two_loads_queued: issue two LOADs back-to-back; cmdproc not stalled
       (both accepts within 2 cycles); both eventually complete with correct
       smem contents and barrier state.
    4. multi_beat_correctness: LOAD of N*BEAT_BYTES bytes copies exactly N beats.
    5. assert_unaligned_bytes_or_addrs.
"""

"""
Implementation NOTES (back-door simplification for pymodel):

LOAD accesses GMEM and SMEM via back-door methods (gmem.dump, smem.load).
This avoids modeling the multi-cycle port handshake; transfer cycles are
still counted explicitly (bytes / BEAT_BYTES).

Barrier interaction (signals to SIM harness, routed to barrier.tick):
    On COMMAND ACCEPT (issue_en seen, FIFO push):  add_tx_en pulses with
        the FULL byte count of the new command. This is required for WAIT
        correctness — pending LOADs in the FIFO must already raise
        tx_pending so a WAIT can't flip the barrier early.
    On COMMAND COMPLETION (last beat of current cmd written):
        sub_tx_en pulses with that command's total byte count, and
        arrive_en pulses concurrently.
"""

from collections import deque

import numpy as np

from config import BEAT_BYTES


class Load:
    def __init__(self, gmem, smem):
        self.gmem = gmem
        self.smem = smem
        # Registered outputs (signals to SIM/barrier)
        self.busy: int = 0
        self.done: int = 0
        self.accept: int = 0
        # Barrier drive pulses
        self.add_tx_en: int = 0
        self.add_tx_bar_id: int = 0
        self.add_tx_bytes: int = 0
        self.sub_tx_en: int = 0
        self.sub_tx_bar_id: int = 0
        self.sub_tx_bytes: int = 0
        self.arrive_en: int = 0
        self.arrive_bar_id: int = 0
        # Internal state
        self._in_fifo: deque[dict] = deque()
        self._cur: dict | None = None
        self._bytes_transferred: int = 0

    def tick(
        self,
        *,
        issue_en: int = 0,
        gmem_ptr: int = 0,
        smem_ptr: int = 0,
        bytes_n: int = 0,
        bar_id: int = 0,
        smem_wr_stall_in: int = 0,
    ) -> None:
        """Tick one cycle.

        `smem_wr_stall_in` mirrors the SMEM's combinational `load_wr_stall_out`
        for the current cycle. In the current pymodel sim, LOAD uses
        back-door SMEM access (smem.load) so it never actually contends
        with MMA reads — but for parity with the RTL stall protocol, when
        `smem_wr_stall_in` is 1 we pause the per-beat transfer (do not
        increment _bytes_transferred and do not signal completion this
        cycle). Note: per the priority arbitration (LOAD_WR > RD_A > RD_B),
        the SMEM's `load_wr_stall_out` is always 0; this parameter exists
        purely as a forward-compatibility hook.
        """
        # Clear pulse outputs.
        self.done = 0
        self.accept = 0
        self.add_tx_en = 0
        self.sub_tx_en = 0
        self.arrive_en = 0

        # 1. Accept new command into input FIFO (atomic with add_tx).
        if issue_en:
            assert bytes_n > 0, "LOAD bytes must be > 0"
            assert bytes_n % BEAT_BYTES == 0, f"LOAD bytes {bytes_n} not BEAT_BYTES-aligned"
            assert gmem_ptr % BEAT_BYTES == 0, f"gmem_ptr {gmem_ptr} not BEAT_BYTES-aligned"
            assert smem_ptr % BEAT_BYTES == 0, f"smem_ptr {smem_ptr} not BEAT_BYTES-aligned"
            cmd = {
                "gmem": gmem_ptr,
                "smem": smem_ptr,
                "bytes": bytes_n,
                "bar_id": bar_id,
            }
            self._in_fifo.append(cmd)
            self.accept = 1
            # add_tx fires THIS cycle for the new command's byte total.
            self.add_tx_en = 1
            self.add_tx_bar_id = bar_id
            self.add_tx_bytes = bytes_n

        # 2. If idle and FIFO non-empty, start the next command.
        if self._cur is None and self._in_fifo:
            self._cur = self._in_fifo.popleft()
            self._bytes_transferred = 0

        # 3. If executing, transfer one beat per cycle (back-door).
        #    Paused when smem_wr_stall_in is high (parity with RTL).
        if self._cur is not None and not smem_wr_stall_in:
            n = BEAT_BYTES
            off = self._bytes_transferred
            chunk = self.gmem.dump(self._cur["gmem"] + off, n)
            self.smem.load(self._cur["smem"] + off, chunk)
            self._bytes_transferred += n

            if self._bytes_transferred >= self._cur["bytes"]:
                # Completion: sub_tx + arrive concurrently.
                self.sub_tx_en = 1
                self.sub_tx_bar_id = self._cur["bar_id"]
                self.sub_tx_bytes = self._cur["bytes"]
                self.arrive_en = 1
                self.arrive_bar_id = self._cur["bar_id"]
                self.done = 1
                self._cur = None
                self._bytes_transferred = 0

        # 4. busy reflects "any work pending or in flight"
        self.busy = 1 if (self._cur is not None or self._in_fifo) else 0
