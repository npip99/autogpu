# B1 — smem bank_rdata mux congestion

## Problem

`smem.sv` instantiates 32 fakeram banks (`NUM_BANKS=32`), each 32 bits
wide. A read produces a 32-byte (MMA_M=32) beat by selecting 8 banks via
address bits, then concatenating their `bank_rdata[i][31:0]` outputs.
Because the mux can pick *any* 8-of-32, **all 32 banks' 32-bit data
buses must wire into the per-bit mux logic — 32 × 32 = 1024 wires
converging on one mux region.**

On asap7's M3 (~480 nm pitch), a 30 µm inter-bank channel holds ~50–60
wires. 1024 wires across 3 horizontal channels = ~340 wires/channel
needed → 5–6× over capacity. Same on M2/M4.

Empirical:
- Standalone `smem.config.mk` (40 µm horizontal channels): stuck in
  GRT extra iterations indefinitely.
- chip_top run with smem inlined at 28 µm pitch: detail route plateaued
  at ~7,500 violations.
- chip_top with 40 µm pitch: same problem, slower convergence to ~7,200.
- DRC report confirms ~8000 of 8025 violations in a single horizontal
  channel between two bank rows.

This is **wire-count-per-channel by construction**, not a placement bug.
No amount of channel widening within reasonable area budget fixes it.

Affects:
- A6 chip_top (currently dodged by inlining smem; the bug just moves
  with it).
- Pool-B standalone smem hardening (cannot ship a smem.lef).
- Any future module that adopts the same wide-mux pattern.

## Acceptance criteria

1. Standalone `./tech/asap7/orfs/run.sh smem` reaches `6_final.gds`
   with 0 detail-route violations and no PSM-0069. Total wall time
   under 30 min on a quiet host.
2. The resulting `smem.lef` plugs into chip_top.config.mk's
   `ADDITIONAL_LEFS` (replacing the inlined-smem path), and chip_top
   re-runs cleanly through 6_final.
3. `pymodel/tests/test_e2e.py` and the cocotb `chip_top` end-to-end
   tests continue to pass — any RTL change must not break the
   functional contract (32-byte beats, 1-cycle read latency unless
   explicitly pipelined, byte-level forwarding for LOAD/RD conflicts).
4. The fix is documented in `DESIGN.md` (under a new "Mux fanout limits"
   section adjacent to "PDN" + "Hold-timing limitation").

## Constraints

- **Don't change the smem ports.** chip_top.sv connects to smem's port
  signature today; widening / changing port count cascades into LOAD,
  compute_array, cmdproc.
- **Don't break write-path forwarding.** The byte-level forwarding logic
  for LOAD-write-then-MMA-read in the same cycle has been cocotb-tested
  and must keep working.
