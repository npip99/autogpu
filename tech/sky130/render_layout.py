"""Render a layout (.gds or .def) to PNG via KLayout.

Run inside the OpenLane Docker image where klayout is on PATH and the
sky130A PDK is mounted. Example:

    docker run --rm -v $PWD:/work -v $HOME/.volare:/root/.volare \\
      ghcr.io/efabless/openlane2:2.3.10 \\
      klayout -b -r /work/render_layout.py \\
        -rd layout_path=/work/foo.gds \\
        -rd out_png=/work/foo.png

GDS is preferred for "what do the gates look like" renders — DEF reads
pull in many LEF-derived annotation layers (psaN test markers, areaid
overlays, padCenter, blanking, etc.) that paint a uniform diagonal
crosshatch across the die at our zoom.

Set the optional `gds_path` or `def_path` `-rd` arg explicitly, OR set
the legacy `def_path` arg — for backwards compat we accept both.
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
    os.path.expanduser("~/.volare/volare/sky130/versions"),
)

assert layout_path, "set layout_path / gds_path / def_path (-rd ...) or env"

pdk_version = sorted(os.listdir(pdk_root))[0]
sky130A = os.path.join(pdk_root, pdk_version, "sky130A")

view = pya.LayoutView()

is_def = layout_path.endswith(".def")
if is_def:
    # DEF needs LEF for cell expansion.
    tech_lef = glob.glob(
        os.path.join(sky130A, "libs.ref/sky130_fd_sc_hd/techlef/*.tlef")
    )
    cell_lefs = glob.glob(
        os.path.join(sky130A, "libs.ref/sky130_fd_sc_hd/lef/*.lef")
    )
    options = pya.LoadLayoutOptions()
    options.lefdef_config.read_lef_with_def = False
    options.lefdef_config.lef_files = tech_lef + cell_lefs
    view.create_layout(True)
    view.load_layout(layout_path, options, 0)
else:
    view.load_layout(layout_path)

# Recurse all subcell levels so std-cell internal geometry (diff, poly,
# contacts inside cell references) renders.
view.max_hier_levels = 99
view.min_hier_levels = 0

# Apply sky130 colors.
lyp_path = os.path.join(sky130A, "libs.tech/klayout/tech/sky130A.lyp")
if os.path.exists(lyp_path):
    view.load_layer_props(lyp_path)
view.add_missing_layers()

# Hide noise layers that paint a uniform diagonal hatch over the die
# without telling you anything about device structure or routing.
HIDE = {
    # Well + S/D implant fills — cover ~50% of die in big bands.
    (64, 20),    # nwell.drawing
    (94, 20),    # psdm.drawing  (PMOS S/D)
    (93, 44),    # nsdm.drawing  (NMOS S/D)
    (95, 20),    # npc.drawing   (nitride poly cut)
    # Vt / device markers — large rectangular hatched overlays.
    (78, 44),    # hvtp.drawing
    (125, 44),   # lvtn.drawing
    (11, 44),    # ldntm.drawing
    (18, 20),    # hvtr.drawing
    (80, 20),    # tunm.drawing
    # Placement / process region tags — cover all std-cell area.
    (81, 4),     # areaid.standardc
    (81, 20),    # padCenter.drawing
    (235, 4),    # PRBOUNDARY
    # Misc full-die annotation.
    (84, 44),    # prune.drawing
    (124, 40),   # blanking.drawing
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

# KLayout's canvas grid overlay; we want only the layout shapes.
view.set_config("grid-visible", "false")
view.set_config("background-color", "#000000")

bg_white = globals().get("bg_white") or os.environ.get("BG_WHITE", "0")
if bg_white == "1":
    view.set_config("background-color", "#ffffff")

view.zoom_fit()

# Optional zoom mode: "crop" picks a CROP_UM × CROP_UM box centered on
# the design's bbox; "fit" uses the whole die.
zoom_mode = globals().get("zoom_mode") or os.environ.get("ZOOM_MODE", "fit")
if zoom_mode == "crop":
    crop_um = float(os.environ.get("CROP_UM", "100"))
    # Use the first cellview's bbox (works for both gds and def loads).
    bbox = view.cellview(0).cell.dbbox()
    cx, cy = (bbox.left + bbox.right) / 2, (bbox.bottom + bbox.top) / 2
    half = crop_um / 2
    view.zoom_box(pya.DBox(cx - half, cy - half, cx + half, cy + half))

res = int(os.environ.get("RES", "4096"))
view.save_image(out_png, res, res)
print(f"Wrote {out_png}")
