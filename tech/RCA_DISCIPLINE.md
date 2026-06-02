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
