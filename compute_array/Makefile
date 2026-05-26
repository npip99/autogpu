# compute_array — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against compute_array.sv
#   make clean      # remove build artifacts
#
# Parameters baked in from config.py via Verilator -G flags.

SIM            ?= verilator

# Parallelize Verilator emit + build across all CPU cores.
EXTRA_ARGS     += -j 0
BUILD_ARGS     += -j $(shell sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
# -O0 is a big compile-time win for the 1024-FMA grid. Switch to -O2 if
# sim runtime becomes a bottleneck.
BUILD_ARGS     += OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0
# Route compiles through ccache (no-op if ccache not on PATH).
BUILD_ARGS     += OBJCACHE=$(shell command -v ccache 2>/dev/null)

TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/../common/fpnew/cf_math_pkg.sv \
                  $(PWD)/../common/fpnew/fpnew_pkg.sv \
                  $(PWD)/../common/fpnew/fpnew_classifier.sv \
                  $(PWD)/../common/fpnew/fpnew_rounding.sv \
                  $(PWD)/../common/fpnew/fpnew_fma.sv \
                  $(PWD)/../common/fpnew/lzc.sv \
                  $(PWD)/../common/fp32_fma.sv \
                  $(PWD)/../common/fp8_decode.sv \
                  $(PWD)/../mac_tmem_cell/mac_tmem_cell.sv \
                  $(PWD)/../cmd_unit/cmd_unit.sv \
                  $(PWD)/../skew_lane/skew_lane.sv \
                  $(PWD)/../skew_lane/skew_lane_a.sv \
                  $(PWD)/../skew_lane/skew_lane_b.sv \
                  $(PWD)/compute_array.sv
TOPLEVEL        = compute_array

# Include path for "common_cells/registers.svh" inside fpnew sources.
COMPILE_ARGS += +incdir+$(PWD)/../common/fpnew

# cocotb 2.0: COCOTB_TEST_MODULES; tb lives in ./tb/test_compute_array.py
COCOTB_TEST_MODULES = tb.test_compute_array

# Make pymodel.*, golden.*, common.tb_utils importable from the testbench.
export PYTHONPATH := $(PWD)/..:$(PYTHONPATH)

EXTRA_ARGS     += --trace --trace-structs
# Suppress vendored-source warnings.
EXTRA_ARGS     += -Wno-UNOPTFLAT -Wno-WIDTHTRUNC -Wno-ASCRANGE -Wno-WIDTHEXPAND -Wno-SPLITVAR

# Pull constants out of config.py so SV params match the pymodel.
MMA_M := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_M; print(MMA_M)")
MMA_N := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_N; print(MMA_N)")
MMA_K := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import MMA_K; print(MMA_K)")
N_SLOTS := $(shell python3 -c "import sys; sys.path.insert(0, '$(PWD)/..'); from config import TMEM_SLOTS; print(TMEM_SLOTS)")

COMPILE_ARGS += -GMMA_M=$(MMA_M) -GMMA_N=$(MMA_N) -GMMA_K=$(MMA_K) -GN_SLOTS=$(N_SLOTS)

# Broadcast pipeline depth. Default 0 (no pipe, matches sky130 build).
# Set BCAST_PIPE=N to verify parent-level pipeline functional behavior
# under the same N used by tech/asap7 hardening configs. Pymodel reads
# the env var to model the matching cycle latency.
BCAST_PIPE ?= 0
COMPILE_ARGS += -GBCAST_PIPE=$(BCAST_PIPE)
export BCAST_PIPE

include $(shell cocotb-config --makefiles)/Makefile.sim
