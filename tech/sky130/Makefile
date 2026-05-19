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
    $(REPO_ROOT)/mac_array_small/mac_array_small.sv \
    $(REPO_ROOT)/tile_buf_8row/tile_buf_8row.sv \
    $(REPO_ROOT)/cmd_unit/cmd_unit.sv \
    $(REPO_ROOT)/skew_lane/skew_lane.sv \
    $(REPO_ROOT)/skew_lane/skew_lane_a.sv \
    $(REPO_ROOT)/skew_lane/skew_lane_b.sv \
    $(REPO_ROOT)/compute_array/compute_array.sv \
    $(REPO_ROOT)/load/load.sv \
    $(REPO_ROOT)/store/store.sv \
    $(REPO_ROOT)/reset_seq/reset_seq.sv \
    $(REPO_ROOT)/top/chip_top.sv

.PHONY: sv2v synth clean

sv2v: $(BUILD_DIR)/chip_top.v $(BUILD_DIR)/mac_array_small.v $(BUILD_DIR)/tile_buf_8row.v $(BUILD_DIR)/cmd_unit.v $(BUILD_DIR)/skew_lane_a.v $(BUILD_DIR)/skew_lane_b.v $(BUILD_DIR)/smem.v

$(BUILD_DIR)/chip_top.v: $(SV_SOURCES)
	@mkdir -p $(BUILD_DIR)
	# --exclude=Always keeps `always_comb` instead of materializing an
	# explicit sensitivity list. The explicit list would include unpacked
	# arrays like smem's bank_rdata, which Yosys 0.46 rejects.
	# -D USE_SKY130_MACRO makes sram_1rw instantiate the sky130 SRAM macro
	# instead of the FF behavioral fallback (validated in smem cocotb).
	sv2v -I $(REPO_ROOT)/common/fpnew -D USE_SKY130_MACRO --exclude=Always $(SV_SOURCES) --top=chip_top -w $@

# Standalone sv2v output for tile_buf_8row — the 8-row × 1024-bit banked
# storage submodule. Hardens once as a leaf macro; store.sv will later
# instantiate 4 of these as hardened children.
$(BUILD_DIR)/tile_buf_8row.v: $(REPO_ROOT)/tile_buf_8row/tile_buf_8row.sv
	@mkdir -p $(BUILD_DIR)
	sv2v --exclude=Always $^ --top=tile_buf_8row -w $@

# Standalone sv2v output for the cmd_unit submodule: one control block,
# the only non-array macro inside compute_array.
$(BUILD_DIR)/cmd_unit.v: $(REPO_ROOT)/cmd_unit/cmd_unit.sv
	@mkdir -p $(BUILD_DIR)
	sv2v --exclude=Always $^ --top=cmd_unit -w $@

$(BUILD_DIR)/skew_lane_a.v: $(REPO_ROOT)/skew_lane/skew_lane.sv $(REPO_ROOT)/skew_lane/skew_lane_a.sv
	@mkdir -p $(BUILD_DIR)
	sv2v --exclude=Always $^ --top=skew_lane_a -w $@

$(BUILD_DIR)/skew_lane_b.v: $(REPO_ROOT)/skew_lane/skew_lane.sv $(REPO_ROOT)/skew_lane/skew_lane_b.sv
	@mkdir -p $(BUILD_DIR)
	sv2v --exclude=Always $^ --top=skew_lane_b -w $@

# Standalone sv2v output for smem — only depends on sram_1rw.sv.
# Avoids parsing all of chip_top.v which makes yosys mem2reg + opt
# passes ~10× slower (had smem stuck at stage 02 for 30+ min).
$(BUILD_DIR)/smem.v: $(REPO_ROOT)/smem/smem.sv $(REPO_ROOT)/mem/sram_1rw.sv
	@mkdir -p $(BUILD_DIR)
	sv2v -D USE_SKY130_MACRO --exclude=Always $^ --top=smem -w $@

# Separate sv2v output for the small systolic synth-proof wrapper (it's
# not reachable from chip_top, so the main run drops it).
$(BUILD_DIR)/mac_array_small.v: \
		$(REPO_ROOT)/common/fpnew/cf_math_pkg.sv \
		$(REPO_ROOT)/common/fpnew/fpnew_pkg.sv \
		$(REPO_ROOT)/common/fpnew/fpnew_classifier.sv \
		$(REPO_ROOT)/common/fpnew/fpnew_rounding.sv \
		$(REPO_ROOT)/common/fpnew/fpnew_fma.sv \
		$(REPO_ROOT)/common/fpnew/lzc.sv \
		$(REPO_ROOT)/common/fp32_fma.sv \
		$(REPO_ROOT)/common/fp8_decode.sv \
		$(REPO_ROOT)/mac_tmem_cell/mac_tmem_cell.sv \
		$(REPO_ROOT)/mac_array_small/mac_array_small.sv
	@mkdir -p $(BUILD_DIR)
	sv2v -I $(REPO_ROOT)/common/fpnew --exclude=Always $^ --top=mac_array_small -w $@

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
