# RCA discipline: never guess

When an ORFS/OpenROAD build fails or hangs, follow this process before
proposing any fix. The point is: **every causal claim must trace back to
direct evidence** — an observed log line, a perf sample, a gdb stack, a
diff. If you cannot point at the evidence, you do not have a diagnosis,
you have a hypothesis. Hypotheses are fine — write them down and design
experiments to test them. They are not diagnoses.

This file exists because past sessions extrapolated from partial evidence
("perf showed GRT spinning therefore the cause is the 8K-fanout clk net" —
plausible, not proven). When the "fix" then didn't work, the next round
guessed again. Iterate.

## Cardinal rules

1. **Every claim of "X causes Y" must cite the evidence on the same line.**
   - GOOD: "GRT is spinning on a single net — perf shows 97% in
     `mazeRouteMSMDOrder3D` (`/tmp/orfs.perf`)."
   - BAD: "GRT is spinning on push_now_piped because it has high fanout."
   - The second sentence is a hypothesis. Mark it as one.

2. **If you don't know, say "I don't know" and propose how to find out.**
   Do not paper over uncertainty with confidence-sounding language ("almost
   certainly", "very likely", "this should be"). Either you have evidence
   or you have an experiment to run.

3. **Run the smallest experiment that distinguishes hypotheses.**
   Don't change five things and re-launch a 30-minute build. Change one,
   run, observe.

4. **Preserve failure state before mutating.**
   - Copy logs out of the build dir before `rm -rf`.
   - `perf record` while the process is alive.
   - gdb attach + thread apply all bt before kill.
   - Without preserved state you cannot iterate on the diagnosis.

5. **Distinguish "fix" from "workaround".**
   A workaround sidesteps a symptom (`-allow_congestion` exits GRT without
   resolving overflow). A fix removes the cause (removing the 16K parent
   flops that caused 8K clk fanout). Document which one you are
   applying. Workarounds are valid when paired with a tracked issue to
   come back to the real cause.

6. **When a build is stuck (slow or not advancing), RCA first, kill second.**
   Let it keep running while you investigate (logs, metrics JSON, partial
   reports). Kill only once RCA has produced either (a) an actual fix to
   retry with, or (b) a new diagnostic / instrumentation to add so the
   next iteration captures what this run was missing. Don't kill on
   suspicion alone — that just resets the clock without changing anything.

## The process

### 1. Capture the symptom

The first artifact is the exact failure: error message, the stage it
failed in, wall time at failure, output files at point of failure.

```bash
# Stage progression and last meaningful output
ls -t $BUILD_DIR/base/ | head -20
# Error / fatal lines
grep -E "DONE|Error|ERROR|fatal|FAILED|RSZ-|GRT-|DRT-|CTS-" $LOG | tail -20
# Stage wall time (extract per-stage from log if available)
grep -E "Elapsed time|Took.*seconds" $LOG | tail -10
```

Record this in the failure report verbatim. Do not paraphrase.

### 2. Localize to a single stage / file / function

Which ORFS stage emitted the symptom? Which file in
`build/orfs/results/asap7/<design>/base/` is the highest-numbered? That's
where you are.

If the build is hung (not errored), is the process actually working?

```bash
# Is OpenROAD doing CPU work or stuck in a syscall?
PID=$(pgrep -f openroad | grep -v "sg docker\|sh -c\|docker run" | head -1)
ps -p $PID -o pid,state,pcpu,time,rss
# state R = running (doing CPU work)
# state D = uninterruptible sleep (I/O or kernel — investigate)
# state S = sleeping — check wchan
cat /proc/$PID/wchan
```

If CPU is pegged (`pcpu` ~100+%), the process is alive and working.
If CPU is near 0 and state is S/D, it's blocked — investigate `wchan`.

### 3. Find which function is the bottleneck

Never guess "the bottleneck is X" — measure it.

```bash
# 5-second perf sample of the running thread
sudo perf record -p $PID --call-graph fp -o /tmp/orfs.perf -- sleep 5
sudo perf report -i /tmp/orfs.perf --stdio --no-children | head -20
```

The "Overhead" column at the top gives you the dominant function with its
call stack. **This is evidence.** Cite the perf output by path when you
reference it later.

For multi-threaded apps, check per-thread CPU before sampling: only the
threads in state `R` are doing work. Sampling an idle thread gives noise.

```bash
ps -L -p $PID -o tid,pcpu,state,comm --sort=-pcpu | head -10
```

### 4. Identify the specific object inside that function

This is the step the past session usually skipped. Knowing "97% in
`mazeRouteMSMDOrder3D`" tells you the maze router is the bottleneck — it
does NOT tell you which net it is routing. The net identity is what
determines the fix.

For OpenROAD specifically:

```bash
# Attach gdb to the running process (non-destructive)
sudo gdb -p $PID --batch -ex "thread apply all bt" 2>&1 | tail -80
```

For deeper introspection, walk the OpenROAD data structures. The class
hierarchy and field names are in
`/OpenROAD-flow-scripts/tools/OpenROAD/src/grt/include/grt/`. Print the
current `FrNet*` being processed:

```bash
# Inside gdb prompt (interactive):
# (gdb) frame N    # frame N is the mazeRouteMSMDOrder3D frame
# (gdb) p net_name # or whatever local variable holds the current net
```

If gdb introspection is fragile because of optimization, the fallback is
to rebuild OpenROAD with debug logging that prints `net->getName()` at the
start of each maze-route call, then re-run the build. This is heavier
but yields a definitive trace.

A third option: enable OpenROAD's verbose-routing options if they exist
for the stage in question (search the stage's `.tcl` file for `verbose`
flags).

#### Stage-specific shortcuts

**GRT** writes a `congestion-N.rpt` to `build/orfs/reports/asap7/<design>/base/`
every `-congestion_report_iter_step` iters (default 5). Each entry has
`bbox` (g-cell coords), `comment` (capacity:N usage:M overflow:K), and
`srcs:` (the nets passing through). This is the cheapest, most direct
GRT diagnostic — works without killing the build, without source code,
without debug symbols. **Method validated 2026-06-02:**

```bash
DESIGN=compute_array_abut
RPT=build/orfs/reports/asap7/$DESIGN/base
# Convergence trend (line count proxies violation count)
wc -l $RPT/congestion-*.rpt
# Persistent nets at the LAST iter report (= the stubborn ones)
grep -oE "net:net[0-9]+" $RPT/congestion-10.rpt | sort -u
# Hot bbox locations
grep "bbox" $RPT/congestion-10.rpt | sort -u | head -20
# Translate net IDs to design signals (resizer-created names won't show up)
grep -rln "<netname>" build/orfs/results/asap7/$DESIGN/base/1_2_yosys.v
# If empty: net was created by resizer, check 3_resizer.rpt for the path
grep -A30 "report_checks" $RPT/3_resizer.rpt
```

A `net*` name not found in `1_2_yosys.v` (synth pre-resizer output) means
the net was created by the resizer when it inserted a buffer. Then the
critical-path entries in `3_resizer.rpt` tell you which RTL signal that
buffer chain belongs to.

#### Tracing a specific net to its RTL signal via OpenROAD TCL

When the congestion report names a net but `3_resizer.rpt` doesn't
cover it (or it's a TIE-cell-driven net, dummy buffer, etc.), query
the ODB directly. Useful when the resizer report's critical-path
samples don't cover the actual congestion source — you can pick ANY
specific net from `congestion-N.rpt`'s `srcs:` list and trace it.

```tcl
read_db /work/build/orfs/results/asap7/<design>/base/3_place.odb
set block [ord::get_db_block]
set net [$block findNet $net_name]
foreach iterm [$net getITerms] {
    set inst [$iterm getInst]
    set pin_name [[$iterm getMTerm] getName]
    set master_name [[$inst getMaster] getName]
    set bbox [$inst getBBox]
    set x [expr [$bbox xMin] / 1000.0]
    set y [expr [$bbox yMin] / 1000.0]
    puts "  [$inst getName] / $pin_name @ ($x, $y) master=$master_name"
}
```

Run via:
```bash
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $(pwd):/work -v /tmp/trace.tcl:/tmp/trace.tcl \
    openroad/orfs@sha256:cf4186a5e6a52eddcad1e53e55e1571dbd6711a8e5e687cdb2a8bdc62bc20f1d \
    /OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad -exit -no_init -no_splash /tmp/trace.tcl"
```

Used 2026-06-02 to identify the W/E mac mesh boundary congestion as
TIE cells driving mac_tmem_cell.init_data pins — 34,000 TIE→init
wires from parent perimeter into mac mesh interior. Could not have
been identified from `congestion-N.rpt` alone (it shows net IDs and
bboxes but not what the net DOES). Led to INVARIANTS.md R4a (don't
expose unused ports) + the mac_tmem_cell port pruning.

### 5. Form ONE testable hypothesis

After steps 1-4 you have:
- exact symptom
- stage / file
- dominant function (from perf)
- the specific object the function is processing (from gdb / debug log)

Now write a single sentence:

> Hypothesis: <object> causes <function> to spin because <mechanism>.

Then list at least one alternative hypothesis that the evidence does NOT
yet rule out. Be honest about what you have and have not ruled out.

### 6. Design the experiment

Pick the experiment that distinguishes your hypothesis from at least one
alternative. The experiment should:
- Run faster than the original failure (you want fast feedback).
- Change exactly one thing.
- Have a binary outcome that maps to confirm/refute.

If your only available experiment is "re-run the full build with a
config change" and the build takes 30 minutes, sketch out what each
possible outcome means BEFORE launching. Otherwise you'll get a fail
and start guessing again.

### 7. Apply fix vs workaround — explicitly

After step 6 confirms a hypothesis, decide:
- **Fix**: remove the cause. Track in a regular issue/PR.
- **Workaround**: paper over the symptom. Document the workaround AND
  open a tracking issue for the real fix. A workaround without a follow-up
  is technical debt.

In commit messages and config files, label workarounds:

```mk
# WORKAROUND (#XX): cap GRT iters because <root cause> not yet fixed
export OR_GLOBAL_ROUTING_ARGS = -congestion_iterations 5
```

### 8. Update FAILURES.md

If the failure recurs in the future, the FAILURES.md entry should be the
first lookup. Include:
- exact error string (greppable)
- stage that emits it
- the diagnosis chain from steps 1-4 (cite evidence)
- the fix vs workaround applied
- link to the PR/issue

## Tools — OpenROAD/ORFS cheatsheet

| Tool | Purpose | Command |
|---|---|---|
| `perf record` / `perf report` | dominant function | `sudo perf record -p $PID --call-graph fp -- sleep 5 && sudo perf report -i perf.data --stdio` |
| `gdb` attach | live stack / object inspection | `sudo gdb -p $PID --batch -ex "thread apply all bt"` |
| `/proc/$PID/wchan` | what kernel call (if blocked) | `cat /proc/$PID/wchan` |
| `/proc/$PID/syscall` | current syscall (if any) | `cat /proc/$PID/syscall` |
| `ps -L -p $PID` | per-thread state & CPU | `ps -L -p $PID -o tid,pcpu,state,comm --sort=-pcpu` |
| `top -bH -p $PID` | live per-thread CPU | `top -bH -p $PID -n 1` |
| ORFS stage logs | per-stage detail | `tail $BUILD_DIR/logs/asap7/$DESIGN/base/<stage>.log` |
| ORFS metrics JSON | numeric metrics per stage | `jq . $BUILD_DIR/logs/asap7/$DESIGN/base/<stage>.json` |
| KLayout DRC reports | DRC violation list with coords | `klayout -b -r drc.lydrc -rd in=$GDS -rd out=drc.lyrdb` |
| OpenROAD verbose flags | per-iter detail (where supported) | check stage `.tcl` for `-verbose` |

## Common false-confidence patterns to avoid

- **"perf shows X is the bottleneck therefore X is the cause"** — perf
  shows what the CPU is doing. The CAUSE might be the input data to X
  (e.g., a pathological net), not X itself. Always identify the specific
  object X is processing.

- **"fixing X helped therefore X was the cause"** — partial fixes can
  reduce the symptom without addressing the root cause; the bug returns
  later in a different form. Verify by REVERTING the fix and confirming
  the symptom recurs exactly.

- **"the log went silent therefore the process is stuck"** — many
  OpenROAD stages buffer output. Check `ps state` and CPU% before
  declaring stuck.

- **"my mental model says..." with no measurement** — discard. Measure
  or don't claim.

- **"this should be fast because..."** — capacity arguments without
  measurement are not evidence. The same hardware running the same
  algorithm with different input data can vary 1000x. Measure.

## Past sessions where this discipline was violated

### Session 2026-06-01 — #40 take-2 GRT hang

- Symptom: GRT extra-iter 16/30 ran 78+ min with no log progress.
- Evidence captured: perf showed 97% in `mazeRouteMSMDOrder3D` ✓
- Claim made: "stuck on the 8K-endpoint chip clk net" — NOT PROVEN.
  Could equally have been any high-fanout broadcast (push_now_piped,
  push_slot_piped, etc.). Should have run gdb to identify net.
- Fix applied based on the unproven claim: B4 (remove parent pa_chain).
- Result: B4 dropped clk fanout 8K → 140. Take-4 still hung GRT at the
  same function. The "fix" was actually a partial reduction — the real
  cause (whichever net actually causes the spin) was not addressed.
- Lesson: gdb-step to identify the SPECIFIC net would have changed the
  diagnosis and possibly the fix.

When this session continues, the next gdb investigation should be:

```bash
PID=$(pgrep -f openroad | grep -v "docker" | head -1)
sudo gdb -p $PID --batch \
    -ex "thread apply all bt 30" \
    -ex "frame 0" \
    -ex "info locals" \
    -ex "info args" 2>&1 > /tmp/orfs.gdb.log
```

Then map the local variable that holds the current net (`net`, `n`,
`current_net` — depends on OpenROAD version) back to a name via
`db->getNetByIndex(idx)->getName()` or similar.

### Session 2026-06-02 — #40 take-6 B5 made the problem worse

- Symptom: B4 take-5 had 20 stuck nets at iter 10. Hypothesis: absorbing
  parent BCAST_PIPE registers into cmd_unit (B5) would remove the long
  parent-flop→skew_lane wires that the resizer was buffering through
  the W mac boundary.
- Evidence before B5: diagnose_grt.sh identified resizer-inserted
  buffer chains from `ps_pipe[0]` and `pn_pipe[0]` (parent BCAST_PIPE
  flops) to skew_b[0] and gen_row[31][0]. ✓ The buffers were real.
- What I MISSED: the parent `pa_pipe`/`pb_pipe` flops are
  TIMING-DRIVEN placed — ORFS places them NEAR their loads (along the
  skew_lane column / row). Moving the flops INSIDE cmd_unit pinned the
  source at SW corner, replacing "32 placer-spread sources to nearby
  loads" with "1 fixed source to 32 distant loads."
- Result: B5 made it WORSE (102 stuck nets vs B4's 20). Same congestion
  pattern, more buffer chains required because the source no longer
  spreads with its loads.
- Lesson: **when reducing a SOURCE's flop COUNT, also check that the
  source's POSITION FREEDOM isn't being reduced**. A flexibly-placeable
  source can beat a smaller-but-pinned source, especially for
  high-fanout broadcasts. The placer's freedom is a hidden architectural
  feature — moving flops into a macro takes that freedom away.

The correct architectural answer (only learned after B5 failed):
**chain the broadcast through skew_lane abutment ports**, the same
pattern as the existing clk feedthrough. Per hop the signal is registered
inside one macro and routed only ~35 µm to the next macro's chain
input. No long fanouts ANYWHERE — not from cmd_unit, not from any
parent flop. This is the only structure where the placer's freedom and
the macro flop count are both optimal.

### Session 2026-06-02 — #40 B6 takes 7/8/9 ran B4 silently (sv2v staleness)

- Symptom: B6 RTL added 260-bit chain ports to skew_lane_a/b + chain
  wiring in compute_array.sv. Three 32×32 build attempts (takes 7, 8, 9)
  all failed at GRT with the SAME failure pattern as the previous B4
  attempts — long buffer chain on push_now from u_cmd to skew_a[31].
- Evidence captured: resizer report showed `u_cmd/push_now_o → ~14
  buffer cells → gen_a_skew[31].u_a/push_now → edge_byte → mac[31][0]`
  as a SINGLE-CYCLE combinational path. That's the B4 fan-out pattern,
  not B6's registered chain. ✓
- Claim made: "abstract .lib of skew_lane treats chain as combinational"
  — DISPROVEN by inspecting the .lib (`chain_e_n[0]` has correct
  `timing { related_pin: clk_w; timing_type: rising_edge }` clk-to-Q arc).
- Root cause (found by inspecting mtimes):
  `build/sv2v/chip_top_bcast1.v` mtime was BEFORE the B6 RTL edits.
  The Makefile target `sv2v-bcast-sweep` (which produces
  `chip_top_bcast1.v`) had not been re-run after I modified the SV
  sources. The 32×32 build was compiling against a stale chip_top
  that still had B4's compute_array logic. The hardened skew_lane
  macros DID have B6's chain ports — but compute_array.v never
  connected them, so the chain register inputs floated and the build
  silently fell back to the B4 broadcast pattern.
- I had run `make sv2v` (produces `chip_top.v` for standalone macro
  hardening, MMA=4 tiny) but NOT `make sv2v-bcast-sweep` (produces
  `chip_top_bcast{0,1,2,3}.v` for compute_array_abut). Different
  targets, different outputs.
- Lesson: **before any 32×32 build, verify the sv2v output mtime is
  newer than every modified RTL file**. The build system doesn't
  cross-check this. Specifically for compute_array_abut, the relevant
  artifact is `build/sv2v/chip_top_bcast1.v`. Recipe to add to every
  pre-launch checklist:

```bash
ls -la build/sv2v/chip_top_bcast1.v \
       compute_array/compute_array.sv \
       skew_lane/*.sv \
       cmd_unit/cmd_unit.sv \
       mac_tmem_cell/mac_tmem_cell.sv
# verify chip_top_bcast1.v mtime is the most recent
```

  If chip_top_bcast1.v is older than ANY RTL source, run:
```bash
make -C tech/sky130 sv2v-bcast-sweep
```
  BEFORE launching the build.

  This should ideally be enforced by the Makefile chain
  (run.sh should depend on sv2v output being newer than RTL).
  Tracked separately.

### Pattern: yosys cannot pass parameters into a hardened (black-box) macro

Bit me TWICE in #40 (B5 cmd_unit BCAST_PIPE and B6 skew_lane CHAIN_WIDTH).
When a macro has been hardened — its LEF + abstract .lib are loaded
as a black box during compute_array synth — yosys treats the cell as
opaque. Yosys then errors:

```
ERROR: Module `<macro>' referenced in module `<parent>' in cell `<inst>'
       does not have a parameter named '<NAME>'.
```

even when the SV source declares the parameter. The fix is to NOT pass
parameters at instantiation. Bake the value in via:
- the macro's standalone sv2v command (`-D NAME=VALUE`), OR
- the parameter's default value in the macro's SV (`parameter int NAME = K`)

The hardened LEF/.lib captures one chosen value; subsequent
instantiations must match that value implicitly.

If you genuinely need a parameterized macro at parent synth, you must
either harden it separately for each value (creating multiple LEFs),
or accept full Verilog inclusion (`SYNTH_HIERARCHICAL=1` + no LEF) so
the parameter can be resolved.

### Mistake patterns to internalize

- **Don't optimize one dimension blind to another.** B5 optimized "flop
  count at parent" but blew up "physical fanout distance." Both
  matter — measure both before deciding.
- **A working partial fix is data — try TWO fixes before committing
  to one.** B4 took-5 had only 20 stuck nets at iter 10 — that's the
  evidence that B4's direction was correct. Should have validated B4
  with the GRT cap working (which it wasn't due to the env-var name
  bug) before adding B5 on top.
- **Verify EVERY config knob actually takes effect.** Set
  `OR_GLOBAL_ROUTING_ARGS` for two iterations of debugging before
  noticing the correct name was `GLOBAL_ROUTE_ARGS`. Recipe: grep the
  flow scripts for the var name AT THE TIME OF SETTING IT, not after
  the fact. ORFS docs are `variables.yaml` — single source of truth.
- **One re-harden costs ≥ 10 min — make it count.** Re-hardening
  cmd_unit for B5 was a forward-only decision; the wasted re-harden
  was the cost of skipping step 2 above. Before re-hardening: list
  what the experiment will prove vs refute, and what it WON'T tell
  you.
