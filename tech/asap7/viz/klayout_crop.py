"""KLayout top-down render of an arbitrary window. Run inside the ORFS image.
Env: GDS, LYP, VIZ_CROP="x0,y0,x1,y1" (µm), VIZ_W (px width), OUT (png path)."""
import pya, os
gds=os.environ["GDS"]; lyp=os.environ["LYP"]
x0,y0,x1,y1=[float(v) for v in os.environ["VIZ_CROP"].split(",")]
W=int(os.environ.get("VIZ_W","2000")); OUT=os.environ.get("OUT","/out/cmp_2d.png")
lv=pya.LayoutView(); lv.load_layout(gds,0); lv.load_layer_props(lyp); lv.max_hier()
lv.set_config("background-color","#000000"); lv.set_config("grid-visible","false"); lv.set_config("text-visible","false")
PAL={19:0xff3b30,20:0xff9500,30:0xffd60a,40:0x34c759,50:0x32ade6,60:0x5e5ce6,70:0xff2d92,
     18:0x8e8e93,21:0x8e8e93,25:0x8e8e93,35:0x8e8e93,45:0x8e8e93,55:0x8e8e93,65:0x8e8e93}
it=lv.begin_layers()
while not it.at_end():
    p=it.current().dup(); col=PAL.get(p.source_layer); p.visible=col is not None
    if col is not None:
        p.dither_pattern=0; p.frame_color=col; p.fill_color=col; p.transparent=True
    lv.set_layer_properties(it,p); it.next()
b=pya.DBox(x0,y0,x1,y1); H=int(W*(y1-y0)/(x1-x0)); lv.zoom_box(b)
lv.save_image_with_options(OUT,W,H,0,0,0,b,False)
print("wrote",OUT,W,"x",H)
