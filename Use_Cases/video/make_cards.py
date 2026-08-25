"""Render the title card, the end card and the speed badges as PNGs.

The title card embeds the real mascot by importing MASCOT_SVG and PANEL_CSS
from cuaexp.panel, so the card can never drift from the Browsy in the video.
That drags the whole panel stylesheet in with it, and the panel defines its
own .wrap (position:fixed, width:0) and .chip -- which flattened this layout
into a zero-width column the first time. Card classes are prefixed bz* to
stay clear of it.

Chrome rather than ffmpeg drawtext: real typography, real layout, and the badges
come out with genuine transparency so they can be overlaid on the footage.
"""
import asyncio, base64, pathlib, sys

ROOT = pathlib.Path(r"c:\Users\render\Joshi\Cuaexp")
sys.path.insert(0, str(ROOT))
from cuaexp.recorder import Recorder
from cuaexp.session import BrowserSession
from cuaexp.panel import MASCOT_SVG, PANEL_CSS

OUT = pathlib.Path(sys.argv[1])
W, H = 2880, 1800

BASE = """
<style>
  * { margin:0; padding:0; box-sizing:border-box }
  html,body { width:2880px; height:1800px; overflow:hidden }
  body { background:#0d0f14; color:#e8ecf2;
         font-family:"Segoe UI Variable Display","Segoe UI",system-ui,sans-serif;
         display:flex; align-items:center; justify-content:center }
  .glow { position:absolute; inset:0;
          background:radial-gradient(1200px 700px at 50% 38%, rgba(70,120,255,.20), transparent 70%),
                     radial-gradient(900px 600px at 82% 88%, rgba(120,70,255,.14), transparent 70%) }
  .bzwrap { position:relative; width:2180px; text-align:center }
  .eyebrow { font-size:34px; letter-spacing:.42em; text-transform:uppercase;
             color:#7f8ea6; font-weight:600; margin-bottom:54px }
  h1 { font-size:118px; line-height:1.14; font-weight:700; letter-spacing:-.022em }
  .accent { background:linear-gradient(92deg,#6ea8ff,#a78bfa 55%,#67e8f9);
            -webkit-background-clip:text; background-clip:text; color:transparent }
  .rule { width:180px; height:5px; border-radius:3px; margin:64px auto 0;
          background:linear-gradient(90deg,#6ea8ff,#a78bfa) }
  .sub { margin-top:56px; font-size:44px; color:#9aa6b8; line-height:1.5 }
  .models { margin-top:70px; display:flex; gap:20px; justify-content:center; flex-wrap:wrap }
  .bzchip { font-size:33px; padding:18px 34px; border-radius:999px; color:#c7d2e2;
          background:#171c25; border:1px solid #2a3240; font-weight:500 }
  .foot { position:absolute; left:0; right:0; bottom:96px; text-align:center;
          font-size:32px; color:#5d6a80; letter-spacing:.06em }
  /* Browsy himself, straight out of cuaexp/panel.py, so the card cannot
     drift from the mascot the video actually shows. */
  .browsy { width:300px; height:300px; margin:0 auto 44px;
            display:flex; align-items:center; justify-content:center }
  .browsy .bot { width:300px !important; height:300px !important;
                 filter:drop-shadow(0 26px 60px rgba(0,0,0,.55)) }
  /* Freeze the idle animation so the capture is not caught mid-blink. */
  .browsy .bot, .browsy .bot * { animation:none !important }
</style>
"""

TITLE = BASE + "<style>" + PANEL_CSS + "</style>" + """
<div class="glow"></div>
<div class="bzwrap">
  <div class="browsy">""" + MASCOT_SVG + """</div>
  <div class="eyebrow">The task</div>
  <h1>Get the specs and the <span class="accent">real human experience</span><br>
      of five open&#8209;weight models &mdash;<br>then build a webpage to compare them.</h1>
  <div class="rule"></div>
  <div class="models">
    <span class="bzchip">Qwen 3.8&#8209;27B</span>
    <span class="bzchip">Muse Glimmer</span>
    <span class="bzchip">DeepSeek V4&#8209;Pro</span>
    <span class="bzchip">Kimi K3</span>
    <span class="bzchip">GLM&#8209;5.3</span>
  </div>
</div>
<div class="foot">BROWSY &middot; AN AGENT DRIVING A REAL CHROME</div>
"""

