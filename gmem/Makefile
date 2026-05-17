# gmem — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against gmem.sv (compares to pymodel.gmem)
#   make clean      # remove build artifacts
#
# GMEM_BYTES and BEAT_BYTES are baked in from config.py via Verilator -G flags
# so the SV parameters always match what the pymodel uses.

SIM            ?= verilator

# Parallelize Verilator emit + build across all CPU cores. -j 0 auto-detects nproc.
EXTRA_ARGS     += -j 0
# Also parallelize the C++ compile step (cocotb invokes make on Vtop.mk without -j).
BUILD_ARGS     += -j $(shell sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
# Compile at -O0: simulation runtime is fine, and -O0 vs -Os is a big compile-time win
# for the 1024 fp32_fma instances. Switch to -O2 if you need fast sim runtime.
BUILD_ARGS     += OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0
# Route compiles through ccache (no-op if ccache not on PATH).
BUILD_ARGS     += OBJCACHE=$(shell command -v ccache 2>/dev/null)
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/gmem.sv
TOPLEVEL        = gmem

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_gmem.py
COCOTB_TEST_MODULES = tb.test_gmem

# Make pymodel.gmem and common.tb_utils importable from the testbench.
# (sim is invoked from this directory; project root is one level up.)
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

# Run with the project's venv ACTIVATED:
#   cd <module>/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)
# Make's `export PATH` does NOT propagate to $(shell), so we can't auto-fix
# this from inside the Makefile — venv must be on PATH before `make` is invoked.

# Pull constants out of config.py so the SV parameters match the pymodel.
GMEM_BYTES := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import GMEM_BYTES; print(GMEM_BYTES)")
BEAT_BYTES := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import BEAT_BYTES; print(BEAT_BYTES)")

COMPILE_ARGS += -GGMEM_BYTES=$(GMEM_BYTES) -GBEAT_BYTES=$(BEAT_BYTES)

include $(shell cocotb-config --makefiles)/Makefile.sim
