# barrier — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against barrier.sv (compares to pymodel.barrier)
#   make clean      # remove build artifacts
#
# NUM_BARRIERS is baked in from config.py via Verilator -G flag so the SV
# parameter always matches what the pymodel uses.

SIM            ?= verilator
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/barrier.sv
TOPLEVEL        = barrier

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_barrier.py
COCOTB_TEST_MODULES = tb.test_barrier

# Make pymodel.barrier and common.tb_utils importable from the testbench.
# (sim is invoked from this directory; project root is one level up.)
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

# Run with the project's venv ACTIVATED:
#   cd <module>/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)
# Make's `export PATH` does NOT propagate to $(shell), so we can't auto-fix
# this from inside the Makefile — venv must be on PATH before `make` is invoked.

# Pull constants out of config.py so the SV parameters match the pymodel.
NUM_BARRIERS := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import NUM_BARRIERS; print(NUM_BARRIERS)")

COMPILE_ARGS += -GNUM_BARRIERS=$(NUM_BARRIERS)

include $(shell cocotb-config --makefiles)/Makefile.sim
