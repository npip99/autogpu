# cmdproc — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against cmdproc + full Phase 4 pipeline
#   make clean      # remove build artifacts
#
# Parameters are pulled from config.py via Verilator -G flags so the SV
# parameters always match what the pymodel uses.

SIM            ?= verilator
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/cmdproc.sv \
                  $(PWD)/cmdproc_tb_top.sv \
                  $(PWD)/../gmem/gmem.sv \
                  $(PWD)/../smem/smem.sv \
                  $(PWD)/../tmem/tmem.sv \
                  $(PWD)/../barrier/barrier.sv \
                  $(PWD)/../mma/mma.sv \
                  $(PWD)/../load/load.sv \
                  $(PWD)/../store/store.sv
TOPLEVEL        = cmdproc_tb_top

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_cmdproc.py
COCOTB_TEST_MODULES = tb.test_cmdproc

# Make pymodel.*, golden.*, common.tb_utils, config importable from the TB.
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

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
