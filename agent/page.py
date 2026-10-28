"""The insight dashboard page shown in GE's side panel (Canvas + IFrameSrcdoc).

Fully self-contained (inline CSS, JS and SVG): the frame's CSP blocks CDNs, fonts and fetches.
Layout: headline and summary, chart-style and column chips, four KPI tiles, the chart with a
"View data" table, insight cards with their evidence, actions and follow-up questions, then
"How these numbers were made" (every computed fact, data notes and caveats).
Chips post {type:'a2ui_action'} to the parent; GE delivers them to the agent like native buttons.
"""
import html
import json

from . import charts, explorer
from .analysis import STYLES, Analysis, available
from .schema import Narrative

CHART_ACTION = "chart"
ASK_ACTION = "ask"

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--axis:#c3c2b7;--line:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s1soft:rgba(42,120,214,.14);--up:#2a78d6;--down:#e34948;
--good:#006300;--bad:#d03b3b;--accent:#2a78d6;--chip:#eef3fb;--warn:#fab219;
--r0:#e8f1fd;--ri0:#0b0b0b;--r1:#cde2fb;--ri1:#0b0b0b;--r2:#b7d3f6;--ri2:#0b0b0b;--r3:#9ec5f4;--ri3:#0b0b0b;--r4:#86b6ef;--ri4:#0b0b0b;--r5:#6da7ec;--ri5:#0b0b0b;--r6:#5598e7;--ri6:#0b0b0b;--r7:#3987e5;--ri7:#ffffff;--r8:#2a78d6;--ri8:#ffffff;--r9:#256abf;--ri9:#ffffff;--r10:#1c5cab;--ri10:#ffffff;--r11:#104281;--ri11:#ffffff}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--axis:#383835;--line:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s1soft:rgba(57,135,229,.22);
--up:#3987e5;--down:#e66767;--good:#0ca30c;--bad:#e66767;--accent:#3987e5;--chip:#1f2a3a;
--r0:#104281;--ri0:#ffffff;--r1:#1c5cab;--ri1:#ffffff;--r2:#256abf;--ri2:#ffffff;--r3:#2a78d6;--ri3:#ffffff;--r4:#3987e5;--ri4:#ffffff;--r5:#5598e7;--ri5:#0b0b0b;--r6:#6da7ec;--ri6:#0b0b0b;--r7:#86b6ef;--ri7:#0b0b0b;--r8:#9ec5f4;--ri8:#0b0b0b;--r9:#b7d3f6;--ri9:#0b0b0b;--r10:#cde2fb;--ri10:#0b0b0b;--r11:#e8f1fd;--ri11:#0b0b0b}}
:root[data-theme=dark]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--axis:#383835;--line:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s1soft:rgba(57,135,229,.22);
--up:#3987e5;--down:#e66767;--good:#0ca30c;--bad:#e66767;--accent:#3987e5;--chip:#1f2a3a;
--r0:#104281;--ri0:#ffffff;--r1:#1c5cab;--ri1:#ffffff;--r2:#256abf;--ri2:#ffffff;--r3:#2a78d6;--ri3:#ffffff;--r4:#3987e5;--ri4:#ffffff;--r5:#5598e7;--ri5:#0b0b0b;--r6:#6da7ec;--ri6:#0b0b0b;--r7:#86b6ef;--ri7:#0b0b0b;--r8:#9ec5f4;--ri8:#0b0b0b;--r9:#b7d3f6;--ri9:#0b0b0b;--r10:#cde2fb;--ri10:#0b0b0b;--r11:#e8f1fd;--ri11:#0b0b0b}
.theme{display:inline-flex;border:1px solid var(--line);border-radius:999px;overflow:hidden;float:right;margin-left:8px}
.theme button{border:0;background:var(--surface);color:var(--ink2);font:inherit;font-size:12px;padding:3px 10px;cursor:pointer}
.theme button.on{background:var(--accent);color:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:20px 18px 40px}
.eyebrow{font-size:13px;color:var(--muted);letter-spacing:.02em;text-transform:uppercase}
h1{font-size:26px;line-height:1.25;margin:6px 0 8px;font-weight:700}
.summary{color:var(--ink2);margin:0 0 14px;max-width:820px}
.controls{display:flex;flex-direction:column;gap:8px;margin:12px 0 18px}
.crow{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.crow>b{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;min-width:78px}
.chip{border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:999px;padding:5px 12px;font:inherit;
font-size:13px;cursor:pointer}
.chip:hover{background:var(--chip)}.chip.on{background:var(--accent);border-color:var(--accent);color:#fff;cursor:default}
.chip.sent{opacity:.6}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:16px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .k{font-size:13px;color:var(--ink2)}.kpi .v{font-size:24px;font-weight:700;margin:2px 0;overflow-wrap:anywhere}
.kpi .n{font-size:13px;color:var(--muted)}.kpi.up .v{color:var(--good)}.kpi.down .v{color:var(--bad)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 16px 12px;margin-bottom:16px}
.card h2{font-size:17px;margin:0 0 2px}.card .sub{font-size:13px;color:var(--muted);margin:0 0 10px}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;color:var(--ink2);margin:0 0 6px}
.legend .sw{display:inline-block;width:14px;height:10px;border-radius:2px;margin-right:6px;vertical-align:0}
.sw.s1{background:var(--s1)}.sw.s2{background:var(--s2)}.sw.fit{background:none;border-top:2px dotted var(--ink2);height:0;vertical-align:3px}
.sw.band{background:var(--s1soft)}.sw.rest{background:var(--axis)}.sw.start{background:var(--muted)}.sw.up{background:var(--up)}
.sw.down{background:var(--down)}.sw.iqr{background:var(--s1soft)}.sw.flagsw{background:none;outline:2px solid var(--ink);height:10px}
.ramp{display:inline-flex;align-items:center;gap:0}.ramp i{display:inline-block;width:12px;height:10px}
.viz{width:100%;height:auto;display:block;overflow:visible}
.viz text{font-family:inherit}.tick{font-size:12px;fill:var(--muted)}.axislab{font-size:12px;fill:var(--ink2)}
.ann{font-size:12px;fill:var(--ink);font-weight:600}.ann.muted{fill:var(--ink2);font-weight:400}.note{font-size:12px;fill:var(--muted)}
.grid{stroke:var(--grid);stroke-width:1}.divider{stroke:var(--axis);stroke-dasharray:3 3}
.line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.line.thin{stroke-width:2;vector-effect:non-scaling-stroke}
.line.s1{stroke:var(--s1)}.line.s2{stroke:var(--s2)}.line.dashed{stroke-dasharray:6 5}.line.fit{stroke:var(--ink2);stroke-width:1.5;stroke-dasharray:2 4}
.band{fill:var(--s1soft)}.dot.s1{fill:var(--s1);stroke:var(--surface);stroke-width:2}
.mark{fill:none;stroke:var(--ink);stroke-width:2}.hit{fill:transparent}.hit:hover{fill:var(--s1soft);fill-opacity:.35}
.wf.start,.wf.end{fill:var(--muted)}.wf.up{fill:var(--up)}.wf.down{fill:var(--down)}.conn{stroke:var(--axis);stroke-dasharray:2 2}
.pt{fill:var(--s1);fill-opacity:.55;stroke:var(--surface);stroke-width:1}.pt.flagged{fill:var(--s2);fill-opacity:1;stroke:var(--surface);stroke-width:2}
.hbar.s1{fill:var(--s1)}.hbar.s2{fill:var(--s2)}.iqr{fill:var(--s1soft)}.medl{stroke:var(--ink);stroke-width:2}.meanl{stroke:var(--ink2);stroke-width:1.5;stroke-dasharray:5 4}
.axisnote{font-size:12px;color:var(--muted);margin:4px 0 0}
.pareto{display:flex;flex-direction:column;gap:3px}
.prow{display:grid;grid-template-columns:minmax(90px,26%) 1fr 70px 64px;gap:10px;align-items:center;font-size:14px;padding:2px 0}
.prow.phead{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
.plab{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pval{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
.pcum{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.ptrack{height:16px}.pbar{display:block;height:16px;border-radius:0 4px 4px 0;transform-origin:left;animation:grow .6s ease-out both}
.pbar.vital{background:var(--s1)}.pbar.rest{background:var(--axis)}
.pcut{border-top:2px dashed var(--ink2);margin:4px 0 6px;font-size:12px;color:var(--ink2)}.pcut span{background:var(--surface);position:relative;top:-10px;padding-right:8px}
@keyframes grow{from{transform:scaleX(0)}}
.hmwrap{overflow-x:auto}.heat{border-collapse:separate;border-spacing:2px;font-size:12px;font-variant-numeric:tabular-nums}
.heat th{font-weight:600;color:var(--ink2);padding:2px 6px;white-space:nowrap;text-align:center}.heat th.rowh{text-align:left}
.heat.dense td{min-width:30px}.heat td{min-width:44px;height:30px;text-align:center;border-radius:3px;padding:0 4px}.heat td.empty{background:var(--grid)}
.heat td.tot{color:var(--ink2);font-weight:600;text-align:right}
.heat td.flag{outline:2px solid var(--ink);outline-offset:-2px;position:relative}
.heat td.flag.up::after,.heat td.flag.down::after{position:absolute;top:0;right:2px;font-size:9px}
.heat td.flag.up::after{content:"▲"}.heat td.flag.down::after{content:"▼"}
.minis{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
.mini{border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.mhead{display:flex;justify-content:space-between;gap:6px;font-size:13px}.mhead b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mval{font-size:18px;font-weight:700}.mini svg{width:100%;height:56px;display:block}
.mch{font-size:12px;white-space:nowrap}.mch.up{color:var(--good)}.mch.down{color:var(--bad)}
details.data summary{cursor:pointer;font-size:13px;color:var(--accent);margin-top:8px}
.tbl{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px;font-variant-numeric:tabular-nums}
.tbl th,.tbl td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right}.tbl th:first-child,.tbl td:first-child{text-align:left}
.tblwrap{max-height:320px;overflow:auto}
.insights{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-bottom:16px}
.ins{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--k);border-radius:10px;padding:12px 14px}
.ins .kind{display:flex;align-items:center;gap:6px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink2)}
.ins .kind svg{width:16px;height:16px;color:var(--k)}.ins h3{font-size:16px;margin:4px 0 4px}.ins p{margin:0 0 8px;color:var(--ink)}
.ev{display:inline-block;font-size:11px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin:0 4px 2px 0;color:var(--ink2);cursor:help}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.two ol{margin:6px 0 0;padding-left:20px}.two li{margin-bottom:6px}
.asks{display:flex;flex-direction:column;gap:6px;align-items:flex-start;margin-top:6px}
.asks .chip{text-align:left;border-radius:10px}
.how{font-size:13px;color:var(--ink2)}.how summary{cursor:pointer;color:var(--accent);font-size:14px}
.how ul{padding-left:18px}.how li{margin:3px 0}.how code{color:var(--muted);font-size:12px}
.heads{display:flex;gap:8px;align-items:flex-start;background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--warn);
border-radius:10px;padding:10px 14px;margin:-4px 0 16px;font-size:14px;color:var(--ink2)}.heads b{color:var(--ink);white-space:nowrap}
.badge{display:inline-flex;gap:6px;align-items:center;font-size:12px;color:var(--ink2);background:var(--chip);border-radius:999px;padding:3px 10px;margin-top:6px}
#tip{position:fixed;pointer-events:none;z-index:9;background:var(--ink);color:var(--surface);font-size:12px;padding:6px 9px;border-radius:6px;max-width:320px;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.fade{animation:fade .5s ease-out both}@keyframes fade{from{opacity:0;transform:translateY(6px)}}
@media (prefers-reduced-motion:reduce){.pbar,.fade{animation:none}}
@media (max-width:560px){h1{font-size:21px}.prow{grid-template-columns:minmax(70px,30%) 1fr 58px}.pcum{display:none}}
"""

JS = """
(function(){var K='sheetInsightsTheme',root=document.documentElement,t='auto';
try{t=localStorage.getItem(K)||'auto';}catch(e){}
function set(v){t=v;if(v==='auto')root.removeAttribute('data-theme');else root.setAttribute('data-theme',v);
  document.querySelectorAll('.theme button').forEach(function(b){b.classList.toggle('on',b.dataset.t===v);});
  try{localStorage.setItem(K,v);}catch(e){}}
document.querySelectorAll('.theme button').forEach(function(b){b.onclick=function(){set(b.dataset.t);};});set(t);})();
(function(){
var tip=document.getElementById('tip');
document.addEventListener('mousemove',function(e){
  var t=e.target.closest&&e.target.closest('[data-tip]');
  if(!t){tip.hidden=true;return;}
  tip.textContent=t.getAttribute('data-tip');tip.hidden=false;
  var x=e.clientX+14,y=e.clientY+14,w=tip.offsetWidth,h=tip.offsetHeight;
  if(x+w>window.innerWidth-8)x=e.clientX-w-14; if(y+h>window.innerHeight-8)y=e.clientY-h-14;
  tip.style.left=x+'px';tip.style.top=y+'px';
});
document.addEventListener('mouseleave',function(){tip.hidden=true;});
function send(btn){
  var data=JSON.parse(btn.getAttribute('data-ctx'));
  var msg={type:'a2ui_action',action:btn.getAttribute('data-act'),data:data};
  try{(window.parent||window.top).postMessage(msg,'*');}catch(err){}
  btn.classList.add('sent');
}
document.querySelectorAll('button[data-act]').forEach(function(b){b.addEventListener('click',function(){send(b);});});
})();
"""

# Small inline icons (stroke follows currentColor, so they suit dark mode).
_ICON = {
    "trend": '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    "driver": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "risk": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
    "opportunity": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    "anomaly": '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16h.01"/>',
    "pattern": '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/>',
}
_KIND_COLOUR = {"trend": "var(--s1)", "driver": "#1baf7a", "risk": "var(--bad)", "opportunity": "#008300",
                "anomaly": "var(--s2)", "pattern": "#6250d6"}


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _icon(kind: str) -> str:
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{_ICON.get(kind, _ICON["pattern"])}</svg>')


def _button(label: str, action: str, ctx: dict, cls: str = "chip", on: bool = False) -> str:
    if on:
        return f'<button class="{cls} on" disabled aria-pressed="true">{_e(label)}</button>'
    return f'<button class="{cls}" data-act="{action}" data-ctx="{_e(json.dumps(ctx))}">{_e(label)}</button>'


def chart_title(a: Analysis) -> tuple[str, str]:
    c = a.chart
    g = a.prof.grain or "period"
    word = "average" if a.agg == "mean" else "total"
    if a.style == "explore":
        return explorer.title(a)
    return {
        "trend": (f"{a.measure} by {g}, with trend and forecast", f"{word.capitalize()} per {g}. Hover any point for the value and its change."),
        "pareto": (f"{a.measure} by {a.dim}, ranked", "Blue bars are the few items that together make 80% of the total."),
        "bridge": (f"What moved {a.measure} from {c.get('start', {}).get('label')} to {c.get('end', {}).get('label')}",
                   f"Start level, the change from each {a.dim}, end level."),
        "heatmap": (f"{a.measure} by {a.dim} and {c.get('col_name')}", "Colour runs low to high along the scale. Outlined cells are far from what their row and column predict."),
        "multiples": (f"{a.measure} by {g}, one panel per {a.dim}", "Same scale in every panel; the arrow is the change from first to last."),
        "scatter": (f"{c.get('y')} against {c.get('x')}", "Each dot is a row. Orange dots sit furthest from the fit line."),
        "distribution": (f"How {a.measure} is spread across rows", "Bars count rows in each range; orange ranges are unusually high."),
    }[a.style]


def _table(t: dict) -> str:
    if not t:
        return ""
    head = "".join(f"<th>{_e(c)}</th>" for c in t["columns"])
    rows = "".join("<tr>" + "".join(f"<td>{_e(v)}</td>" for v in r) + "</tr>" for r in t["rows"])
    return (f'<details class="data"><summary>View data ({len(t["rows"])} rows)</summary><div class="tblwrap">'
            f'<table class="tbl"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div></details>')


def page(a: Analysis, n: Narrative, file: str) -> str:
    base = {"sheet": a.sheet, "style": a.style, "measure": a.measure, "dim": a.dim}
    styles = "".join(
        _button(STYLES[s][0], CHART_ACTION, {**base, "style": s, "prompt": f"Show {a.sheet} as {STYLES[s][0].lower()}"}, on=s == a.style)
        for s, ok in available(a.prof).items() if ok and s != "auto")
    measures = "".join(_button(m, CHART_ACTION, {**base, "measure": m, "prompt": f"Chart {m} instead"}, on=m == a.measure)
                       for m in a.prof.measures[:8])
    dims = "".join(_button(d, CHART_ACTION, {**base, "dim": d, "prompt": f"Break it down by {d}"}, on=d == a.dim)
                   for d in a.prof.dimensions[:8])
    controls = f'<div class="crow"><b>Chart</b>{styles}</div><div class="crow"><b>Number</b>{measures}</div>'
    if dims and a.style not in ("trend", "distribution"):
        controls += f'<div class="crow"><b>By</b>{dims}</div>'

    kpis = "".join(f'<div class="kpi {_e(k.get("tone") or "")}"><div class="k">{_e(k["label"])}</div><div class="v">{_e(k["value"])}</div>'
                   f'<div class="n">{_e(k.get("note", ""))}</div></div>' for k in a.kpis)
    title, sub = chart_title(a)
    facts = {f.id: f.text for f in a.facts}
    cards = []
    for ins in n.insights:
        ev = "".join(f'<span class="ev" data-tip="{_e(facts[e])}">{_e(e)}</span>' for e in ins.evidence if e in facts)
        cards.append(f'<article class="ins fade" style="--k:{_KIND_COLOUR.get(ins.kind, "var(--s1)")}"><div class="kind">{_icon(ins.kind)}{_e(ins.kind)}</div>'
                     f'<h3>{_e(ins.title)}</h3><p>{_e(ins.text)}</p>{f"<div>{ev}</div>" if ev else ""}</article>')
    actions = "".join(f"<li>{_e(x)}</li>" for x in n.actions)
    asks = "".join(_button(q, ASK_ACTION, {**base, "prompt": q}) for q in n.questions)
    fact_list = "".join(f"<li><code>{_e(f.id)}</code> {_e(f.text)}</li>" for f in a.facts)
    caveats = "".join(f"<li>{_e(c)}</li>" for c in n.caveats)
    notes = "".join(f"<li>{_e(x)}</li>" for x in a.notes if x not in n.caveats)
    agg = "averaged" if a.agg == "mean" else "added up"
    warn = [f.text for f in a.facts if f.kind == "quality" and f.weight >= 2]
    heads = f'<div class="heads"><b>Heads-up</b><span>{_e(" ".join(warn))}</span></div>' if warn else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(a.sheet)} insights</title><style>{CSS}</style></head><body><div class="wrap">
<div class="theme" role="group" aria-label="Theme"><button data-t="auto">Auto</button><button data-t="light">Light</button><button data-t="dark">Dark</button></div>
<div class="eyebrow">{_e(file)} · {_e(a.sheet)} tab · {a.prof.rows:,} rows</div>
<h1>{_e(n.headline)}</h1><p class="summary">{_e(n.summary)}</p>
<div class="controls">{controls}</div>
{"" if a.style == "explore" else f'<section class="kpis">{kpis}</section>'}
{heads}<section class="card"><h2>{_e(title)}</h2><p class="sub">{_e(sub)}</p>{charts.draw(a)}{_table(a.table)}</section>
<section class="insights">{"".join(cards)}</section>
<section class="two"><div class="card"><h2>What to do next</h2><ol>{actions}</ol></div>
<div class="card"><h2>Ask next</h2><div class="asks">{asks}</div></div></section>
<section class="card how"><details><summary>How these numbers were made</summary>
<p>{_e(a.measure)} values are {agg}{f" by {_e(a.prof.grain)}" if a.prof.time else ""}. Every figure on this page was computed from your sheet in Python;
the AI only wrote the words around them, and any sentence whose numbers did not match these facts was replaced by the facts themselves.</p>
{f"<h3>Caveats</h3><ul>{caveats}</ul>" if caveats else ""}{f"<h3>Data notes</h3><ul>{notes}</ul>" if notes else ""}
<h3>Computed facts</h3><ul>{fact_list}</ul></details>
<div class="badge">Numbers checked against the sheet · {len(a.facts)} facts</div></section>
</div><div id="tip" role="tooltip" hidden></div><script>{JS}</script></body></html>"""

