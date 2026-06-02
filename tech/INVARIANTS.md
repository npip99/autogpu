# Invariants

High-level goals this codebase should always satisfy. **If you find one
violated, fix it OR document why temporarily — don't silently accept the
violation.** Each invariant has a one-line "how to check" so it can be
verified mechanically.

Complements:
- `tech/RCA_DISCIPLINE.md` — what to do when a build BREAKS (diagnostic process)
- `tech/FAILURES.md` — lookup table for known error codes

This file is what to keep true so failures don't HAPPEN in the first place.

---

## Build system

### B1. `run.sh` is idempotent and re-derives from RTL

Running `tech/asap7/orfs/run.sh <module>` twice in a row with no RTL
changes must produce bit-identical output the second time, OR detect
the cache is current and skip. Running it after RTL changes must
ALWAYS pick up those changes.

**Check:** `run.sh` aborts if `build/sv2v/chip_top_bcast*.v` mtime is
older than any `.sv` source. See `tech/asap7/orfs/run.sh:25-50`.

Violated 3× in #40 (takes 7-9 silently compiled B4 RTL because
sv2v output wasn't regenerated). Fix landed in
`run.sh` — staleness check now ERRORS rather than silently running
the wrong RTL.

### B2. sv2v outputs are explicit Makefile targets

Each `.v` file in `build/sv2v/` must have an explicit Makefile target
in `tech/sky130/Makefile`. Touching any RTL source file must mark
every dependent `.v` file out-of-date via standard `make` mtime rules.

**Check:** `make -C tech/sky130 sv2v sv2v-bcast-sweep sv2v-tiny-bcast-sweep`
recomputes incrementally. After `touch compute_array/compute_array.sv`,
re-running each target must rebuild the corresponding `.v` files.

### B3. Hardened macro configs are reproducible

