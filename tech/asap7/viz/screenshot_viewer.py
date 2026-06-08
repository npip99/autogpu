"""Headless screenshot of a three.js viewer at a given copy-pose, via swiftshader WebGL.
Usage: python screenshot_viewer.py <out.png> ["<pose string>"] [extra-query]
Default pose/url target macros_instanced.html?win=1."""
import sys, urllib.parse
from playwright.sync_api import sync_playwright
out=sys.argv[1] if len(sys.argv)>1 else "out/shot.png"
pose=sys.argv[2] if len(sys.argv)>2 else "layoutX=124.1 layoutY=151.8 height=124.90µm | yaw=-81.6° pitch=-83.0° roll=-0.0°"
extra=sys.argv[3] if len(sys.argv)>3 else "win=1"
url=f"http://localhost:8017/viewers/macros_instanced.html?{extra}&cam="+urllib.parse.quote(pose)
print("URL:",url)
with sync_playwright() as p:
    b=p.chromium.launch(headless=True, args=[
        "--enable-unsafe-swiftshader","--use-gl=angle","--use-angle=swiftshader",
        "--ignore-gpu-blocklist","--disable-gpu-sandbox","--no-sandbox"])
    pg=b.new_page(viewport={"width":1200,"height":760},device_scale_factor=1)
    pg.on("console",lambda m: print("  JS:",m.text[:160]))
    pg.on("pageerror",lambda e: print("  PAGEERR:",str(e)[:200]))
    pg.goto(url,wait_until="load",timeout=120000)
    try:
        pg.wait_for_function("document.getElementById('stat') && /instances/.test(document.getElementById('stat').textContent)",timeout=240000)
        print("  loaded:",pg.eval_on_selector("#stat","e=>e.textContent"))
    except Exception as e:
        print("  (stat wait timed out)",str(e)[:120])
    err=pg.eval_on_selector("#err","e=>e.textContent") if pg.query_selector("#err") else ""
    if err: print("  ERR DIV:",err)
    pg.wait_for_timeout(5000)
    pg.screenshot(path=out,timeout=240000)   # swiftshader needs a long time per heavy frame
    b.close()
print("wrote",out)
