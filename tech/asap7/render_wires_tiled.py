"""Render an asap7 GDS as a grid of crop tiles (each at high resolution),
ready to stitch into one massive image. Same per-tile resolution as
ca_tiny_wires_crop15.png (4096 px over a 15 µm crop = ~3.7 nm/px), but
covers the entire die instead of one window.

Env vars:
  LAYOUT_PATH   — input GDS/DEF
  OUT_DIR       — directory for tile_TT_TT.png outputs
  RES           — pixels per tile side (default 4096)
  CROP_UM       — µm per tile side (default 15)
  DIE_UM        — total die size to cover (default 350)
  PDK_ROOT      — asap7 PDK root
"""
import pya  # type: ignore
import os
import glob
import math

layout_path = os.environ.get("LAYOUT_PATH")
out_dir     = os.environ.get("OUT_DIR", "build/render/tiles")
RES         = int(os.environ.get("RES", "4096"))
CROP_UM     = float(os.environ.get("CROP_UM", "15"))
DIE_UM      = float(os.environ.get("DIE_UM", "350"))
pdk_root    = os.environ.get("PDK_ROOT", os.path.expanduser("~/.volare"))

assert layout_path, "set LAYOUT_PATH"

os.makedirs(out_dir, exist_ok=True)

view = pya.LayoutView()
view.load_layout(layout_path)
view.max_hier_levels = 99
view.min_hier_levels = 0
view.add_missing_layers()

# Same hide list as render_wires.py — drop the layers that bury wires
HIDE = {(1, 0), (2, 0), (8, 0), (12, 0), (13, 0), (7, 0), (11, 0),
        (16, 0), (17, 0), (19, 0)}
WIRES = {
    (20, 0): 0x00ffff, (30, 0): 0xffff00, (40, 0): 0x00ff80,
    (50, 0): 0xff7f0e, (60, 0): 0x4488ff, (70, 0): 0xff1493,
    (80, 0): 0xee82ee, (90, 0): 0x9acd32,
}
it = view.begin_layers()
while not it.at_end():
    lp = it.current()
    src = (lp.source_layer, lp.source_datatype)
    if src in HIDE:
        lp.visible = False
        view.replace_layer_node(it, lp)
    elif src in WIRES:
        color = WIRES[src]
        lp.fill_color = color
        lp.frame_color = color
        lp.fill_brightness = 0
        lp.dither_pattern = 0
        view.replace_layer_node(it, lp)
    it.next()
view.update_content()
view.set_config("grid-visible", "false")
view.set_config("background-color", "#000000")

N = math.ceil(DIE_UM / CROP_UM)
half = CROP_UM / 2.0

print(f"Rendering {N}×{N} tiles, each {RES}×{RES} px over {CROP_UM} µm "
      f"({RES/CROP_UM:.1f} px/µm = {1000/(RES/CROP_UM):.2f} nm/px)")

# Tile indexing: (tx, ty) → crop center at (tx+0.5)*CROP_UM, (ty+0.5)*CROP_UM
# ty grows northward in chip coords, but image rows grow downward, so we
# emit with ty inverted in the filename for easy montage.
for ty in range(N):
    for tx in range(N):
        cx = (tx + 0.5) * CROP_UM
        cy = (ty + 0.5) * CROP_UM
        view.zoom_box(pya.DBox(cx - half, cy - half, cx + half, cy + half))
        # ty inverted: image-row 0 = top of chip
        row = N - 1 - ty
        out_png = f"{out_dir}/tile_r{row:02d}_c{tx:02d}.png"
        view.save_image(out_png, RES, RES)
        # Progress every 16 tiles
        if (ty * N + tx + 1) % 16 == 0 or (ty == N - 1 and tx == N - 1):
            print(f"  {ty*N + tx + 1}/{N*N} tiles written")

print(f"Done — {N*N} tiles in {out_dir}")
