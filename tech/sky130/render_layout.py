"""Render a DEF (with sky130 LEFs) to PNG via KLayout.

Run inside the OpenLane Docker image where klayout is on PATH and the
sky130A PDK is mounted. Example:

    sudo docker run --rm \\
      -v $PWD:/work -v $HOME/.volare:$HOME/.volare \\
      ghcr.io/efabless/openlane2:2.3.10 \\
      klayout -b -r /work/render_layout.py -rd def_path=/work/chip_top.def \\
                                            -rd out_png=/work/chip_top.png
"""
import pya  # type: ignore  # provided by KLayout
import os
import glob

def_path = globals().get("def_path") or os.environ.get("DEF_PATH")
out_png = globals().get("out_png") or os.environ.get("OUT_PNG", "out.png")
pdk_root = os.environ.get(
    "PDK_ROOT",
    os.path.expanduser("~/.volare/volare/sky130/versions"),
)

assert def_path, "set def_path (klayout -rd def_path=...) or DEF_PATH env"

# Pick whichever sky130A version is installed.
pdk_version = sorted(os.listdir(pdk_root))[0]
sky130A = os.path.join(pdk_root, pdk_version, "sky130A")

# All LEFs from sky130_fd_sc_hd (standard cells) + tech LEF.
tech_lef = glob.glob(os.path.join(sky130A, "libs.ref/sky130_fd_sc_hd/techlef/*.tlef"))
cell_lefs = glob.glob(os.path.join(sky130A, "libs.ref/sky130_fd_sc_hd/lef/*.lef"))

layout = pya.Layout()
options = pya.LoadLayoutOptions()
options.lefdef_config.read_lef_with_def = False
options.lefdef_config.lef_files = tech_lef + cell_lefs
layout.read(def_path, options)

# Headless render via LayoutView.
view = pya.LayoutView()
cell_view_idx = view.create_layout(True)
view.load_layout(def_path, options, cell_view_idx)
# Show subcells, all hierarchy levels. Default visibility is sometimes
# limited to a few levels which hides the actual standard-cell geometry.
view.max_hier_levels = 99
view.min_hier_levels = 0

# Apply sky130 colors so layers are actually visible.
lyp_path = os.path.join(sky130A, "libs.tech/klayout/tech/sky130A.lyp")
if os.path.exists(lyp_path):
    view.load_layer_props(lyp_path)

# Force ALL layers visible (some .lyp configs hide layers by default
# or based on zoom). Walk the layer list and set visible=True.
iter = view.begin_layers()
while not iter.at_end():
    lp = iter.current()
    lp.visible = True
    iter.next()
view.update_content()

view.zoom_fit()

# White background instead of KLayout's default black, for readability when
# zoomed out. Layer colors from the sky130 .lyp file still apply.
bg_white = globals().get("bg_white") or os.environ.get("BG_WHITE", "0")
if bg_white == "1":
    view.set_config("background-color", "#ffffff")

# `zoom`: "crop" picks a CROP_UM × CROP_UM box centered on the design's
# bbox (where cells actually are); "fit" uses the whole die.
zoom_mode = globals().get("zoom_mode") or os.environ.get("ZOOM_MODE", "fit")
if zoom_mode == "crop":
    crop_um = float(os.environ.get("CROP_UM", "100"))
    bbox = layout.top_cell().dbbox()
    cx, cy = (bbox.left + bbox.right) / 2, (bbox.bottom + bbox.top) / 2
    half = crop_um / 2
    view.zoom_box(pya.DBox(cx - half, cy - half, cx + half, cy + half))

res = int(os.environ.get("RES", "4096"))
view.save_image(out_png, res, res)
print(f"Wrote {out_png}")
