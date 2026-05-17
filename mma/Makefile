# mma — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against mma.sv (compared to golden matmul)
#   make clean      # remove build artifacts
#
# MMA_M, MMA_N, MMA_K, TMEM_SLOTS, SMEM_BYTES, BEAT_BYTES, NUM_BARRIERS are
# baked in from config.py via Verilator -G flags so the SV parameters always
# match what the pymodel uses.

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
VERILOG_SOURCES = $(PWD)/mma.sv $(PWD)/mma_tb_top.sv \
                  $(PWD)/../smem/smem.sv \
                  $(PWD)/../tmem/tmem.sv \
                  $(PWD)/../barrier/barrier.sv \
                  $(PWD)/../common/fpnew/cf_math_pkg.sv \
                  $(PWD)/../common/fpnew/fpnew_pkg.sv \
                  $(PWD)/../common/fpnew/fpnew_classifier.sv \
                  $(PWD)/../common/fpnew/fpnew_rounding.sv \
                  $(PWD)/../common/fpnew/fpnew_fma.sv \
                  $(PWD)/../common/fpnew/lzc.sv \
                  $(PWD)/../common/fp32_fma.sv \
                  $(PWD)/../common/fp8_decode.sv
TOPLEVEL        = mma_tb_top

# Include path for "common_cells/registers.svh" inside fpnew sources.
COMPILE_ARGS += +incdir+$(PWD)/../common/fpnew

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_mma.py
COCOTB_TEST_MODULES = tb.test_mma

# Make pymodel.mma, golden.*, common.tb_utils importable from the testbench.
# (sim is invoked from this directory; project root is one level up.)
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs
# Suppress harmless warnings from vendored fpnew / common_cells:
#   - UNOPTFLAT   : lzc.sv's tree reduction
#   - WIDTHTRUNC  : fpnew_fma.sv intentional truncations
#   - ASCRANGE    : fpnew_pkg uses [0:N-1] vector ranges
#   - WIDTHEXPAND : fpnew_pkg has a 2-bit literal assigned to 32-bit
#   - SPLITVAR    : lzc.sv split_var pragmas conflict with cocotb's --public-flat-rw
EXTRA_ARGS     += -Wno-UNOPTFLAT -Wno-WIDTHTRUNC -Wno-ASCRANGE -Wno-WIDTHEXPAND -Wno-SPLITVAR

# Run with the project's venv ACTIVATED:
#   cd mma/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)
# Make's `export PATH` does NOT propagate to $(shell), so we can't auto-fix
# this from inside the Makefile — venv must be on PATH before `make` is invoked.

# Pull constants out of config.py so the SV parameters match the pymodel.
MMA_M        := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_M; print(MMA_M)")
MMA_N        := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_N; print(MMA_N)")
MMA_K        := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_K; print(MMA_K)")
TMEM_SLOTS   := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import TMEM_SLOTS; print(TMEM_SLOTS)")
SMEM_BYTES   := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import SMEM_BYTES; print(SMEM_BYTES)")
BEAT_BYTES   := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import BEAT_BYTES; print(BEAT_BYTES)")
NUM_BARRIERS := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import NUM_BARRIERS; print(NUM_BARRIERS)")

COMPILE_ARGS += -GMMA_M=$(MMA_M) -GMMA_N=$(MMA_N) -GMMA_K=$(MMA_K) \
                -GTMEM_SLOTS=$(TMEM_SLOTS) -GSMEM_BYTES=$(SMEM_BYTES) \
                -GBEAT_BYTES=$(BEAT_BYTES) -GNUM_BARRIERS=$(NUM_BARRIERS)

include $(shell cocotb-config --makefiles)/Makefile.sim
