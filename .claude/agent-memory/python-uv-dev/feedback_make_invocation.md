---
name: feedback-make-invocation
description: In this repo, cocotb makefiles must run with the venv on PATH; the right command from an agent is `uv run make -C <module>`.
metadata:
  type: feedback
---

For this project's cocotb `Makefile`s (e.g. `gmem/Makefile`, `tmem/Makefile`, `store/Makefile`), running `make` requires the venv to be active so `cocotb-config` and `python3` resolve to the venv's Python 3.12. The human-facing recipe is `cd <module>/ && source ../.venv/bin/activate && make`.

**Why:** The harness blocks `source .venv/bin/activate` and `PATH="...venv/bin:$PATH" make ...` patterns. But `uv run make -C <module>` works because `uv run` injects the venv into the child environment. The cocotb sim still finds the right Python interpreter via cocotb-config.

**How to apply:** When running RTL cocotb tests from this repo as an agent, use `uv run make -C /abs/path/to/<module>`. The Makefile's `$(shell python3 -c "...config import...")` calls may emit `ModuleNotFoundError` to stderr (system python3 lacks `config`), but the verilator `-G` parameter values still get populated correctly via the Makefile substitution — those errors are noise, not failure.
