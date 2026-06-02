#!/bin/bash
# Render a GDS at "crop15-level resolution" (~3.7 nm/px) over the FULL die
# by tiling and stitching. Output is a single massive PNG.
#
# Usage:
#   tech/asap7/render_full_hires.sh \
#     build/orfs/results/asap7/<module>/base/6_final.gds \
#     <die_um>                                 \
#     [out_png]
#
# Designed to run inside screen — long-running, ~30 min to ~hours
# depending on die size.

set -e
GDS="${1:?usage: render_full_hires.sh <gds> <die_um> [out]}"
DIE_UM="${2:?usage: render_full_hires.sh <gds> <die_um> [out]}"
OUT="${3:-build/render/$(basename ${GDS%.gds})_full_hires.png}"

REPO=$(cd "$(dirname "$0")/../.." && pwd)
TILE_DIR="$REPO/build/render/tiles_$(basename ${GDS%.gds})"
mkdir -p "$TILE_DIR"

# Tile params: 4096 px tile over 15 µm crop = same density as crop15.png
RES=${RES:-4096}
CROP_UM=${CROP_UM:-15}

echo "[$(date)] Stage 1/2: rendering tiles to $TILE_DIR"
echo "  $RES px tiles, $CROP_UM µm crop each, covering $DIE_UM µm"

sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO:/work \
    -v $HOME/.volare:$HOME/.volare \
    -e RES=$RES \
    -e CROP_UM=$CROP_UM \
    -e DIE_UM=$DIE_UM \
    -e LAYOUT_PATH=/work/${GDS#$REPO/} \
    -e OUT_DIR=/work/${TILE_DIR#$REPO/} \
    -e PDK_ROOT=$HOME/.volare \
    ghcr.io/efabless/openlane2:2.3.10 \
    klayout -b -r /work/tech/asap7/render_wires_tiled.py"

echo "[$(date)] Stage 2/2: stitching tiles with imagemagick montage"
# N = ceil(DIE_UM / CROP_UM)
N=$(python3 -c "import math; print(math.ceil($DIE_UM/$CROP_UM))")
echo "  Grid: $N × $N tiles → final $((N*RES)) × $((N*RES)) px"

# montage in tile order — files are named tile_rRR_cCC.png. Sort
# row-major (r00 c00, r00 c01, ..., r00 c$N-1, r01 c00, ...).
sg docker -c "docker run --rm --user $(id -u):$(id -g) \
    -v $REPO:/work \
    dpokidov/imagemagick:7.1.1-15 \
    bash -c 'cd /work && montage \$(ls ${TILE_DIR#$REPO/}/tile_r*.png | sort) -tile ${N}x${N} -geometry +0+0 -background black ${OUT#$REPO/}'"

echo "[$(date)] Done"
ls -la "$OUT"
