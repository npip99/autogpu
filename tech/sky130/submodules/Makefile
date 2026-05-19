# Per-submodule synthesis driver.
#
# Each target invokes ./run.sh <module>. Use the parent
# tech/sky130/Makefile's `sv2v` target first to generate the converted
# Verilog that ALL these runs share.

REPO_ROOT := $(realpath $(dir $(lastword $(MAKEFILE_LIST)))../../..)

MODULES := store barrier reset_seq mac_tmem_cell mac_array_small tile_buf_8row cmd_unit skew_lane compute_array load cmdproc smem
# (fp32_fma and fpnew_fma_EA93F are pure combinational — no clock port —
# and need a different config to synthesize. Add them later if needed.)

.PHONY: sv2v $(MODULES) all clean

sv2v:
	$(MAKE) -C $(REPO_ROOT)/tech/sky130 sv2v

# Each module is its own target. Depend on sv2v so the converted .v exists.
$(MODULES): sv2v
	sg docker -c './run.sh $@ < /dev/null'

# Run every module in sequence. Stops on first failure.
all: $(MODULES)

clean:
	rm -rf $(addsuffix /runs,$(MODULES))
