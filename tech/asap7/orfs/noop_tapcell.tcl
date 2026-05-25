# No-op tapcell script. Overrides asap7's tapcell.tcl which inserts tap +
# endcap cells in every stdcell row. With our 1024-macro grid and halo'd
# rows, those tap cells leave fragmented M1 followpins that pdngen can't
# repair (PDN-0179). Skipping tapcell altogether avoids the problem at
# the cost of well-tie integrity (acceptable for proof-of-concept; real
# silicon would need a different floorplan strategy).
puts "noop_tapcell.tcl: skipping tap/endcap insertion"
