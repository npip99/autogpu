# reset_seq — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against reset_seq.sv (compares to pymodel.reset_seq)
#   make clean      # remove build artifacts
#
# SCRUB_DEPTH is derived from config.SMEM_BYTES / NUM_BANKS / 4 so the SV
# parameter matches the pymodel's NUM_WORDS_PER_BANK.

SIM            ?= verilator

# Parallelize Verilator emit + build across all CPU cores. -j 0 auto-detects nproc.
EXTRA_ARGS     += -j 0
# Also parallelize the C++ compile step (cocotb invokes make on Vtop.mk without -j).
BUILD_ARGS     += -j $(shell sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
# Compile at -O0: simulation runtime is fine, and -O0 vs -Os is a big compile-time win.
BUILD_ARGS     += OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0
# Route compiles through ccache (no-op if ccache not on PATH).
BUILD_ARGS     += OBJCACHE=$(shell command -v ccache 2>/dev/null)
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/reset_seq.sv
TOPLEVEL        = reset_seq

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_reset_seq.py
COCOTB_TEST_MODULES = tb.test_reset_seq

# Make pymodel.reset_seq and common.tb_utils importable from the testbench.
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

# Run with the project's venv ACTIVATED:
#   cd reset_seq/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)

# Pull SCRUB_DEPTH out of config.py so SV matches pymodel.
SCRUB_DEPTH := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import SMEM_BYTES; print(SMEM_BYTES // 32 // 4)")

COMPILE_ARGS += -GSCRUB_DEPTH=$(SCRUB_DEPTH)

include $(shell cocotb-config --makefiles)/Makefile.sim
