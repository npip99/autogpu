# Adder experiment — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against Verilog (compares to pymodel)
#   make pytest     # run pytest against the pymodel alone
#   make clean      # remove build artifacts

SIM            ?= verilator
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/adder.sv
TOPLEVEL        = adder
MODULE          = tb

EXTRA_ARGS     += --trace --trace-structs

include $(shell cocotb-config --makefiles)/Makefile.sim

.PHONY: pytest
pytest:
	pytest -v test_pymodel.py
