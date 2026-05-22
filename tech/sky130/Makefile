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

# Chip-top-only sv2v output for stub-mode chip_top synthesis. Contains
# just chip_top.sv (no submodule bodies). Combined with v-stub/*.v
# blackbox stubs, this lets yosys synthesize chip_top with submodules
# as opaque macros.
$(BUILD_DIR)/chip_top_only.v: $(REPO_ROOT)/top/chip_top.sv
	@mkdir -p $(BUILD_DIR)
	sv2v --exclude=Always $^ --top=chip_top -w $@

# Verilog blackbox stub for a submodule — same module header (params +
# ports + widths) as the real SV, body empty, `(* blackbox *)` attribute.
$(BUILD_DIR)/v-stub/%.v: $(REPO_ROOT)/tech/sky130/scripts/gen_stub_verilog.py
	@mkdir -p $(BUILD_DIR)/v-stub
	uv run python $< $* $(REPO_ROOT)

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

# -----------------------------------------------------------------------
# Hierarchical chip_top integration: stub-vs-real LEF infrastructure.
# -----------------------------------------------------------------------
# chip_top instantiates each submodule as an opaque MACRO referenced by
# its LEF (physical abstract). The MACRO can come from one of two places:
#
#   build/lef/<mod>.lef        — copy of the latest hardened submodule's LEF
#                                (real path; chip_top GDS at signoff)
#   build/lef-stub/<mod>.lef   — stub LEF generated from SV port list +
#                                config DIE_AREA + .pin_order.cfg edges
#                                (no internal geometry; for early
#                                 chip_top floorplan trials)
#
# Two completely separate output trees so a stub LEF never accidentally
# feeds a real chip_top harden.
#
# Submodules that chip_top actually instantiates (mac_array_small is a
# synth-proof, never used at chip_top).
CHIPTOP_MODULES := store cmdproc smem load barrier reset_seq compute_array

BUILD_LEF      := $(BUILD_DIR)/lef
BUILD_LEF_STUB := $(BUILD_DIR)/lef-stub
BUILD_GDS      := $(BUILD_DIR)/gds
BUILD_GDS_STUB := $(BUILD_DIR)/gds-stub
BUILD_LIB      := $(BUILD_DIR)/lib
BUILD_LIB_STUB := $(BUILD_DIR)/lib-stub

# Real LEF — copy the latest hardened submodule's LEF to a stable path.
# Fails loud if the submodule hasn't been hardened yet.
$(BUILD_LEF)/%.lef:
	@mkdir -p $(BUILD_LEF)
	@LATEST=$$(ls -td $(REPO_ROOT)/tech/sky130/submodules/$*/runs/RUN_* 2>/dev/null | head -1); \
	if [ -z "$$LATEST" ] || [ ! -f "$$LATEST/final/lef/$*.lef" ]; then \
		echo "ERROR: real LEF for '$*' not available — submodule not hardened."; \
		echo "  Either run: cd $(REPO_ROOT)/tech/sky130/submodules && ./dispatch.sh $*"; \
		echo "  Or build with stubs: make chip-top-stub"; \
		exit 1; \
	fi; \
	cp "$$LATEST/final/lef/$*.lef" "$@"

# Stub LEF/GDS/LIB — generated from SV + config + pin_order.cfg. Fast
# (<1s each). Useful for trial chip_top floorplan independent of leaf
# hardening status.
$(BUILD_LEF_STUB)/%.lef: $(REPO_ROOT)/tech/sky130/scripts/gen_stub_lef.py
	@mkdir -p $(BUILD_LEF_STUB)
	uv run python $< $* $(REPO_ROOT) > $@

$(BUILD_GDS_STUB)/%.gds: $(REPO_ROOT)/tech/sky130/scripts/gen_stub_gds.py
	@mkdir -p $(BUILD_GDS_STUB)
	uv run python $< $* $(REPO_ROOT)

$(BUILD_LIB_STUB)/%.lib: $(REPO_ROOT)/tech/sky130/scripts/gen_stub_lib.py
	@mkdir -p $(BUILD_LIB_STUB)
	uv run python $< $* $(REPO_ROOT)

# Real GDS/LIB — copy the latest hardened submodule's deliverables.
$(BUILD_GDS)/%.gds:
	@mkdir -p $(BUILD_GDS)
	@LATEST=$$(ls -td $(REPO_ROOT)/tech/sky130/submodules/$*/runs/RUN_* 2>/dev/null | head -1); \
	if [ -z "$$LATEST" ] || [ ! -f "$$LATEST/final/gds/$*.gds" ]; then \
		echo "ERROR: real GDS for '$*' not available — submodule not hardened."; \
		exit 1; \
	fi; \
	cp "$$LATEST/final/gds/$*.gds" "$@"

