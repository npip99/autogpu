"""Render an ASAP7 layout (.gds or .def) to PNG via KLayout.

Adapted from tech/sky130/render_layout.py for the vendored ASAP7 PDK.

    docker run --rm -v $PWD:/work -v $HOME/.volare:/root/.volare \
      ghcr.io/efabless/openlane2:2.3.10 \
      klayout -b -r /work/render_layout.py \
        -rd layout_path=/work/foo.gds \
        -rd out_png=/work/foo.png
"""
import pya  # type: ignore  # provided by KLayout
import os
import glob

layout_path = (
    globals().get("layout_path")
    or globals().get("gds_path")
    or globals().get("def_path")
    or os.environ.get("LAYOUT_PATH")
    or os.environ.get("GDS_PATH")
    or os.environ.get("DEF_PATH")
)
out_png = globals().get("out_png") or os.environ.get("OUT_PNG", "out.png")
pdk_root = os.environ.get(
    "PDK_ROOT",
    os.path.expanduser("~/.volare"),
)

assert layout_path, "set layout_path / gds_path / def_path (-rd ...) or env"

asap7 = os.path.join(pdk_root, "asap7")

view = pya.LayoutView()

is_def = layout_path.endswith(".def")
if is_def:
    tech_lef = glob.glob(
        os.path.join(asap7, "libs.ref/asap7sc7p5t_rvt/techlef/*.lef")
    )
    cell_lefs = glob.glob(
        os.path.join(asap7, "libs.ref/asap7sc7p5t_rvt/lef/*.lef")
    )
    # Also pick up any hardened-macro LEFs from our ORFS results tree so a
    # hierarchical DEF (e.g. compute_array placing skew_lane / mac_tmem_cell)
    # finds its child macros without us having to enumerate them.
    macro_lefs_root = os.environ.get(
        "MACRO_LEFS_ROOT", "/work/build/orfs/results/asap7"
    )
    macro_lefs = glob.glob(os.path.join(macro_lefs_root, "*/base/*.lef"))
    options = pya.LoadLayoutOptions()
    options.lefdef_config.read_lef_with_def = False
    options.lefdef_config.lef_files = tech_lef + cell_lefs + macro_lefs
    view.create_layout(True)
    view.load_layout(layout_path, options, 0)
else:
    view.load_layout(layout_path)

view.max_hier_levels = 99
view.min_hier_levels = 0

# Apply ASAP7 colors then override the busiest layers with saturated
# values — the stock asap7.lyp uses very pale cream/beige (#fef5e7 etc.)
# which washes out at chip-scale zoom. Sky130's renders look "colorful
# chip-photo" because sky130A.lyp uses jewel tones; we match by
# repainting the key layers (poly, active, LIs, M1..M7).
use_lyp = os.environ.get("USE_LYP", "0") == "1"
if use_lyp:
    lyp_path = os.path.join(asap7, "libs.tech/klayout/asap7.lyp")
    if os.path.exists(lyp_path):
        view.load_layer_props(lyp_path)
view.add_missing_layers()

VIBRANT = {
    (7, 0):   0xd62728,   # Gate (poly)        crimson
    (11, 0):  0x1f77b4,   # Active (diffusion) blue
    (16, 0):  0xffbf00,   # LIG (local int gate) gold
    (17, 0):  0x2ca02c,   # LISD (local int S/D) green
    (19, 0):  0xff00ff,   # M1  magenta
    (20, 0):  0x00ffff,   # M2  cyan
    (30, 0):  0xffff00,   # M3  yellow
    (40, 0):  0x00ff80,   # M4  spring green
    (50, 0):  0xff7f0e,   # M5  orange
    (60, 0):  0x4488ff,   # M6  azure
    (70, 0):  0xff1493,   # M7  hot pink
    (80, 0):  0xee82ee,   # M8  violet
    (90, 0):  0x9acd32,   # M9  yellow green
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
        view.replace_layer_node(it, lp)
    it.next()
view.update_content()

# Hide only the layers that cover the whole die in uniform blocks (wells,
# implants, dummy fill). Keep poly / active / local-interconnect / metals
# visible so the render shows cell internals in full color — matches the
# sky130 render style at tech/sky130/render_layout.py.
HIDE = {
    (1, 0),    # well drawing — monolithic, hides everything
    (2, 0),    # fin drawing — uniform repetition
    (8, 0),    # Dummy poly drawing — fill noise
    (12, 0),   # Nselect (NMOS implant) — big rectangles
    (13, 0),   # Pselect (PMOS implant) — big rectangles
}
it = view.begin_layers()
while not it.at_end():
    lp = it.current()
    src = (lp.source_layer, lp.source_datatype)
    if lp.visible and src in HIDE:
        lp.visible = False
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
    crop_um = float(os.environ.get("CROP_UM", "5"))
    bbox = view.cellview(0).cell.dbbox()
    cx, cy = (bbox.left + bbox.right) / 2, (bbox.bottom + bbox.top) / 2
    half = crop_um / 2
    view.zoom_box(pya.DBox(cx - half, cy - half, cx + half, cy + half))

res = int(os.environ.get("RES", "4096"))
view.save_image(out_png, res, res)
print(f"Wrote {out_png}")
