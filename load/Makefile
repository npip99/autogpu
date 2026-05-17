# load — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against load.sv (compares to pymodel.load)
#   make clean      # remove build artifacts
#
# BEAT_BYTES, NUM_BARRIERS, INSTR_FIFO_DEPTH, GMEM_BYTES, SMEM_BYTES, MMA_M,
# MMA_N are baked in from config.py via Verilator -G flags so the SV
# parameters always match what the pymodel uses.

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
VERILOG_SOURCES = $(PWD)/load.sv \
                  $(PWD)/load_tb_top.sv \
                  $(PWD)/../gmem/gmem.sv \
                  $(PWD)/../smem/smem.sv \
                  $(PWD)/../barrier/barrier.sv
TOPLEVEL        = load_tb_top

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_load.py
COCOTB_TEST_MODULES = tb.test_load

# Make pymodel.load and common.tb_utils importable from the testbench.
# (sim is invoked from this directory; project root is one level up.)
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

# Run with the project's venv ACTIVATED:
#   cd load/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)
# Make's `export PATH` does NOT propagate to $(shell), so we can't auto-fix
# this from inside the Makefile — venv must be on PATH before `make` is invoked.

# Pull constants out of config.py so the SV parameters match the pymodel.
BEAT_BYTES       := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import BEAT_BYTES; print(BEAT_BYTES)")
NUM_BARRIERS     := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import NUM_BARRIERS; print(NUM_BARRIERS)")
INSTR_FIFO_DEPTH := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import INSTR_FIFO_DEPTH; print(INSTR_FIFO_DEPTH)")
GMEM_BYTES       := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import GMEM_BYTES; print(GMEM_BYTES)")
SMEM_BYTES       := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import SMEM_BYTES; print(SMEM_BYTES)")
MMA_M            := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_M; print(MMA_M)")
MMA_N            := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_N; print(MMA_N)")

COMPILE_ARGS += -GBEAT_BYTES=$(BEAT_BYTES) \
                -GNUM_BARRIERS=$(NUM_BARRIERS) \
                -GINSTR_FIFO_DEPTH=$(INSTR_FIFO_DEPTH) \
                -GGMEM_BYTES=$(GMEM_BYTES) \
                -GSMEM_BYTES=$(SMEM_BYTES) \
                -GMMA_M=$(MMA_M) \
                -GMMA_N=$(MMA_N)

include $(shell cocotb-config --makefiles)/Makefile.sim
