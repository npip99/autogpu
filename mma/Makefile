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
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/mma.sv $(PWD)/mma_tb_top.sv \
                  $(PWD)/../smem/smem.sv \
                  $(PWD)/../tmem/tmem.sv \
                  $(PWD)/../barrier/barrier.sv
TOPLEVEL        = mma_tb_top

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_mma.py
COCOTB_TEST_MODULES = tb.test_mma

# Make pymodel.mma, golden.*, common.tb_utils importable from the testbench.
# (sim is invoked from this directory; project root is one level up.)
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

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
