# ORFS config for the mac_tmem_cell submodule on asap7.
#
# Driven by tech/asap7/orfs/run.sh, which mounts the repo into the
# openroad/orfs:latest container at /work and calls `make` with
# DESIGN_CONFIG pointing at this file.
#
# All paths assume DESIGN_CONFIG is set to the in-container path
# /work/tech/asap7/orfs/mac_tmem_cell.config.mk, i.e. ${REPO_ROOT}
# inside the container is /work.

export PLATFORM     = asap7
export DESIGN_NAME  = mac_tmem_cell

# Use the already-sv2v'd chip_top.v from the sky130 Makefile output.
# Yosys treats it as plain Verilog and elaborates DESIGN_NAME as the top.
export VERILOG_FILES = /work/build/sv2v/chip_top.v

export SDC_FILE = /work/tech/asap7/orfs/mac_tmem_cell.sdc

# Floorplan / placement knobs. asap7 stdcells are ~10x smaller than
# sky130, so we run modest utilization with a bit of margin to let
# OpenROAD's pin-access engine work without exhausting routing tracks.
export CORE_UTILIZATION  = 65
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.75

# Skip the "last gasp" hold/setup iterations on this smoke run — they
# cost ~10min and aren't useful when CTS is off.
export SKIP_LAST_GASP ?= 1

