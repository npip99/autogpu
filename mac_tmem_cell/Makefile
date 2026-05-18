# mac_tmem_cell — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against mac_tmem_cell.sv
#   make clean      # remove build artifacts
#
# N_SLOTS is baked in from config.py (TMEM_SLOTS) via Verilator -G flags so
# the SV parameter always matches what the pymodel uses.

SIM            ?= verilator

# Parallelize Verilator emit + build across all CPU cores. -j 0 auto-detects nproc.
EXTRA_ARGS     += -j 0
# Also parallelize the C++ compile step (cocotb invokes make on Vtop.mk without -j).
BUILD_ARGS     += -j $(shell sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
# Compile at -O0: simulation runtime is fine, and -O0 vs -Os is a big compile-time win
# for the fp32_fma. Switch to -O2 if you need fast sim runtime.
BUILD_ARGS     += OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0
# Route compiles through ccache (no-op if ccache not on PATH).
BUILD_ARGS     += OBJCACHE=$(shell command -v ccache 2>/dev/null)
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/mac_tmem_cell.sv \
                  $(PWD)/../common/fpnew/cf_math_pkg.sv \
                  $(PWD)/../common/fpnew/fpnew_pkg.sv \
                  $(PWD)/../common/fpnew/fpnew_classifier.sv \
                  $(PWD)/../common/fpnew/fpnew_rounding.sv \
                  $(PWD)/../common/fpnew/fpnew_fma.sv \
                  $(PWD)/../common/fpnew/lzc.sv \
                  $(PWD)/../common/fp32_fma.sv \
                  $(PWD)/../common/fp8_decode.sv
TOPLEVEL        = mac_tmem_cell

# Include path for "common_cells/registers.svh" inside fpnew sources.
COMPILE_ARGS += +incdir+$(PWD)/../common/fpnew

# cocotb 2.0: use COCOTB_TEST_MODULES; the tb lives in ./tb/test_mac_tmem_cell.py
COCOTB_TEST_MODULES = tb.test_mac_tmem_cell

# Make pymodel.mac_tmem_cell, golden.*, common.tb_utils importable from the testbench.
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
#   cd mac_tmem_cell/ && source ../.venv/bin/activate && make
# (See DEVELOPMENT.md §Testing.)

# Pull constants out of config.py so the SV parameters match the pymodel.
N_SLOTS := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import TMEM_SLOTS; print(TMEM_SLOTS)")

COMPILE_ARGS += -GN_SLOTS=$(N_SLOTS)

include $(shell cocotb-config --makefiles)/Makefile.sim