$(BUILD_LIB)/%.lib:
	@mkdir -p $(BUILD_LIB)
	@LATEST=$$(ls -td $(REPO_ROOT)/tech/sky130/submodules/$*/runs/RUN_* 2>/dev/null | head -1); \
	LIB=$$(find "$$LATEST/final/lib" -name "$*.lib" 2>/dev/null | head -1); \
	if [ -z "$$LIB" ]; then \
		echo "ERROR: real LIB for '$*' not available — submodule not hardened."; \
		exit 1; \
	fi; \
	cp "$$LIB" "$@"

# Aggregate targets — build all deliverables of either flavor.
.PHONY: lefs lefs-stub gdss gdss-stub libs libs-stub stubs reals
lefs:      $(addprefix $(BUILD_LEF)/,      $(addsuffix .lef, $(CHIPTOP_MODULES)))
lefs-stub: $(addprefix $(BUILD_LEF_STUB)/, $(addsuffix .lef, $(CHIPTOP_MODULES)))
gdss:      $(addprefix $(BUILD_GDS)/,      $(addsuffix .gds, $(CHIPTOP_MODULES)))
gdss-stub: $(addprefix $(BUILD_GDS_STUB)/, $(addsuffix .gds, $(CHIPTOP_MODULES)))
libs:      $(addprefix $(BUILD_LIB)/,      $(addsuffix .lib, $(CHIPTOP_MODULES)))
libs-stub: $(addprefix $(BUILD_LIB_STUB)/, $(addsuffix .lib, $(CHIPTOP_MODULES)))
BUILD_V_STUB := $(BUILD_DIR)/v-stub
verilogs-stub: $(addprefix $(BUILD_V_STUB)/, $(addsuffix .v, $(CHIPTOP_MODULES)))
stubs: lefs-stub gdss-stub libs-stub verilogs-stub $(BUILD_DIR)/chip_top_only.v
reals: lefs gdss libs

# chip_top OpenLane configs (auto-generated from chip_top_floorplan.yaml).
# Edit the floorplan YAML — never edit the generated config files directly.
.PHONY: config.yaml config.stub.yaml
config.yaml:      $(REPO_ROOT)/tech/sky130/config.yaml
config.stub.yaml: $(REPO_ROOT)/tech/sky130/config.stub.yaml

$(REPO_ROOT)/tech/sky130/config.yaml: \
		$(REPO_ROOT)/tech/sky130/scripts/gen_chip_top_config.py \
		$(REPO_ROOT)/tech/sky130/chip_top_floorplan.yaml
	uv run python $< real $(REPO_ROOT) > $@

$(REPO_ROOT)/tech/sky130/config.stub.yaml: \
		$(REPO_ROOT)/tech/sky130/scripts/gen_chip_top_config.py \
		$(REPO_ROOT)/tech/sky130/chip_top_floorplan.yaml
	uv run python $< stub $(REPO_ROOT) > $@

# chip_top hardening — single target, mode controlled by STUB.
#   make chip-top           ← real LEFs/GDSes/LIBs, requires all leaves hardened
#   make chip-top STUB=1    ← stub deliverables, fast, no leaf hardens required
#
# The two modes use SEPARATE output dirs internally (build/lef vs
# build/lef-stub, runs vs runs-stub, config.yaml vs config.stub.yaml)
# so stub artifacts can never accidentally feed a real chip_top harden.
STUB ?=

ifeq ($(STUB),1)
  CHIPTOP_DEPS := sv2v stubs $(REPO_ROOT)/tech/sky130/config.stub.yaml
  CHIPTOP_CFG  := config.stub.yaml
else
  CHIPTOP_DEPS := sv2v reals $(REPO_ROOT)/tech/sky130/config.yaml
  CHIPTOP_CFG  := config.yaml
endif

.PHONY: chip-top
chip-top: $(CHIPTOP_DEPS)
	cd $(REPO_ROOT)/tech/sky130 && sg docker -c '. $(REPO_ROOT)/.venv/bin/activate && python -m openlane --dockerized --pdk sky130A $(CHIPTOP_CFG)'

# dense_grid: NxN mac_tmem_cell macros, trivially chained. PoC of fast PnR.
$(BUILD_DIR)/dense_grid.v: $(REPO_ROOT)/mac_tmem_cell/mac_tmem_cell.sv $(REPO_ROOT)/dense_grid/dense_grid.sv
	@mkdir -p $(BUILD_DIR)
	sv2v --exclude=Always $^ --top=dense_grid -w $@
