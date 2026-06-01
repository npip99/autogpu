# Pinned ORFS container image — single source of truth.
#
# Was `openroad/orfs:latest` (a floating tag → non-reproducible builds: the
# PDK, DRC deck, router, and tool versions could all silently change under us).
# Pinned to the digest of the image the asap7 flow was validated on:
#   OpenROAD 26Q2-1164-g08f67ee5ec, image built 2026-05-18.
#
# Sourced by run.sh and every sign-off script (drc/lvs/ir_drop/antenna_check/
# density_check/render_odb/gen_lef); each does `docker run ... "$ORFS_IMAGE"`.
#
# To re-pin (deliberately adopt a newer ORFS — then re-validate the flow):
#   docker pull openroad/orfs:latest
#   docker inspect openroad/orfs:latest --format '{{index .RepoDigests 0}}'
#   # paste the openroad/orfs@sha256:... value below.
ORFS_IMAGE="openroad/orfs@sha256:cf4186a5e6a52eddcad1e53e55e1571dbd6711a8e5e687cdb2a8bdc62bc20f1d"
