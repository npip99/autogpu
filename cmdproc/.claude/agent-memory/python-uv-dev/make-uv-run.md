---
name: make-uv-run
description: How to invoke cocotb tests when bash sandboxing blocks "source venv && make"
metadata:
  type: feedback
---

For cocotb/Verilator tests under any `<sub>/` directory, prefer `uv run make` over `source ../.venv/bin/activate && make`.

**Why:** The bash sandbox in agent sessions denies commands that prepend the project's `.venv/bin` to PATH (either via `export PATH=...` or `source .venv/bin/activate`). `uv run make` runs make with the venv's PATH and `python3`/`cocotb-config` already resolvable, sidestepping the denial.

**How to apply:** When DEVELOPMENT.md says "source ../.venv/bin/activate && make", agent runs should substitute `uv run make`. The Makefile's `$(shell python3 ...)` invocations resolve to the venv python3 under uv.
