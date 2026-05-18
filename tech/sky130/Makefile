# Build helpers for sky130 synthesis.
#
# Usage:
#   make sv2v   # preprocess SystemVerilog -> plain Verilog
#   make synth  # run OpenLane (depends on sv2v output)

REPO_ROOT := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))../..)
BUILD_DIR := $(REPO_ROOT)/build/sv2v

SV_SOURCES := \
    $(REPO_ROOT)/common/fpnew/cf_math_pkg.sv \
    $(REPO_ROOT)/common/fpnew/fpnew_pkg.sv \
    $(REPO_ROOT)/common/fpnew/fpnew_classifier.sv \
    $(REPO_ROOT)/common/fpnew/fpnew_rounding.sv \
    $(REPO_ROOT)/common/fpnew/fpnew_fma.sv \
    $(REPO_ROOT)/common/fpnew/lzc.sv \
    $(REPO_ROOT)/common/fp32_fma.sv \
    $(REPO_ROOT)/common/fp8_decode.sv \
    $(REPO_ROOT)/common/fp8_encode.sv \
    $(REPO_ROOT)/mem/sram_1rw.sv \
    $(REPO_ROOT)/cmdproc/cmdproc.sv \
    $(REPO_ROOT)/smem/smem.sv \
    $(REPO_ROOT)/barrier/barrier.sv \
    $(REPO_ROOT)/mac_tmem_cell/mac_tmem_cell.sv \
    $(REPO_ROOT)/compute_array/compute_array.sv \
    $(REPO_ROOT)/load/load.sv \
    $(REPO_ROOT)/store/store.sv \
    $(REPO_ROOT)/reset_seq/reset_seq.sv \
    $(REPO_ROOT)/top/chip_top.sv

.PHONY: sv2v synth clean

sv2v: $(BUILD_DIR)/chip_top.v

$(BUILD_DIR)/chip_top.v: $(SV_SOURCES)
	@mkdir -p $(BUILD_DIR)
	# --exclude=Always keeps `always_comb` instead of materializing an
	# explicit sensitivity list. The explicit list would include unpacked
	# arrays like smem's bank_rdata, which Yosys 0.46 rejects.
	# -D USE_SKY130_MACRO makes sram_1rw instantiate the sky130 SRAM macro
	# instead of the FF behavioral fallback (validated in smem cocotb).
	sv2v -I $(REPO_ROOT)/common/fpnew -D USE_SKY130_MACRO --exclude=Always $(SV_SOURCES) --top=chip_top -w $@

synth: sv2v
	sg docker -c './run.sh < /dev/null'

# Resume from the most recent run's last completed step. Skips all
# previously-finished stages and re-uses their state_out.json. Use this when
# a long run died mid-flow and you want to retry without redoing parsing or
# synthesis. Will fall back to a full fresh run if no completed step exists.
synth-resume: sv2v
	sg docker -c './run.sh resume < /dev/null'

clean:
	rm -rf $(BUILD_DIR)