END = BASE + """
<style>
  .grid { display:grid; grid-template-columns:repeat(2,1fr); gap:38px; margin-top:78px }
  .cell { background:#151a22; border:1px solid #262e3b; border-radius:26px;
          padding:52px 56px; text-align:left; position:relative; overflow:hidden }
  .cell::before { content:''; position:absolute; left:0; top:0; bottom:0; width:6px;
                  background:linear-gradient(180deg,#6ea8ff,#a78bfa) }
  .k { font-size:30px; letter-spacing:.2em; text-transform:uppercase; color:#7f8ea6;
       font-weight:600 }
  .v { font-size:92px; font-weight:700; margin-top:16px; letter-spacing:-.02em;
       font-variant-numeric:tabular-nums }
  .n { font-size:30px; color:#8592a6; margin-top:14px }
  .wide { grid-column:1 / -1 }
  .wide .v { font-size:64px }
</style>
<div class="glow"></div>
<div class="bzwrap">
  <div class="eyebrow">What it took</div>
  <h1 style="font-size:92px">One run, <span class="accent">start to finish</span></h1>
  <div class="grid">
    <div class="cell"><div class="k">Time</div><div class="v">9m 04s</div>
      <div class="n">five models &middot; eight YouTube reviews</div></div>
    <div class="cell"><div class="k">Cost</div><div class="v">$3.10</div>
      <div class="n">about $0.62 per model</div></div>
    <div class="cell"><div class="k">Tokens in</div><div class="v">4.43M</div>
      <div class="n">75% served from cache</div></div>
    <div class="cell"><div class="k">Tokens out</div><div class="v">17.7K</div>
      <div class="n">56 tool calls, 0 errors</div></div>
    <div class="cell wide"><div class="k">Model</div><div class="v">GPT&#8209;5.6 Terra</div>
      <div class="n">driving Chrome over the DevTools Protocol</div></div>
  </div>
</div>
"""

BADGE = """
<style>
  * { margin:0; padding:0; box-sizing:border-box }
  html,body { width:2880px; height:1800px; overflow:hidden; background:transparent }
  body { font-family:"Segoe UI Variable Display","Segoe UI",system-ui,sans-serif }
  .b { position:absolute; right:104px; bottom:196px;
       display:flex; align-items:baseline; gap:6px;
       padding:26px 52px; border-radius:999px;
       background:rgba(13,15,20,.80); border:1px solid rgba(150,175,220,.30);
       box-shadow:0 18px 60px rgba(0,0,0,.55);
       color:#eaf0f8; font-weight:700; letter-spacing:-.01em }
  .n { font-size:76px; font-variant-numeric:tabular-nums }
  .x { font-size:46px; color:#8fb4ff; font-weight:600 }
</style>
<div class="b"><span class="n">__N__</span><span class="x">&times; speed</span></div>
"""


async def shot(sess, html, path, transparent=False):
    await sess.cdp.page("Emulation.setDeviceMetricsOverride",
                        {"width": W, "height": H, "deviceScaleFactor": 1, "mobile": False})
    if transparent:
        await sess.cdp.page("Emulation.setDefaultBackgroundColorOverride",
                            {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
    else:
        await sess.cdp.page("Emulation.setDefaultBackgroundColorOverride",
                            {"color": {"r": 13, "g": 15, "b": 20, "a": 1}})
    await sess.cdp.page("Page.navigate", {"url": "about:blank"})
    await asyncio.sleep(0.4)
    await sess.cdp.eval_js(
        "document.open();document.write(" + repr(html).replace("'", '"', 0) + ");document.close();"
        if False else "1")
    # write via a Blob-free route: set documentElement.innerHTML through a policy
    await sess.cdp.page("Page.setDocumentContent", {
        "frameId": (await sess.cdp.page("Page.getFrameTree"))["frameTree"]["frame"]["id"],
        "html": "<!doctype html><html><head><meta charset='utf-8'></head><body>" + html + "</body></html>"})
    await asyncio.sleep(1.2)
    res = await sess.cdp.page("Page.captureScreenshot", {
        "format": "png", "captureBeyondViewport": False,
        "clip": {"x": 0, "y": 0, "width": W, "height": H, "scale": 1}})
    path.write_bytes(base64.b64decode(res["data"]))
    print(f"  wrote {path.name}  {path.stat().st_size/1000:.0f} KB")


async def main():
    sess = BrowserSession(Recorder("cards", "video cards"), headless=True,
                          port=9488, profile=OUT / "cardprofile")
    await sess.start()
    try:
        await shot(sess, TITLE, OUT / "title.png")
        await shot(sess, END, OUT / "end.png")
        await shot(sess, BADGE.replace("__N__", "2"), OUT / "badge2x.png", transparent=True)
        await shot(sess, BADGE.replace("__N__", "6"), OUT / "badge6x.png", transparent=True)
    finally:
        if sess.proc:
            sess.proc.terminate()

asyncio.run(main())
