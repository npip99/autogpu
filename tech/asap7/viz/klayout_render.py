import pya, os
gds = os.environ["GDS"]; lyp = os.environ["LYP"]
lv = pya.LayoutView()
lv.load_layout(gds, 0)
lv.load_layer_props(lyp)
lv.max_hier()
lv.set_config("background-color", "#000000")
lv.set_config("grid-visible", "false")
lv.set_config("text-visible", "false")
# vivid per-layer palette (GDS layer number -> 0xRRGGBB); only these draw
PAL = {
    19:0xff3b30, 20:0xff9500, 30:0xffd60a, 40:0x34c759,   # m1 red, m2 orange, m3 yellow, m4 green
    50:0x32ade6, 60:0x5e5ce6, 70:0xff2d92,                # m5 cyan, m6 indigo, m7 pink
    18:0x8e8e93, 21:0x8e8e93, 25:0x8e8e93, 35:0x8e8e93,   # vias gray
    45:0x8e8e93, 55:0x8e8e93, 65:0x8e8e93,
}
it = lv.begin_layers()
while not it.at_end():
    p = it.current().dup()
    col = PAL.get(p.source_layer)
    p.visible = col is not None
    if col is not None:
        p.dither_pattern = 0       # solid fill
        p.frame_color = col
        p.fill_color = col
        p.transparent = True       # blend the metal stack
    lv.set_layer_properties(it, p)
    it.next()
# (name, x0,y0,x1,y1, width_px)
views = [
    ("corner",  0,   0,   235, 235, 4000),   # cmd + skews + 3x3 mac
    ("channel", 125, 125, 215, 215, 4000),   # zoom: mac block + the routing channels
    ("seam",    160, 130, 180, 210, 2600),   # one vertical channel between mac columns
]
for name,x0,y0,x1,y1,W in views:
    b = pya.DBox(x0,y0,x1,y1)
    H = int(W*(y1-y0)/(x1-x0))
    lv.zoom_box(b)
    out = "/out/kl_%s.png" % name
    lv.save_image_with_options(out, W, H, 0, 0, 0, b, False)  # monochrome=False -> colors
    print("wrote", out, W, "x", H)
