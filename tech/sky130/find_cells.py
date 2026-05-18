"""Find cell instance locations in a GDS. Prints a histogram of cell
density on a 50µm grid so we know WHERE to crop for a render."""
import pya  # type: ignore
import sys
import os

gds = globals().get("GDS_PATH") or os.environ.get("GDS_PATH")
if not gds and len(sys.argv) > 1:
    gds = sys.argv[1]
assert gds, "set GDS_PATH"
ly = pya.Layout()
ly.read(gds)
top = ly.top_cell()
print(f"Top: {top.name}   bbox(um): {top.dbbox()}")

# Iterate all instances at the top level.
xs, ys = [], []
for inst in top.each_inst():
    cell = ly.cell(inst.cell_index)
    if cell.is_proxy() or cell is top:
        continue
    bb = inst.dbbox()
    cx = (bb.left + bb.right) / 2
    cy = (bb.bottom + bb.top) / 2
    xs.append(cx)
    ys.append(cy)

print(f"Total cell instances: {len(xs)}")
if not xs:
    sys.exit(0)

print(f"Cell-region bbox (um): "
      f"({min(xs):.1f}, {min(ys):.1f}) - ({max(xs):.1f}, {max(ys):.1f})")

# 50um bins. Print a tiny ASCII heatmap.
bin_um = 50.0
bb = top.dbbox()
nx = max(1, int((bb.right - bb.left) / bin_um) + 1)
ny = max(1, int((bb.top - bb.bottom) / bin_um) + 1)
grid = [[0] * nx for _ in range(ny)]
for cx, cy in zip(xs, ys):
    ix = min(nx - 1, max(0, int((cx - bb.left) / bin_um)))
    iy = min(ny - 1, max(0, int((cy - bb.bottom) / bin_um)))
    grid[iy][ix] += 1

cmax = max(max(row) for row in grid)
shades = " .:-=+*#%@"
print(f"\nCell density grid ({nx}x{ny}, {bin_um}um bins, max={cmax}):")
for row in reversed(grid):
    print("  " + "".join(shades[min(len(shades)-1, int(v * (len(shades)-1) / cmax))]
                          for v in row))
