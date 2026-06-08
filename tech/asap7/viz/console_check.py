"""Load the viewer headless and dump console + errors + the loaded-mesh stat — no screenshot.
Diagnoses whether a specific macro mesh fails to load/parse in three.js."""
import sys
from playwright.sync_api import sync_playwright
extra=sys.argv[1] if len(sys.argv)>1 else "win=1"
url=f"http://localhost:8017/viewers/macros_instanced.html?{extra}"
print("URL:",url)
msgs=[]
with sync_playwright() as p:
    b=p.chromium.launch(headless=True,args=["--enable-unsafe-swiftshader","--use-gl=angle","--use-angle=swiftshader","--no-sandbox"])
    pg=b.new_page(viewport={"width":900,"height":600})
    pg.on("console",lambda m: msgs.append(("LOG",m.text[:300])))
    pg.on("pageerror",lambda e: msgs.append(("ERR",str(e)[:400])))
    pg.on("requestfailed",lambda r: msgs.append(("REQFAIL",r.url.split('/')[-1]+" :: "+str(r.failure))))
    pg.goto(url,wait_until="load",timeout=120000)
    try:
        pg.wait_for_function("document.getElementById('stat') && /instances|error/i.test(document.getElementById('stat').textContent)",timeout=150000)
    except Exception as e:
        msgs.append(("TIMEOUT",str(e)[:100]))
    stat=pg.eval_on_selector("#stat","e=>e.textContent")
    err=pg.eval_on_selector("#err","e=>e.textContent")
    # how many InstancedMeshes ended up in the scene, by querying three via the render info on the canvas
    info=pg.evaluate("()=>({calls: (window.__r&&window.__r.info)||null})")
    b.close()
print("\n--- STAT:",stat)
print("--- ERR DIV:",err)
print("--- console / errors ---")
for k,t in msgs: print(f"  [{k}] {t}")
