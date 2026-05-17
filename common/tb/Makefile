# common/tb -- cocotb harness for the vendored fpnew FMA wrapper and the
# fp8 encoder/decoder.
#
# This Makefile builds three independent simulations, one per common
# module. Default target builds all three:
#   make             -> run all three
#   make decode      -> only fp8_decode
#   make encode      -> only fp8_encode
#   make fma         -> only fp32_fma
#
# Run with the project's venv ACTIVATED:
#   cd common/tb/ && source ../../.venv/bin/activate && make

SIM            ?= verilator
TOPLEVEL_LANG  ?= verilog

# Make golden.fp8 + numpy importable from the testbench. (sim is invoked
# from this directory; project root is two levels up.)
export PYTHONPATH := $(PWD)/../..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs

# Verilator needs the include path for "common_cells/registers.svh"
# referenced inside the vendored fpnew sources.
COMPILE_ARGS += +incdir+$(PWD)/../fpnew

.PHONY: all decode encode fma clean

all: decode encode fma

decode:
	$(MAKE) -f Makefile.decode

encode:
	$(MAKE) -f Makefile.encode

fma:
	$(MAKE) -f Makefile.fma

clean:
	$(MAKE) -f Makefile.decode clean
	$(MAKE) -f Makefile.encode clean
	$(MAKE) -f Makefile.fma clean
