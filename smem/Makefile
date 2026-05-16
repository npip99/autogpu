# smem — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against smem.sv (compares to pymodel.smem)
#   make clean      # remove build artifacts
#
# SMEM_BYTES, BEAT_BYTES, MMA_M, MMA_N are baked in from config.py via
# Verilator -G flags so the SV parameters always match what the pymodel uses.

SIM            ?= verilator
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/smem.sv
TOPLEVEL        = smem

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_smem.py
COCOTB_TEST_MODULES = tb.test_smem

# Make pymodel.smem and common.tb_utils importable from the testbench.
# (sim is invoked from this directory; project root is one level up.)
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

# Run with the project's venv ACTIVATED:
#   cd <module>/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)
# Make's `export PATH` does NOT propagate to $(shell), so we can't auto-fix
# this from inside the Makefile — venv must be on PATH before `make` is invoked.

# Pull constants out of config.py so the SV parameters match the pymodel.
SMEM_BYTES := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import SMEM_BYTES; print(SMEM_BYTES)")
BEAT_BYTES := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import BEAT_BYTES; print(BEAT_BYTES)")
MMA_M      := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_M; print(MMA_M)")
MMA_N      := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_N; print(MMA_N)")

COMPILE_ARGS += -GSMEM_BYTES=$(SMEM_BYTES) -GBEAT_BYTES=$(BEAT_BYTES) -GMMA_M=$(MMA_M) -GMMA_N=$(MMA_N)

include $(shell cocotb-config --makefiles)/Makefile.sim
