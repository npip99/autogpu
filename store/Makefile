# store — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against store.sv (compares to pymodel.store)
#   make clean      # remove build artifacts
#
# MMA_M, MMA_N, TMEM_SLOTS, BEAT_BYTES, GMEM_BYTES are baked in from config.py
# via Verilator -G flags so the SV parameters always match what the pymodel uses.

SIM            ?= verilator
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/store.sv $(PWD)/store_tb_top.sv $(PWD)/../tmem/tmem.sv $(PWD)/../gmem/gmem.sv \
                  $(PWD)/../common/fp8_encode.sv
TOPLEVEL        = store_tb_top

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_store.py
COCOTB_TEST_MODULES = tb.test_store

# Make pymodel.store, golden.fp8, common.tb_utils importable from the testbench.
# (sim is invoked from this directory; project root is one level up.)
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

# Run with the project's venv ACTIVATED:
#   cd store/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)
# Make's `export PATH` does NOT propagate to $(shell), so we can't auto-fix
# this from inside the Makefile — venv must be on PATH before `make` is invoked.

# Pull constants out of config.py so the SV parameters match the pymodel.
MMA_M      := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_M; print(MMA_M)")
MMA_N      := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_N; print(MMA_N)")
TMEM_SLOTS := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import TMEM_SLOTS; print(TMEM_SLOTS)")
BEAT_BYTES := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import BEAT_BYTES; print(BEAT_BYTES)")
GMEM_BYTES := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import GMEM_BYTES; print(GMEM_BYTES)")

COMPILE_ARGS += -GMMA_M=$(MMA_M) -GMMA_N=$(MMA_N) -GTMEM_SLOTS=$(TMEM_SLOTS) -GBEAT_BYTES=$(BEAT_BYTES) -GGMEM_BYTES=$(GMEM_BYTES)

include $(shell cocotb-config --makefiles)/Makefile.sim
