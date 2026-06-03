# tile_buf_8row pin placement — INTENTIONALLY EMPTY (auto-place).
#
# tile_buf_8row is 115×115 µm with 1024-bit rd_data and 1024-bit wr_data
# interfaces. Any pin TCL that forces those onto a small subset of edges
# creates pin pitch < 0.5 µm (M5 minimum) and DRT stalls indefinitely.
#
# Attempted v3 (rd→E, wr→W) hit 38-min DRT spin before kill. ORFS's
# auto-placement spreads the 1024 bits across all 4 edges, giving ~0.45 µm
# pitch which is routable — and tile_buf_8row is consumed as a black box
# *inside* store, so chip_top doesn't care where these pins are.
#
# The scattered placement DOES make store's internal muxing larger
# (the ~13K resizer-buffer tax we measured), but that's a separate problem
# that can only be solved by re-floorplanning tile_buf_8row to a different
# aspect ratio (e.g. 60×500 with the wide buses on the long edges).
#
# Leaving empty so ORFS auto-places. Re-add this file with a real plan
# only if/when tile_buf_8row gets re-floorplanned.
