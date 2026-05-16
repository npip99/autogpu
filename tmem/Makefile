# tmem — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against tmem.sv (compares to pymodel.tmem)
#   make clean      # remove build artifacts
#
# TMEM_SLOTS, MMA_M, MMA_N are baked in from config.py via Verilator -G flags
# so the SV parameters always match what the pymodel uses.

SIM            ?= verilator
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/tmem.sv
TOPLEVEL        = tmem

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_tmem.py
COCOTB_TEST_MODULES = tb.test_tmem

# Make pymodel.tmem and common.tb_utils importable from the testbench.
# (sim is invoked from this directory; project root is one level up.)
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

# Run with the project's venv ACTIVATED:
#   cd <module>/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)
# Make's `export PATH` does NOT propagate to $(shell), so we can't auto-fix
# this from inside the Makefile — venv must be on PATH before `make` is invoked.

# Pull constants out of config.py so the SV parameters match the pymodel.
TMEM_SLOTS := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import TMEM_SLOTS; print(TMEM_SLOTS)")
MMA_M      := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_M; print(MMA_M)")
MMA_N      := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_N; print(MMA_N)")

COMPILE_ARGS += -GTMEM_SLOTS=$(TMEM_SLOTS) -GMMA_M=$(MMA_M) -GMMA_N=$(MMA_N)

include $(shell cocotb-config --makefiles)/Makefile.sim
