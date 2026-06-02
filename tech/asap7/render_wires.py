"""Render an asap7 GDS/DEF showing ONLY signal-routing layers + macro outlines.

The default render_layout.py shows every visible layer including LIG/LISD
(local interconnect) and M1 — both of which are dense INSIDE every stdcell
and bury the actual parent-level signal routing under a solid orange/green
texture. This script hides those low layers and renders M2..M7 with high
contrast on a black background so the signal nets stand out.

Usage:
    docker run --rm -v $PWD:/work -v ~/.volare:~/.volare \
      ghcr.io/efabless/openlane2:2.3.10 \
      klayout -b -r /work/tech/asap7/render_wires.py \
        -rd layout_path=/work/build/orfs/.../6_final.gds \
        -rd out_png=/work/build/render/foo_wires.png
"""
import pya  # type: ignore
import os
import glob

layout_path = (
    globals().get("layout_path")
    or os.environ.get("LAYOUT_PATH")
    or "in.gds"
)
out_png = globals().get("out_png") or os.environ.get("OUT_PNG", "out_wires.png")
pdk_root = os.environ.get("PDK_ROOT", os.path.expanduser("~/.volare"))

asap7 = os.path.join(pdk_root, "asap7")
view = pya.LayoutView()

is_def = layout_path.endswith(".def")
if is_def:
    tech_lef = glob.glob(os.path.join(asap7, "libs.ref/asap7sc7p5t_rvt/techlef/*.lef"))
    cell_lefs = glob.glob(os.path.join(asap7, "libs.ref/asap7sc7p5t_rvt/lef/*.lef"))
    macro_lefs_root = os.environ.get("MACRO_LEFS_ROOT", "/work/build/orfs/results/asap7")
    macro_lefs = glob.glob(os.path.join(macro_lefs_root, "*/base/*.lef"))
    options = pya.LoadLayoutOptions()
    options.lefdef_config.read_lef_with_def = False
    options.lefdef_config.lef_files = tech_lef + cell_lefs + macro_lefs
    view.create_layout(True)
    view.load_layout(layout_path, options, 0)
else:
    view.load_layout(layout_path)

view.max_hier_levels = int(os.environ.get("MAX_HIER", "99"))
view.min_hier_levels = 0
view.add_missing_layers()

# Per-layer colors for the WIRE layers we want to see. Each is rendered
# bright with a thin frame, no fill, so overlapping wires from different
# layers stay visible as crossings.
WIRES = {
    (20, 0):  0x00ffff,   # M2 — cyan (horizontal)
    (30, 0):  0xffff00,   # M3 — yellow (vertical)
    (40, 0):  0x00ff80,   # M4 — spring green (horizontal)
    (50, 0):  0xff7f0e,   # M5 — orange (vertical)
    (60, 0):  0x4488ff,   # M6 — azure (horizontal)
    (70, 0):  0xff1493,   # M7 — hot pink (vertical)
    (80, 0):  0xee82ee,   # M8 — violet (horizontal)
    (90, 0):  0x9acd32,   # M9 — yellow green (vertical)
}

# Hide layers that bury the signal routing under uniform dense fill.
HIDE = {
    (1, 0),    # well drawing
    (2, 0),    # fin drawing
    (8, 0),    # dummy poly
    (12, 0),   # nselect
    (13, 0),   # pselect
    (7, 0),    # poly (gate) — dense at every stdcell
    (11, 0),   # active (diffusion)
    (16, 0),   # LIG (local interconnect gate) — orange, every stdcell
    (17, 0),   # LISD (local interconnect S/D) — green, every stdcell
    (19, 0),   # M1 — magenta, dense in every stdcell internal routing
}

it = view.begin_layers()
while not it.at_end():
    lp = it.current()
    src = (lp.source_layer, lp.source_datatype)
    if src in HIDE:
        lp.visible = False
        view.replace_layer_node(it, lp)
    elif src in WIRES:
        # Vibrant frame, lower-opacity fill so crossings are visible
        color = WIRES[src]
        lp.fill_color = color
        lp.frame_color = color
        lp.fill_brightness = 0
        lp.dither_pattern = 0   # solid
        lp.visible = True
        view.replace_layer_node(it, lp)
    it.next()
view.update_content()

view.set_config("grid-visible", "false")
view.set_config("background-color", "#000000")

bg_white = globals().get("bg_white") or os.environ.get("BG_WHITE", "0")
if bg_white == "1":
    view.set_config("background-color", "#ffffff")

view.zoom_fit()

zoom_mode = globals().get("zoom_mode") or os.environ.get("ZOOM_MODE", "fit")
if zoom_mode == "crop":
    crop_um = float(os.environ.get("CROP_UM", "100"))
    cx = float(os.environ.get("CROP_CX", "0"))
    cy = float(os.environ.get("CROP_CY", "0"))
    if cx == 0 and cy == 0:
        bbox = view.cellview(0).cell.dbbox()
        cx, cy = (bbox.left + bbox.right) / 2, (bbox.bottom + bbox.top) / 2
    half = crop_um / 2
    view.zoom_box(pya.DBox(cx - half, cy - half, cx + half, cy + half))

res = int(os.environ.get("RES", "8192"))
view.save_image(out_png, res, res)
print(f"Wrote {out_png} ({res}x{res})")
