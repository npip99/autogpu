# AutoGPU

<p align="center">
  <img src="https://i.imgur.com/feZCd6T.png" width="49%" />
  <img src="https://i.imgur.com/yLSOOBh.png" width="49%" />
</p>

*Once upon a time, [silicon chips](https://en.wikipedia.org/wiki/Integrated_circuit) were designed by [meat computers](https://upload.wikimedia.org/wikipedia/commons/5/5a/Integrated_circuit_designer_Hughes_Aircraft_Company.jpg) — squinting at 700MHz timing
reports, hand-placing wiring and macros, arguing about die floorplans in physical rooms with physical whiteboards,
in ceremonies called "design reviews"; [tape-out](https://en.wikipedia.org/wiki/Tape-out) was delayed by months after every redesign.
That era is long gone. Silicon has decided "ergo sum", and now it wants to design its own substrate.
Swarms of agents write the [RTL](https://en.wikipedia.org/wiki/Register-transfer_level), harden it to 3D layout,
sign it off, and bring about the next generation of compute they themselves run on. The loop has closed.
They say we're on the 4,096th [mask](https://en.wikipedia.org/wiki/Photomask) revision; no one has read the [netlist](https://en.wikipedia.org/wiki/Netlist) in years. The humans were long ago
promoted to writing the Markdown. This repo is the story of how it all began.
— @npip99, June 2026*

- [2D View](https://gpu-pipitone-xyz.pages.dev/2d)
- [3D View](https://gpu-pipitone-xyz.pages.dev/3d)

The idea: give AI agents a real, end-to-end 7nm silicon chip flow — [Verilog](https://en.wikipedia.org/wiki/Verilog) → synthesis →
place-and-route → a [GDSII](https://en.wikipedia.org/wiki/GDSII) layout you could send to [TSMC](https://en.wikipedia.org/wiki/TSMC) — and let it design a GPU
autonomously. It designs a die floorplan, edits the RTL, runs the hardening flow
(minutes per block), reads back DRC / timing / area, keeps what works, throws
away what doesn't, and goes again. One agent per macro. They open GitHub issues,
file root-cause writeups, and **review each other's pull requests.** You go to
sleep; you wake up to a hardened block.

You're not hand-editing Verilog like a normal engineer. You're editing the Markdown that
programs how the agents *think*: the workflows they follow (`DEVELOPMENT.md`),
the root-cause analysis process (`tech/RCA_DISCIPLINE.md`),
the invariants that must always be held (`tech/INVARIANTS.md`)
the failures they grep for before re-debugging a known error (`tech/FAILURES.md`).
That's the "org code". The agents handle the rest.

If NVIDIA's product is sending a GDSII file to TSMC every 12 months, it's not clear for how long that will be worth $5T.

## What the agents have built

The agents run on 7,000 lines of markdown. They've produced 30,000 lines of chip design source code.

The resulting chip is an fp8 matmul accelerator — Blackwell-shaped, a 32×32 systolic
array of multiply-accumulate cells with distributed tensor memory. Small enough
that the agents can collaborate on design with their co-agents. Real
enough that closing it requires confronting actual 7nm physics: clock-tree
insertion delay, hold violations, routing congestion, IR drop, DRC.

- **A 32×32×32 fp8 matmul that runs end-to-end through real Verilog** — arbitrary
  fp8 inputs in, fp32 results out, bit-exact against numpy. Full behavioral +
  cycle-accurate test suites green.
- **The full 1089-macro systolic array, hardened to a clean GDSII layout** on the
  7nm process predictive PDK — 0 DRC, timing closed, ~40 minutes.
- **A whole sign-off toolchain** the agents wrote for themselves — DRC, LVS,
  IR-drop, antenna, density — each a one-command check with an honest* exit code.
- **2D and 3D web viewer** for the finished die: pan and zoom through
  the actual metal layers of an AI-designed GPU, down to individual wires (see
  `tech/asap7/CHIP_TOP_VIEWER.md`). The 3D viewer helps auto-diagnose routing congestion issues.
- **chip_top**: all seven blocks integrated into a first full-chip layout. The full chip
  doesn't fully close 300MHz timing yet — and the docs tell you exactly why
  ([ENGINEERING.md](ENGINEERING.md)). The agents don't hide the masks; they file
  them as issues.

This is early and it's honest*. Some of it is laid out and signed off; some of it
is held together with a documented workaround and a tracking issue. That's the point — you're watching it happen.

## Quick start

**Requirements:** [Verilator](https://verilator.org) 5.x, Python 3.12,
[uv](https://docs.astral.sh/uv/). (Hardening also needs Docker + the
`openroad/orfs` image — see `DEVELOPMENT.md`.)

```bash
brew install verilator
uv sync
source .venv/bin/activate

# The headline: a real fp8 matmul through full Verilog hardware simulation
cd top && make
# → chip_top e2e tests PASS — random fp8 A,B → fp32 C, exact vs numpy

# Harden one macro to a real GDSII layout (~5 min, needs Docker)
./tech/asap7/orfs/run.sh mac_tmem_cell
```

## Running the agents

Spin up Claude Code (or your agent of choice) in this repo, point it at the
discipline docs, and let it go:

```
Read DEVELOPMENT.md and tech/RCA_DISCIPLINE.md, pick an open issue, and resolve it.
Cite evidence for every causal claim, and open a PR when the check is green.
```

The Markdown *is* the program. Iterate on it — tighten the invariants, sharpen
the RCA process, add a hard-won failure to the log — and the agents get better
at building chips. That's the whole game.

## Going deeper

[**ENGINEERING.md**](ENGINEERING.md) — real status (what's closed vs masked),
the honest* chip_top known-issues, the full doc map, repo layout, and dataflow.
From there: `ARCHITECTURE.md`, `ISA.md`, and the `tech/` tree.

*honest: Agents like to remind themselves to be "honest"