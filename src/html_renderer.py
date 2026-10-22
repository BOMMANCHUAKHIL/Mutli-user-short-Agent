"""HTML -> 1080x1920 PNG with Playwright (Chromium).

Locked down: JavaScript is off, every request except data: URLs is blocked (so a
template cannot read local files or call the network), and fonts are embedded
as base64 from assets/fonts instead of being read by the page.
"""
import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
DEBUG_DIR = ROOT / "output" / "debug"
W, H = 1080, 1920


def _font_css() -> str:
    css = ""
    for fname, weight in (("NotoSansTelugu-Regular.ttf", 400), ("NotoSansTelugu-Bold.ttf", 700)):
        p = FONT_DIR / fname
        if p.exists():
            b64 = base64.b64encode(p.read_bytes()).decode()
            css += (f"@font-face{{font-family:'ShortsFont';font-weight:{weight};"
                    f"src:url(data:font/ttf;base64,{b64}) format('truetype');}}")
    return f"<style>{css}</style>" if css else ""


def _with_fonts(html: str) -> str:
    css = _font_css()
    if not css:
        return html
    return html.replace("<head>", "<head>" + css, 1) if "<head>" in html else css + html


def render_many(jobs: list[tuple[str, Path]], debug: bool = True) -> list[Path]:
    """jobs: [(html, out_png_path)]. One browser for all posters."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    outs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": W, "height": H},
                                      java_script_enabled=False, device_scale_factor=1)

            def gate(route):
                u = route.request.url
                if u.startswith("data:") or u == "about:blank":
                    route.continue_()
                else:
                    route.abort()

            ctx.route("**/*", gate)
            for n, (html, out) in enumerate(jobs):
                html = _with_fonts(html)
                if debug:
                    (DEBUG_DIR / ("last_render.html" if n == 0 else f"last_render_{n}.html")
                     ).write_text(html[:3_000_000], encoding="utf-8")
                page = ctx.new_page()
                page.set_content(html, wait_until="load")
                page.wait_for_timeout(500)        # let embedded fonts settle
                out = Path(out)
                out.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
                page.close()
                outs.append(out)
        finally:
            browser.close()
    return outs
