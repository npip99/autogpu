# cmdproc — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against cmdproc + full Phase 4 pipeline
#   make clean      # remove build artifacts
#
# Parameters are pulled from config.py via Verilator -G flags so the SV
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
VERILOG_SOURCES = $(PWD)/cmdproc.sv \
                  $(PWD)/cmdproc_tb_top.sv \
                  $(PWD)/../gmem/gmem.sv \
                  $(PWD)/../smem/smem.sv \
                  $(PWD)/../tmem/tmem.sv \
                  $(PWD)/../barrier/barrier.sv \
                  $(PWD)/../mma/mma.sv \
                  $(PWD)/../load/load.sv \
                  $(PWD)/../store/store.sv \
                  $(PWD)/../reset_seq/reset_seq.sv \
                  $(PWD)/../common/fpnew/cf_math_pkg.sv \
                  $(PWD)/../common/fpnew/fpnew_pkg.sv \
                  $(PWD)/../common/fpnew/fpnew_classifier.sv \
                  $(PWD)/../common/fpnew/fpnew_rounding.sv \
                  $(PWD)/../common/fpnew/fpnew_fma.sv \
                  $(PWD)/../common/fpnew/lzc.sv \
                  $(PWD)/../common/fp32_fma.sv \
                  $(PWD)/../common/fp8_decode.sv \
                  $(PWD)/../common/fp8_encode.sv
TOPLEVEL        = cmdproc_tb_top

# Include path for "common_cells/registers.svh" inside fpnew sources.
COMPILE_ARGS += +incdir+$(PWD)/../common/fpnew

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_cmdproc.py
COCOTB_TEST_MODULES = tb.test_cmdproc

# Make pymodel.*, golden.*, common.tb_utils, config importable from the TB.
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

# NOTE: --trace / --trace-structs are intentionally disabled here.
# cmdproc instantiates the full pipeline INCLUDING the 1024-element MMA FMA
# grid, which causes Verilator's per-module trace code to balloon the
# generated archive past macOS `ar`'s 32-bit offset limit (link fails with
# "malformed archive, member exceeds file size"). Tracing is still available
# on the smaller mma/Makefile and store/Makefile harnesses if needed.
# EXTRA_ARGS     += --trace --trace-structs
# Suppress harmless warnings from vendored fpnew / common_cells (see mma/Makefile).
EXTRA_ARGS     += -Wno-UNOPTFLAT -Wno-WIDTHTRUNC -Wno-ASCRANGE -Wno-WIDTHEXPAND -Wno-SPLITVAR

# Run with the project's venv ACTIVATED:
#   cd cmdproc/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)
# Make's `export PATH` does NOT propagate to $(shell), so we can't auto-fix
# this from inside the Makefile — venv must be on PATH before `make` is invoked.

# Pull constants out of config.py so the SV parameters match the pymodel.
MMA_M            := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_M; print(MMA_M)")
MMA_N            := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_N; print(MMA_N)")
MMA_K            := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_K; print(MMA_K)")
TMEM_SLOTS       := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import TMEM_SLOTS; print(TMEM_SLOTS)")
SMEM_BYTES       := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import SMEM_BYTES; print(SMEM_BYTES)")
GMEM_BYTES       := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import GMEM_BYTES; print(GMEM_BYTES)")
BEAT_BYTES       := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import BEAT_BYTES; print(BEAT_BYTES)")
NUM_BARRIERS     := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import NUM_BARRIERS; print(NUM_BARRIERS)")
INSTR_FIFO_DEPTH := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import INSTR_FIFO_DEPTH; print(INSTR_FIFO_DEPTH)")

COMPILE_ARGS += -GMMA_M=$(MMA_M) \
                -GMMA_N=$(MMA_N) \
                -GMMA_K=$(MMA_K) \
                -GTMEM_SLOTS=$(TMEM_SLOTS) \
                -GSMEM_BYTES=$(SMEM_BYTES) \
                -GGMEM_BYTES=$(GMEM_BYTES) \
                -GBEAT_BYTES=$(BEAT_BYTES) \
                -GNUM_BARRIERS=$(NUM_BARRIERS) \
                -GINSTR_FIFO_DEPTH=$(INSTR_FIFO_DEPTH)

include $(shell cocotb-config --makefiles)/Makefile.sim
