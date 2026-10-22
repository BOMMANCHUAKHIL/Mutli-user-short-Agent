"""Jinja2 poster templates (table / story / puzzle) rendered in a sandbox.

Templates are plain HTML+CSS at 1080x1920. Images arrive as data: URIs, so the
renderer never needs network or file access. User-supplied templates run in
Jinja's SandboxedEnvironment.
"""
import re

from jinja2.sandbox import SandboxedEnvironment

FONT = "'ShortsFont','Noto Sans Telugu','Noto Sans Devanagari','Noto Sans',sans-serif"


def clean_label(value) -> str:
    """Tidy an LLM label: no markdown, numbering, stray punctuation or double spaces."""
    s = str(value or "")
    s = re.sub(r"[*_`#]+", "", s)
    s = re.sub(r"^\s*\d+[.)]\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip(" \t-–—:;,.")
    return s


_env = SandboxedEnvironment(autoescape=True)
_env.filters["clean_label"] = clean_label

BASE_CSS = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:1080px;height:1920px;overflow:hidden}}
body{{font-family:{FONT};background:linear-gradient(160deg,#10122b,#1d1147 55%,#2a1458);color:#fff}}
.wm{{position:absolute;left:0;right:0;bottom:22px;text-align:center;font-size:30px;opacity:.75}}
"""

TABLE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>""" + BASE_CSS + """
.wrap{padding:40px 40px 0}
h1{font-size:60px;line-height:1.2;text-align:center;height:150px;display:flex;align-items:center;justify-content:center}
.heads,.row{display:grid;grid-template-columns:1fr 1fr;gap:40px}
.heads{margin:20px 0 10px}
.heads div{font-size:38px;font-weight:700;text-align:center;padding:12px;border-radius:18px}
.ha{background:#1f9d55}.hb{background:#d64545}
.row{margin-bottom:14px}
.cell{display:flex;align-items:center;gap:16px;background:rgba(255,255,255,.08);border-radius:22px;padding:5px;height:180px;overflow:hidden}
.cell img{width:170px;height:170px;object-fit:cover;border-radius:18px;flex:none}
.lab{font-size:36px;font-weight:700;line-height:1.25}
.tag{font-size:24px;opacity:.7;font-weight:400}
.tips{margin-top:12px;font-size:30px;line-height:1.35;background:rgba(255,255,255,.1);border-radius:20px;padding:16px 22px}
</style></head><body><div class="wrap">
<h1>{{ title|clean_label }}</h1>
<div class="heads"><div class="ha">{{ col_a_title|clean_label }}</div><div class="hb">{{ col_b_title|clean_label }}</div></div>
{% for r in rows %}<div class="row">
<div class="cell"><img src="{{ r.a_img }}"><div class="lab"><div class="tag">{{ r.tag|clean_label }}</div>{{ r.a_label|clean_label }}</div></div>
<div class="cell"><img src="{{ r.b_img }}"><div class="lab"><div class="tag">&nbsp;</div>{{ r.b_label|clean_label }}</div></div>
</div>{% endfor %}
<div class="tips">{% for t in tips %}<div>• {{ t|clean_label }}</div>{% endfor %}</div>
</div>{% if watermark %}<div class="wm">{{ watermark }}</div>{% endif %}</body></html>"""

STORY_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>""" + BASE_CSS + """
.wrap{padding:60px}
img{width:960px;height:860px;object-fit:cover;border-radius:36px;display:block;box-shadow:0 20px 60px rgba(0,0,0,.45)}
h1{font-size:64px;line-height:1.25;margin:40px 0 26px;text-align:center}
.cap{font-size:34px;opacity:.75;text-align:center;margin-bottom:22px}
p{font-size:42px;line-height:1.5;text-align:center}
.tags{margin-top:30px;text-align:center;font-size:32px;opacity:.7}
</style></head><body><div class="wrap">
<img src="{{ image }}">
<h1>{{ title|clean_label }}</h1>
{% if image_label %}<div class="cap">{{ image_label|clean_label }}</div>{% endif %}
<p>{{ story }}</p>
<div class="tags">{{ hashtags|join(' ') }}</div>
</div>{% if watermark %}<div class="wm">{{ watermark }}</div>{% endif %}</body></html>"""

PUZZLE_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>""" + BASE_CSS + """
.wrap{padding:50px 45px}
h1{font-size:56px;text-align:center;line-height:1.2;margin-bottom:20px}
.q{font-size:52px;font-weight:700;text-align:center;line-height:1.3;background:rgba(255,255,255,.1);border-radius:26px;padding:26px;margin-bottom:34px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:30px}
.opt{position:relative;background:rgba(255,255,255,.08);border-radius:28px;padding:12px;border:8px solid transparent}
.opt img{width:100%;height:410px;object-fit:cover;border-radius:20px;display:block}
.opt .l{font-size:38px;font-weight:700;text-align:center;margin-top:12px;min-height:56px}
.badge{position:absolute;top:22px;left:22px;width:70px;height:70px;border-radius:50%;background:#7c3aed;font-size:42px;font-weight:700;display:flex;align-items:center;justify-content:center}
.ok{border-color:#22c55e;background:rgba(34,197,94,.22)}
.dim{opacity:.35}
.ex{margin-top:34px;font-size:42px;line-height:1.4;text-align:center;background:rgba(34,197,94,.18);border-radius:24px;padding:24px}
.hint{margin-top:34px;font-size:40px;text-align:center;opacity:.85}
</style></head><body><div class="wrap">
<h1>{{ title|clean_label }}</h1>
<div class="q">{{ question|clean_label }}</div>
<div class="grid">
{% for o in options %}<div class="opt {% if reveal and loop.index0 == answer_index %}ok{% elif reveal %}dim{% endif %}">
<div class="badge">{{ 'ABCD'[loop.index0] }}</div><img src="{{ o.img }}"><div class="l">{{ o.label|clean_label }}</div></div>{% endfor %}
</div>
{% if reveal %}<div class="ex">✅ {{ 'ABCD'[answer_index] }} — {{ options[answer_index].label|clean_label }}<br>{{ explanation }}</div>
{% else %}<div class="hint">{{ hint }}</div>{% endif %}
</div>{% if watermark %}<div class="wm">{{ watermark }}</div>{% endif %}</body></html>"""

BUILTIN = {"table": TABLE_TEMPLATE, "story": STORY_TEMPLATE, "puzzle": PUZZLE_TEMPLATE}


def render_html(mode: str, context: dict, custom_template: str | None = None) -> str:
    """Render a poster. A custom template (from the dashboard) is used for table mode only."""
    src = custom_template.strip() if (mode == "table" and custom_template and custom_template.strip()) \
        else BUILTIN[mode]
    return _env.from_string(src).render(**context)