- **Latency budget**: 1 extra cycle on the MMA read path is acceptable
  (cmdproc + compute_array's K-loop are not yet timing-critical). 2+
  cycles starts to bite throughput.
- **No new SRAM compiler.** The asap7 platform ships only the
  `fakeram7_256x32` macro (32-bit × 256 words). Solutions must compose
  from this primitive or accept that delivering a different macro is
  itself part of the proposed work.

---

## Candidate solutions

### Option 1 — Wider, fewer banks (4 × 256-bit logical banks)

**Idea.** Replace `NUM_BANKS=32` 32-bit-wide banks with `NUM_BANKS=4`
*logical* banks of 256 bits each. Each logical bank is built from 8
fakeram7_256x32 instances in parallel sharing the same address. The
mux becomes 4:1 per output bit, not 32:8.

**Wire count.** 4 × 256 = 1024 wires total (same as before), but they
converge to only 4 destinations. Each fakeram emits 32 wires to a
*local* per-logical-bank concentrator, not the chip-wide mux. A typical
32-byte beat = exactly one logical bank read with no concatenation mux.

**Why the bank-count change is fine here.** The 32-bank CUDA pattern
exists to feed 32 warp threads picking independent addresses. We don't
have warps — `chip_top.sv`'s FSM serializes LOAD and MMA through
barriers (`WAIT` instructions), so they never run concurrently. 32-way
sub-beat bank parallelism gives us nothing in practice. 4 logical
banks supports every access pattern this chip uses:
- LOAD writes 16 B/cycle → ½ of one logical bank
- MMA reads 32 B/cycle → exactly one logical bank
- Different addresses → different logical banks → no conflict

The only access pattern 32 banks would beat 4 banks on is sub-beat
random scatter — which doesn't exist in this chip and isn't needed
for transformer inference (Qwen3 etc.).

**RTL effort.** Medium. `NUM_BANKS=32` is a localparam; change to 4 and
rewrite the `bank_addr`/`bank_en` decode for 256-bit-wide reads. The 8
fakeram-per-bank arrangement plumbs one address to 8 in parallel and
concatenates their 32-bit outputs into a 256-bit logical
`bank_rdata[logical_bank]`. Per-bank forwarding logic becomes 4 ×
16-byte forwarding masks instead of 32 × 1-byte.

**Verification effort.** Medium. cocotb tests bind to smem ports, not
internals — pymodel + cocotb should pass unchanged if per-byte
forwarding is preserved. Fresh pymodel pass to confirm.

**Risk.** Low. The 8-fakeram-per-logical-bank arrangement matches what
real silicon SRAM compilers produce (wide banks built from narrow
primitives), so the macro layout is known-good.

**Wins.** Deletes the mux entirely — no convergence point. Each
logical bank can sit on its own quadrant of the die. Routability
collapses to "place 4 macros with normal channels." Standalone smem
hardens in minutes; chip_top consumes the resulting LEF as a black
box; the chip routes.

**Cost.** Modest area increase (8 fakerams in parallel may not pack as
tightly as 1 wide fakeram of the same total bits). Same total SRAM
capacity (16 KB).

**Recommendation: this is the right fix. It actually works.**

---

### Option 2 — Hierarchical mux with placed pipeline flop

**Idea.** Break the 32:8 mux into two stages with a flop between:
- Stage 1: group banks into 8 groups of 4. Each group has a local 4:1
  mux per bit producing a 32-bit group-output, registered locally.
- Stage 2: 8:1 mux per bit across the 8 registered group-outputs.

**Wire count.** Inside each group: 4 × 32 = 128 wires routing to a
local mux (fits in a 50 µm channel). Between groups: 8 × 32 = 256
wires to the second-stage mux — manageable.

**Latency.** +1 cycle on smem read. cmdproc + compute_array K-loop FSMs
need to add a cycle delay (small change).

**RTL effort.** Medium. Add `bank_rdata_stage1[8][31:0]` as flops fed
by 4:1 muxes. Stage-2 mux feeds rd_a_data/rd_b_data.

**Verification effort.** Medium-high. Latency change touches cmdproc
sequencing — pymodel needs to be updated to model the extra cycle.
cocotb e2e tests would need a 1-cycle adjustment in the issue-to-busy
window.

**Risk.** Medium. Pipelining a memory read is a known pattern; the
hazard is cmdproc/compute_array assumed combinational read.

**Wins.** Doesn't change bank count or organization. Mux fanout per
stage is small enough to route in normal channels.

**Cost.** 1 cycle latency. 32×8 = 256 flops added.

---

### Option 3 — Distributed reduction (systolic OR-tree)

**Idea.** Each bank's data is masked by its enable, then ORed up an
adjacency-aware OR-tree placed between physically adjacent bank pairs.
Total wire fanout is the same but each wire only travels to its
nearest neighbor; the tree's intermediate nodes consolidate.

**Wire count.** Per OR node: 2 in × 1 out. Wires never converge on a
central mux — they reduce locally. Total routed wire length is *less*
than the central-mux approach.

**RTL effort.** High. Requires laying out the OR-tree topology in
SystemVerilog, placing OR adders adjacent to bank pairs. Effectively
a manual physical design embedded in the RTL — yosys won't infer this
unless you write each OR adder explicitly with `(* keep_hierarchy *)`.

**Verification effort.** Low. Same functional behavior, just a
different physical structure. Pymodel unchanged.

**Risk.** Medium-high. Subtle bugs in the OR-tree topology can be
hard to verify (combinational fanin from many sources). Synthesis
may flatten the tree back into a single AND-OR mux.

**Wins.** No latency change. No bank reorganization. Wire density per
channel drops drastically because each wire is local.

**Cost.** Significant RTL complexity + tight coupling between RTL
hierarchy and floorplan.

---

### Option 4 — Narrower beats with K-cycle assembly

**Idea.** Reduce the smem output port width from 32 bytes (256 b) to
8 bytes (64 b) per cycle. compute_array reads a full 32-byte beat over
4 cycles. Per-cycle mux is 32:2 instead of 32:8, reducing output wires
4×.

**Wire count.** Mux output = 64 b per cycle. Per-bit mux fanin from
banks is unchanged (32 banks still all wire in) but output wire count
drops 4×, so the wires *leaving* the mux are sparse.

**Latency.** +3 cycles per read (4 cycles to assemble 32 bytes vs 1).

**RTL effort.** Medium-high. smem becomes a state-machine that returns
4 chunks. compute_array's MMA cycle becomes 4× slower per K-step.

**Verification effort.** High. Throughput change is observable; all
performance-sensitive tests need updating.

**Risk.** Low (functionally simple) but **high throughput cost** — 4×
slower matmul. Defeats the purpose of having SMEM in the first place.

**Wins.** Smallest mux fanout reduction without RTL restructure of
banks.

**Cost.** Unacceptable throughput hit. Not recommended.

---

### Option 5 — Per-bank registered rdata, no latency change to consumer

**Idea.** Add a flop on each bank's rdata output. The mux samples the
registered values. fakeram7 already produces registered rdata, so this
is just adding a *second* flop between bank and mux. Doesn't change
latency (1-cycle read becomes 1-cycle read, just with a different
clock-edge alignment of the rdata).

Wait — that doesn't help. The fanout problem is the wires from bank
*to* the flop, then flop *to* mux. Adding the flop doesn't reduce
either set of wires.

**Verdict: doesn't actually fix the problem. Drop this option.**

---

### Option 6 — Massive die spread (brute-force channels)

**Idea.** Floorplan only — no RTL change. Place the 32 banks at ~200 µm
horizontal pitch on a ~1500 × 800 die. Channels are 5× wider, supporting
1024 wires.

**RTL effort.** None.

**Verification effort.** None (functional behavior unchanged).

**Risk.** Low.

**Wins.** Smallest engineering effort.

**Cost.** Die area roughly 3× larger than current 450 × 400. Standalone
smem becomes ~600,000 µm² of mostly empty space (low utilization,
expensive on real silicon). At chip_top scale, this means the smem
region dominates the chip's footprint.

**When to use.** As an emergency tape-out bandaid only. Not a real fix
because the chip area cost is paid forever.

---

### Option 7 — Single wide SRAM macro

**Idea.** Replace 32 × fakeram7_256x32 with a single wide SRAM (256
bits × 256 entries, single port). Read produces a full 32-byte beat
in one cycle with no mux at all.

**Source of the wide macro.** asap7 platform doesn't ship one.
Options:
- Build with OpenRAM compiler targeting asap7 (~few days of work to
  set up the compiler + characterize).
- Build with the academic FakeMem macro generator (more flexible than
  fakeram7) and integrate it into the ORFS platform manually.
- Use the existing 8-fakeram-in-parallel approach (which is what
  Option 1 effectively does — same primitive, different organization).

**RTL effort.** Medium. smem.sv rewritten to wrap one wide SRAM
instead of 32 narrow ones. Forwarding logic still needed but
single-bank.

**Verification effort.** Medium. Functional behavior preserved if the
port shape stays compatible.

**Risk.** Medium. Tool chain risk — building a new macro for asap7 may
hit unforeseen issues (LEF generation, timing characterization).

**Wins.** Architecturally cleanest. No mux. No fanout.

**Cost.** Time to stand up the macro generator. Once set up, future
SRAM-heavy designs benefit too.

---

## Recommendation

**Option 1 (4 × wider banks).** It deletes the mux, which deletes the
routing problem. No latency cost, no architectural cost the chip
actually pays (we don't have warps), low RTL risk, known-good layout
pattern.

The 32-bank organization is a CUDA artifact for warp-parallel access.
This chip doesn't have warps, so the artifact isn't earning its area
or its routing-failure cost. Wider banks are the right organization
for matmul-heavy workloads (including transformer inference), which
is what this chip is for.

**Fallback if Option 1 hits unforeseen RTL issues:**
- Option 2 (hierarchical mux + 1 cycle latency) — keeps 32 banks but
  adds pipeline stage. Real cost is verification of the latency
  change across cmdproc/compute_array FSMs.

**Avoid Options 4, 5, 6** for tape-out:
- Option 4 hits throughput.
- Option 5 doesn't actually fix anything.
- Option 6 is a bandaid that triples chip area.

**Defer Options 3 and 7** as long-term cleanup — both are larger
engineering investments that aren't needed if Option 1 works.

## Chosen approach (as shipped)

**Region-partition — a variant of Option 1 that keeps the
fakeram7_256x32 primitive.** Rather than build physically 4× wider banks
(which asap7's fakeram library doesn't offer at the right depth), the 16
narrow 32-bit banks are split into 2 fixed regions of 8 banks:

- Region 0 (banks 0-7, addr `[0, 4096)`) serves **operand A** reads.
- Region 1 (banks 8-15, addr `[4096, 8192)`) serves **operand B** reads.

`region_of(addr) = addr[12]`. An `MMA_M=32`-byte A read spans all 8 banks
of region 0; an `MMA_N=32`-byte B read spans all 8 banks of region 1.
Because each bank feeds exactly one dword of exactly one read port, the
smem-level read beat is wired straight from the per-bank gated outputs —
**no central mux and no OR-tree**. The 512 (16 × 32) bank-data wires
split into two independent 8-bank fan-outs that route locally to their
consumer edge, which is what deletes the congestion.

Why this over Option 1's 4-wide banks:
- Keeps `sram_1rw` / `fakeram7_256x32` unchanged — no new macro, no
  width parameterization churn through `smem_bank`/`smem`.
- Conflict detection collapses to a 1-bit region compare (`addr[12]`)
  instead of bank-group indices (see `pymodel/smem.py`).
- Same congestion win as Option 1: the mux is gone.

Costs / constraints this introduces:
- **A and B operands must live in separate regions.** The read path
  hardcodes rd_a→banks 0-7 and rd_b→banks 8-15; `smem.sv` carries a
  sim-only assertion that fires if a read targets the wrong region.
- **SMEM is 8 KB, not 16 KB** (2 regions × 8 banks × 128 words × 4 B).
  See the SMEM_BYTES change in `config.py` and the double-buffer-fit
  note there.

## Out of scope

- IO pads / pad ring (chip_top follow-up, not smem)
- Compute_array internal routing — A1/A2 own those
- PDN macro_grid welding — A1 owns
- Hold timing — A2 owns
