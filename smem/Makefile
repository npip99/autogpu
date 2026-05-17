# smem — cocotb + Verilator harness.
#
# Usage:
#   make            # run cocotb tests against smem.sv (compares to pymodel.smem)
#   make clean      # remove build artifacts
#
# SMEM_BYTES, BEAT_BYTES, MMA_M, MMA_N are baked in from config.py via
# Verilator -G flags so the SV parameters always match what the pymodel uses.

SIM            ?= verilator

# Parallelize Verilator emit + build across all CPU cores. -j 0 auto-detects nproc.
EXTRA_ARGS     += -j 0
# Also parallelize the C++ compile step (cocotb invokes make on Vtop.mk without -j).
BUILD_ARGS     += -j $(shell sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
# Compile at -O0: simulation runtime is fine, and -O0 vs -Os is a big compile-time win
# for the 1024 fp32_fma instances. Switch to -O2 if you need fast sim runtime.
BUILD_ARGS     += OPT_FAST=-O0 OPT_SLOW=-O0 OPT_GLOBAL=-O0
# Route compiles through ccache (no-op if ccache not on PATH).
BUILD_ARGS     += OBJCACHE=$(shell command -v ccache 2>/dev/null)
TOPLEVEL_LANG  ?= verilog
VERILOG_SOURCES = $(PWD)/smem.sv \
                  $(PWD)/../mem/sram_1rw.sv

# Swap the FF-based sram_1rw for the sky130 SRAM macro's behavioral model.
# Usage: `make USE_MACRO=1` — drives the same cocotb tests against
# sky130_sram_1kbyte_1rw1r_32x256_8. If they pass, the macro is functionally
# equivalent to the FF impl for the patterns our tests cover.
ifeq ($(USE_MACRO),1)
    PDK_VERSION := $(shell ls $(HOME)/.volare/volare/sky130/versions/ | head -1)
    SRAM_MODEL := $(HOME)/.volare/volare/sky130/versions/$(PDK_VERSION)/sky130A/libs.ref/sky130_sram_macros/verilog/sky130_sram_1kbyte_1rw1r_32x256_8.v
    VERILOG_SOURCES += $(SRAM_MODEL)
    # USE_SKY130_MACRO: instantiate the macro instead of FF impl.
    # USE_SKY130_MACRO_SIM: also override VERBOSE=0 (only valid for the
    # behavioral .v model; synth flow does NOT set this).
    COMPILE_ARGS    += +define+USE_SKY130_MACRO +define+USE_SKY130_MACRO_SIM
    # The macro's behavioral model uses `#delay` for waveform readability;
    # ignore them (they don't affect cycle-level functional behavior).
    EXTRA_ARGS      += --no-timing -Wno-STMTDLY
endif

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
