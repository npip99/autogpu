"""Render the SW corner of a GDS at fine resolution showing transistors + wires.

Difference vs render_layout.py:
  - Crop is RECTANGLE (X0 Y0 X1 Y1) not centered
  - Aggressively hides all fill/dummy/well/select/diff-dummy patterns that
    produce the "green diagonal slashes" obscuring the actual layout
  - Force solid fill (no hatching) on every visible layer so wires + transistor
    diffusion contacts are clearly readable

Env vars:
  LAYOUT_PATH   — input GDS
  OUT_PNG       — output PNG path
  CROP_BOX      — "x0,y0,x1,y1" in µm (e.g. "0,0,204,204")
  RES           — pixel resolution per side (default 16384)
"""
import pya  # type: ignore
import os
import glob

layout_path = (
    globals().get("layout_path")
    or os.environ.get("LAYOUT_PATH")
)
out_png = globals().get("out_png") or os.environ.get("OUT_PNG", "out.png")
pdk_root = os.environ.get("PDK_ROOT", os.path.expanduser("~/.volare"))
asap7 = os.path.join(pdk_root, "asap7")

assert layout_path, "set LAYOUT_PATH"

view = pya.LayoutView()
view.load_layout(layout_path)
view.max_hier_levels = 99
view.min_hier_levels = 0

# Vibrant per-layer colors (sky130-style)
# M1 is intentionally absent — it's the standard-cell power rail that
# runs horizontally through every row and floods the render with magenta.
# Set HIDE_M1=0 to include it.
VIBRANT = {
    (7, 0):   0xd62728,   # Gate (poly)        crimson
    (11, 0):  0x1f77b4,   # Active (diffusion) blue
    (16, 0):  0xffbf00,   # LIG (local int gate) gold
    (17, 0):  0x2ca02c,   # LISD (local int S/D) green
    (20, 0):  0x00ffff,   # M2  cyan
    (30, 0):  0xffff00,   # M3  yellow
    (40, 0):  0x00ff80,   # M4  spring green
    (50, 0):  0xff7f0e,   # M5  orange
    (60, 0):  0x4488ff,   # M6  azure
    (70, 0):  0xff1493,   # M7  hot pink
    (80, 0):  0xee82ee,   # M8  violet
    (90, 0):  0x9acd32,   # M9  yellow green
}
if os.environ.get("HIDE_M1", "1") == "0":
    VIBRANT[(19, 0)] = 0xff00ff   # M1 magenta (only if requested)

# Hide ALL noise layers that obscure structure (everything not in VIBRANT
# or via layers gets hidden). The previous render's "green 45-degree slashes"
# were Pselect (13/0) and Nselect (12/0) implants with diagonal fill style
# at low alpha — they cover the entire die.
KEEP = set(VIBRANT.keys())
# Also keep via cuts so connections between layers are visible
KEEP |= {
    (15, 0),  # V0  (active/LIG -> M1)
    (21, 0),  # V1  (M1 -> M2)
    (22, 0),  # V2  (M2 -> M3)
    (23, 0),  # V3  (M3 -> M4)
    (24, 0),  # V4  (M4 -> M5)
    (25, 0),  # V5  (M5 -> M6)
    (29, 0),  # V6  (M6 -> M7)
    (26, 0),  # via cuts misc
    (27, 0),
    (28, 0),
}

it = view.begin_layers()
while not it.at_end():
    lp = it.current()
    src = (lp.source_layer, lp.source_datatype)
    if src in VIBRANT:
        color = VIBRANT[src]
        lp.fill_color = color
        lp.frame_color = color
        lp.fill_brightness = 0
        lp.dither_pattern = 0  # solid (no hatching) — kills the "diagonal slashes"
        lp.visible = True
        view.replace_layer_node(it, lp)
    elif src in KEEP:
        # via: white-ish so it's clearly visible against any background
        lp.fill_color = 0xffffff
        lp.frame_color = 0xffffff
        lp.dither_pattern = 0
        lp.visible = True
        view.replace_layer_node(it, lp)
    else:
        # everything else hidden — wells, implants, dummy, fill, etc.
        lp.visible = False
        view.replace_layer_node(it, lp)
    it.next()
view.update_content()

view.set_config("grid-visible", "false")
view.set_config("background-color", "#000000")

crop = os.environ.get("CROP_BOX", "")
if crop:
    x0, y0, x1, y1 = (float(v) for v in crop.split(","))
    view.zoom_box(pya.DBox(x0, y0, x1, y1))
else:
    view.zoom_fit()

res = int(os.environ.get("RES", "16384"))
view.save_image(out_png, res, res)
print(f"Wrote {out_png}")
