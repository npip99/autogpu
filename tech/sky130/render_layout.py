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

# Apply sky130 colors so layers are actually visible.
lyp_path = os.path.join(sky130A, "libs.tech/klayout/tech/sky130A.lyp")
if os.path.exists(lyp_path):
    view.load_layer_props(lyp_path)

view.zoom_fit()

# White background instead of KLayout's default black, for readability when
# zoomed out. Layer colors from the sky130 .lyp file still apply.
bg_white = globals().get("bg_white") or os.environ.get("BG_WHITE", "0")
if bg_white == "1":
    view.set_config("background-color", "#ffffff")

# `zoom`: "crop" picks a small box near the origin; "fit" uses the whole die.
zoom_mode = globals().get("zoom_mode") or os.environ.get("ZOOM_MODE", "fit")
if zoom_mode == "crop":
    # microns; tiny box so cells are visible at 4K canvas (cells are ~0.4µm
    # wide so a multi-mm die is sub-pixel even at 4K when fit).
    crop_um = float(os.environ.get("CROP_UM", "50"))
    box = pya.DBox(0.0, 0.0, crop_um, crop_um)
    view.zoom_box(box)

view.save_image(out_png, 4096, 4096)
print(f"Wrote {out_png}")