Re-running `tech/asap7/orfs/run.sh <macro>` against the same SV + config
must produce a LEF/lib/GDS that's functionally equivalent (modulo
non-determinism in placement seed). Specifically:
- Macro tile size in DIE_AREA must match `<macro>.manifest.yaml` (when
  the manifest framework lands per #43)
- Pin coordinates in the LEF must match the `<macro>.pins.tcl` placement

**Check:** post-harden hook in `run.sh` could parse the resulting LEF
and assert pin coordinates match the input pins.tcl. Not implemented;
tracked.

### B4. No hand-edits to generated artifacts

`build/orfs/results/`, `build/sv2v/`, `build/render/` are all generated.
Hand-editing them is a bug — the next `make` run will silently undo
the edit. If you need a generated file changed, change the generator
(SV source, manifest, or generator script).

**Check:** the build directories are `.gitignore`'d, so accidental
commits are caught. There's no active check that someone edited a
generated file in-place.

### B5. New macros need ONE place to declare themselves

Adding a hardened macro to compute_array (or any abutment design)
should require editing the macro's RTL + ONE manifest file, no more.
Generated artifacts (pin TCL, macro_placement entries, IO_CONSTRAINTS
regions, parent SDC fragments) come from the manifest.

**Check:** see #43 for the manifest-driven framework spec. Not yet
landed.

### B6. Long-running ORFS work runs inside `screen`, never raw shell

So the build survives shell disconnect, has an addressable name, and
can be killed cleanly (raw `pkill` orphans the docker container).

```bash
screen -dmS harden-<macro> bash -c \
  'NUM_CORES=4 ./tech/asap7/orfs/run.sh <macro> 2>&1 | tee /tmp/h_<macro>.log'
# screen -ls | screen -r <name> | screen -X -S <name> quit
```

---

## RTL & macro design

### R1. Parent design is "macros + wires", not "macros + logic"

`compute_array.sv` and any other parent design should contain ONLY:
- Macro instantiations
- Parent-level wires connecting macro pins
- The thinnest possible glue (assigns, slices)

Specifically: NO long broadcast fan-out trees, NO N-cycle pipeline
registers, NO arithmetic. Anything bigger than a wire goes inside a
hardened macro.

**Check:** post-synth, `IFP-0105 Number of instances` for the parent
design should be << 1000. Today (post-B6) compute_array_abut has
~2,300 parent stdcells (status return pipes + tie cells + drain bus
aggregation). Goal: <500.

Currently violated by:
- `mb_pipe`/`md_pipe`/etc. (status return pipes ~ 45 flops) — should be
  absorbed into cmd_unit per #43-followup
- Drain bus mux logic — should become abutment chain like push_a/b

### R2. All inter-macro broadcasts via abutment chains

Any signal that fans out to more than ~4 distinct macros should travel
via an abutment chain (one register per macro hop) rather than as a
parent-level fan-out. The chain naturally provides per-stage routing
locality and per-stage CTS balance.

**Check:** post-synth, no parent-level net should have fanout > 8.
Inspect with `report_design` or grep synthesis stat.

**Why:** PR #34 originally tried to solve this via parent shift
registers (`pa_chain`/etc.) — that worked functionally but kept the
register loads at the parent CTS level, creating the 8K-endpoint clk
fanout that took 78 min to route. B6 moved the chains into hardened
macros, eliminating both problems.

### R3. Abutment-pair pins share coordinate

For any signal that "feedthroughs" between abutted macros (e.g.,
`chain_w_s[k]` and `chain_e_n[k]` on skew_lane_a), the pin coordinates
on the two abutting edges MUST match. When instances stack, the parent
net is zero-length.

**Check:** the manifest framework (#43) is the natural place to
enforce this. A `verify_abutment.py` script can parse the macro's LEF
and assert paired pins share coordinates on their respective edges.

Currently enforced by hand in `*.pins.tcl` files; one bug introduces
criss-cross routing the placer can't undo.

### R4. Hardened macros must self-describe their abstract .lib

The abstract Liberty file emitted by `write_timing_model` after harden
must accurately model the macro's internal sequential paths.
Specifically: an input → output path through an internal register
must emit setup/hold + CK→Q arcs (not a combinational arc).

**Check:** `grep -A5 'pin (chain_e_n' <macro>_typ.lib | grep
'related_pin : "clk'` should find clock-related timing arcs on
register outputs. If it shows `related_pin : "<input_pin>"`, the macro
SDC is broken — the internal flop's clock isn't being recognized.

Verified for skew_lane_a as of 2026-06-02.

### R4a. Modules expose only ports the design actually USES — never "future flexibility"

If no consumer in this design uses a module port, **it doesn't exist as
a port**. No defensive "expose this in case someone wants it later."
Every exposed port is a real cost — at minimum a LEF pin slot, often
TIE cells or buffer chains at the integrator, plus the synth/place/route
tool time to wire and verify it.

When a port is a candidate for "maybe future use":
- Make it a SystemVerilog `parameter` (zero cost at integration when
  defaulted; yosys constant-folds during synth)
- Or just don't add the port at all; add it when the first real
  consumer needs it. RTL is changeable.

The cost of "expose now, tie off later" pattern compounds quadratically
once a macro is instantiated many times (1024 mac_tmem_cell × 33 init
bits = 34K TIE cells at parent — see R4b for the mechanism).

**Check:** for every input port on a hardened module, grep every
top-level integrator. If every integrator ties the port to a constant
or leaves it unconnected, the port is dead weight. Move to parameter
or delete.

**Examples in this repo (violations to fix):**
- `mac_tmem_cell.sv`: `init_en` / `init_slot` / `init_data` — never driven
  in compute_array, always tied to constants. Should be parameters or removed.

### R4b. Hardened-macro input ports tied to constants at parent are expensive

When a hardened macro exposes input ports that the parent ties to a
constant (e.g. `.init_data(32'd0)`, `.init_en(1'b0)`), yosys CANNOT
constant-propagate through the hardened black box. It must drive each
port pin with a TIE cell (TIELOx1 or TIEHIx1). At scale:

    N hardened instances × W bits of tied input = N × W TIE cells

For 1024 mac_tmem_cell × 33 bits init = ~34,000 TIE cells. Each TIE
cell + its routing → wire congestion (#40 take-13: blocked GRT
convergence at mac mesh boundaries; identified via ODB query of stuck
nets in congestion-5.rpt).

**Rule:** if a macro exposes "optional" or "future-use" input ports
that this design's integration always ties off, ABSORB those ports
into the macro before hardening. Either:
- remove the port + dead-code-eliminate the receiving logic, OR
- make the port a SystemVerilog `parameter` (yosys can constant-fold
  during macro synth, no TIE cells needed at parent integration)

**Check:** post-synth, `synth_stat.txt` cell counts should not show
disproportionate TIELOx1/TIEHIx1 counts vs the rest of the design.
If TIE count is ~ N × W for some N instances and W tied input bits,
you're paying the cost. A simple script counting (TIE cell uses
× macro instance count × tied-input-port width) can detect.

Avoid: shipping a "v1 macro" with flexible-but-unused ports under
the assumption they'll be used "later" — the integration cost is
paid every time the macro is instantiated, even if every consumer
ties them off.

### R5. No "defensive zero" gating on handshake outputs

When a module exposes a `valid + data` interface, **the data lines must
be output ungated**. Adding `valid ? data : 0` at the source synthesizes
N AND gates per N output bits — invisible in the SV but expensive in
silicon. Multiply by buswidth × instance count and the cost gets large
fast.

The valid bit is the authoritative source of "is there real data here."
The downstream receiver gates its action (write enable, sample, etc.)
on valid. Whether the data lines show 0 or stale value during the
invalid cycle is don't-care — no consumer reads them.

**Example violated** (`compute_array.sv:569-570`, fix #43 follow-up):
```sv
// 1024 AND gates at parent for ZERO functional benefit
assign drain_row_data[gj*32 +: 32] = drain_row_valid ? drain_pipe[0][gj] : 32'd0;
// Correct:
assign drain_row_data[gj*32 +: 32] = drain_pipe[0][gj];
```

**Check:** code review of every RTL module's output assignments must
ask: "is this a handshake bus? does the receiver gate on valid? if
yes, the data side should be ungated." Synthesis stats review
(post-synth cell count broken down by type) catches this when the
AND count is disproportionate to the apparent logic.

### R6. Yosys cannot pass parameters into hardened macros

When a macro is hardened and loaded as a black box (`ADDITIONAL_LEFS`),
yosys can't propagate a parent's instantiation-time parameter into
it. Bake the value at hardening time (via `sv2v -D NAME=VALUE` or SV
parameter default) and use bare instantiations (no `#(.NAME(VAL))`)
at the parent. Pattern bit #40 twice (B5 BCAST_PIPE, B6 CHAIN_WIDTH).

**Check:** parent synth fails with "Module `X' ... does not have a
parameter named 'NAME'". When you hit this, remove the parameter
override at the instantiation site, not the parameter declaration.

---

## Tests / verification

### T1. Every hardened macro passes cocotb tests against its RTL

Before re-hardening a macro, the cocotb test for its RTL must pass.
The hardened LEF/lib is meaningless if the RTL it captures is buggy.

**Check:** `make -C <macro_dir> test` (or equivalent) in CI before
ORFS harden.

### T2. Multi-cycle chains have cycle-accurate cocotb tests

When RTL declares a chain like skew_a's 32-stage broadcast chain,
there must be a test that drives the chain HEAD with a marker pattern
and confirms each stage receives its value at the expected cycle. This
catches off-by-one errors (extra/missing register stage) that would
silently break the systolic schedule.

**Check:** `compute_array/tb/test_chain_timing.py` (planned per #43).
Currently NO cocotb test verifies the B6 chain's cycle count — relying
on STA + manual inspection only.

### T3. Tape-out blockers must use proper STA constraints, not workarounds

`set_multicycle_path`, `set_false_path`, `set_dont_touch`, etc. are
production STA constraints when used correctly (the design IS multi-
cycle, the path IS quasi-static, the net IS specifically intended to
not be optimized). They become "lies" when used to silence STA without
fixing the underlying timing.

**Check:** every `set_multicycle_path` / `set_false_path` in our SDC
files must have a code comment justifying it AND a corresponding cocotb
test confirming the design IS multi-cycle / quasi-static. No silent
workarounds.

---

## How to maintain

When adding a new feature/macro:
1. Check the invariants this feature touches (build system, RTL, tests)
2. Either satisfy them or document violation here with TODO
3. Add a "how to check" line so we can re-verify later

When debugging a failure:
1. First check `tech/FAILURES.md` for known errors
2. Then follow `tech/RCA_DISCIPLINE.md`
3. After resolving, check if the failure violated an invariant in this
   file and update the "how to check" if a stricter check could have
   caught it earlier

This file is meant to grow — every "I keep getting bit by X" pattern
should become an invariant + check here.
