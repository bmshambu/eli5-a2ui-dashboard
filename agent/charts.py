"""The chart in the canvas page, drawn here as inline SVG/HTML from analysis.chart (no CDN, no JS
charting library: GE's frame CSP blocks fetches, and this keeps every label exact).

Colours are CSS custom properties defined in page.CSS, with dark-mode steps, following the
dataviz rules: one categorical hue per job in a fixed order, a single-hue ramp for magnitude,
blue/red for up/down, thin 2px lines, recessive grid, one y axis only. Every mark carries
data-tip, which page.JS turns into a hover tooltip; the "View data" table under the chart is
the accessible and exact view.
"""
import html
import math

from . import explorer
from .analysis import num, pct

W, H = 760, 340
L, R, T, B = 64, 24, 28, 46


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not math.isfinite(lo) or not math.isfinite(hi):
        return [0.0, 1.0]
    if hi == lo:
        hi, lo = hi + (abs(hi) or 1) * 0.5, lo - (abs(lo) or 1) * 0.5
    raw = (hi - lo) / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v < hi + step * 0.999:
        ticks.append(round(v, 10))
        v += step
    return ticks


class _Y:
    def __init__(self, lo, hi, top=T, bottom=H - B):
        self.ticks = nice_ticks(lo, hi)
        self.lo, self.hi = self.ticks[0], self.ticks[-1]
        self.top, self.bottom = top, bottom

    def __call__(self, v: float) -> float:
        return self.bottom - (v - self.lo) / ((self.hi - self.lo) or 1) * (self.bottom - self.top)


def _grid(y: _Y, left=L, right=W - R) -> str:
    out = []
    for t in y.ticks:
        yy = y(t)
        out.append(f'<line class="grid" x1="{left}" x2="{right}" y1="{yy:.1f}" y2="{yy:.1f}"/>'
                   f'<text class="tick" x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end">{_e(num(t))}</text>')
    return "".join(out)


def _xlabels(labels: list[str], xs: list[float], every: int | None = None) -> str:
    n = len(labels)
    every = every or max(1, math.ceil(n / 8))
    show = [i for i in range(n) if i % every == 0]
    if show[-1] != n - 1 and (n - 1 - show[-1]) >= every * 0.75:
        show.append(n - 1)
    return "".join(f'<text class="tick" x="{xs[i]:.1f}" y="{H - B + 20}" text-anchor="middle">{_e(labels[i])}</text>' for i in show)


def _svg(body: str, label: str, h: int = H) -> str:
    return f'<svg class="viz" viewBox="0 0 {W} {h}" role="img" aria-label="{_e(label)}">{body}</svg>'


def _legend(items: list[tuple[str, str]]) -> str:
    """[(css class of the swatch, text)]"""
    return '<div class="legend">' + "".join(f'<span><i class="sw {c}"></i>{_e(t)}</span>' for c, t in items) + "</div>"


def _path(points) -> str:
    pts = [(x, y) for x, y in points if y is not None]
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) if pts else ""


# ── Trend + forecast ─────────────────────────────────────────────────────────

def trend(c: dict, measure: str) -> str:
    labels, vals = c["labels"], c["values"]
    fc = c.get("forecast") or []
    n, k = len(vals), len(fc)
    span = max(n + k - 1, 1)
    xs = [L + i * (W - L - R) / span for i in range(n + k)]
    pool = vals + [v for v in (c.get("rolling") or []) if v is not None] + (c.get("fit") or []) + [f["lo"] for f in fc] + [f["hi"] for f in fc]
    y = _Y(min(pool), max(pool))
    parts = [_grid(y)]
    if fc:
        band = [(xs[n - 1], y(vals[-1]))] + [(xs[n + i], y(f["hi"])) for i, f in enumerate(fc)]
        band += [(xs[n + i], y(f["lo"])) for i, f in reversed(list(enumerate(fc)))]
        parts.append('<polygon class="band" points="' + " ".join(f"{a:.1f},{b:.1f}" for a, b in band) + '"/>')
        parts.append(f'<line class="divider" x1="{xs[n - 1]:.1f}" x2="{xs[n - 1]:.1f}" y1="{T}" y2="{H - B}"/>'
                     f'<text class="note" x="{xs[n - 1] + 6:.1f}" y="{T + 12}">Forecast</text>')
        parts.append(f'<path class="line s1 dashed" d="{_path([(xs[n - 1], y(vals[-1]))] + [(xs[n + i], y(f["value"])) for i, f in enumerate(fc)])}"/>')
    if c.get("fit"):
        parts.append(f'<path class="line fit" d="{_path([(xs[i], y(v)) for i, v in enumerate(c["fit"])])}"/>')
    if c.get("rolling"):
        parts.append(f'<path class="line s2" d="{_path([(xs[i], y(v) if v is not None else None) for i, v in enumerate(c["rolling"])])}"/>')
    parts.append(f'<path class="line s1 main" d="{_path([(xs[i], y(v)) for i, v in enumerate(vals)])}"/>')
    if n <= 48:
        parts += [f'<circle class="dot s1" cx="{xs[i]:.1f}" cy="{y(v):.1f}" r="3.5"/>' for i, v in enumerate(vals)]
    for key, word, below in (("peak", "Peak", False), ("low", "Low", True), ("jump", "Biggest move", False)):
        i = c.get(key)
        if i is None or (key == "jump" and i in (c.get("peak"), c.get("low"))):
            continue
        yy = y(vals[i])
        ty = yy + 22 if below else yy - 12
        anchor = "end" if xs[i] > W - 120 else "start" if xs[i] < L + 60 else "middle"
        parts.append(f'<circle class="mark" cx="{xs[i]:.1f}" cy="{yy:.1f}" r="6"/>'
                     f'<text class="ann" x="{xs[i]:.1f}" y="{ty:.1f}" text-anchor="{anchor}">{_e(word)} · {_e(num(vals[i]))}</text>')
    if fc:
        f = fc[-1]
        parts.append(f'<text class="ann muted" x="{xs[-1]:.1f}" y="{y(f["value"]) - 10:.1f}" text-anchor="end">{_e(num(f["value"]))}</text>')
    all_labels = labels + [f["label"] for f in fc]
    parts.append(_xlabels(all_labels, xs))
    # Hover columns, wider than the marks.
    colw = (W - L - R) / span
    for i in range(n + k):
        if i < n:
            tip = f"{labels[i]}: {num(vals[i])}"
            if c.get("rolling") and c["rolling"][i] is not None:
                tip += f" · 3-period avg {num(c['rolling'][i])}"
            if i:
                ch = (vals[i] - vals[i - 1]) / abs(vals[i - 1]) * 100 if vals[i - 1] else None
                tip += f" · {pct(ch)} vs previous" if ch is not None else ""
        else:
            f = fc[i - n]
            tip = f"{f['label']} forecast: {num(f['value'])} (range {num(f['lo'])} to {num(f['hi'])})"
        parts.append(f'<rect class="hit" x="{xs[i] - colw / 2:.1f}" y="{T}" width="{colw:.1f}" height="{H - B - T}" data-tip="{_e(tip)}"/>')
    legend = [("s1", measure), ("s2", "3-period average"), ("fit", "Trend line"), ("band", "Forecast range (95%)")]
    legend = [x for x in legend if (x[0] != "s2" or c.get("rolling")) and (x[0] != "fit" or c.get("fit")) and (x[0] != "band" or fc)]
    return _legend(legend) + _svg("".join(parts), f"{measure} over time with trend and forecast")


# ── Ranked drivers (Pareto) ──────────────────────────────────────────────────

def pareto(c: dict, measure: str, dim: str) -> str:
    items = c["items"]
    top = max((abs(it["value"]) for it in items), default=1) or 1
    rows = []
    for i, it in enumerate(items):
        vital = i <= c["cut"] and not it.get("other") and not c.get("mean")
        width = max(abs(it["value"]) / top * 100, 0.6)
        tip = f"{it['label']}: {num(it['value'])} · {pct(it['share'], False)} of total · running {pct(it['cum'], False)}"
        rows.append(f'<div class="prow" data-tip="{_e(tip)}"><span class="plab">{_e(it["label"])}</span>'
                    f'<span class="ptrack"><span class="pbar {"vital" if vital else "rest"}" style="width:{width:.1f}%"></span></span>'
                    f'<span class="pval">{_e(num(it["value"]))}</span><span class="pcum">{"" if c.get("mean") else _e(pct(it["cum"], False))}</span></div>')
        if i == c["cut"] and not c.get("mean") and i < len(items) - 1:
            rows.append(f'<div class="pcut"><span>These {i + 1} of {len(items)} make 80% of the total</span></div>')
    head = (f'<div class="prow phead"><span class="plab">{_e(dim)}</span><span class="ptrack"></span>'
            f'<span class="pval">{_e(measure)}</span><span class="pcum">{"" if c.get("mean") else "Running share"}</span></div>')
    legend = _legend([("s1", "The vital few (to 80%)"), ("rest", "The rest")]) if not c.get("mean") else ""
    return legend + '<div class="pareto">' + head + "".join(rows) + "</div>"


# ── Change bridge (waterfall) ────────────────────────────────────────────────

def bridge(c: dict, measure: str) -> str:
    steps = c["steps"]
    bars = [("start", c["start"]["label"], c["start"]["value"], 0.0)]
    run = c["start"]["value"]
    for s in steps:
        bars.append(("up" if s["value"] >= 0 else "down", s["label"], s["value"], run))
        run += s["value"]
    bars.append(("end", c["end"]["label"], c["end"]["value"], 0.0))
    levels = [c["start"]["value"], c["end"]["value"]]
    r = c["start"]["value"]
    for s in steps:
        r += s["value"]
        levels.append(r)
    lo, hi = min(levels), max(levels)
    pad = (hi - lo) * 0.25 or abs(hi) * 0.1 or 1
    base = 0.0 if lo - pad <= 0 <= hi or lo <= 0 else lo - pad
    y = _Y(base, hi + pad * 0.3)
    n = len(bars)
    slot = (W - L - R) / n
    bw = min(56, slot * 0.62)
    parts = [_grid(y)]
    rotate = n > 8
    for i, (kind, lab, v, start) in enumerate(bars):
        x = L + i * slot + (slot - bw) / 2
        if kind in ("start", "end"):
            y0, y1 = y(max(y.lo, 0) if y.lo <= 0 else y.lo), y(v)
            text = num(v)
        else:
            y0, y1 = y(start), y(start + v)
            text = ("+" if v >= 0 else "") + num(v)
        top, h = min(y0, y1), max(abs(y1 - y0), 1.5)
        tip = f"{lab}: {text}" + ("" if kind in ("start", "end") else f" · level after {num(start + v)}")
        parts.append(f'<rect class="wf {kind}" x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="3" data-tip="{_e(tip)}"/>')
        parts.append(f'<text class="ann" x="{x + bw / 2:.1f}" y="{top - 6:.1f}" text-anchor="middle">{_e(text)}</text>')
        if i < n - 1:
            nxt_level = v if kind in ("start", "end") else start + v
            parts.append(f'<line class="conn" x1="{x + bw:.1f}" x2="{x + slot:.1f}" y1="{y(nxt_level):.1f}" y2="{y(nxt_level):.1f}"/>')
        short = lab if len(lab) <= 12 else lab[:11] + "…"
        lx, ly = x + bw / 2, H - B + 18
        parts.append(f'<text class="tick" x="{lx:.1f}" y="{ly}" text-anchor="{"end" if rotate else "middle"}"'
                     f'{f" transform=\"rotate(-30 {lx:.1f} {ly})\"" if rotate else ""}>{_e(short)}</text>')
    note = f'<p class="axisnote">Axis starts at {_e(num(y.lo))} so the steps are visible.</p>' if y.lo > 0 else ""
    legend = _legend([("start", "Start and end level"), ("up", "Increase"), ("down", "Decrease")])
    return legend + _svg("".join(parts), f"Bridge of {measure} from {c['start']['label']} to {c['end']['label']}", H + (24 if rotate else 0)) + note


# ── Heatmap ──────────────────────────────────────────────────────────────────

RAMP = ("#e8f1fd", "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#104281")


def heatmap(c: dict, measure: str, dim: str) -> str:
    vals = [v for row in c["values"] for v in row if v is not None]
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    flags = {(f["r"], f["c"]): f["dev"] for f in c["flags"]}
    show_text = len(c["cols"]) <= 12
    head = "".join(f"<th>{_e(col)}</th>" for col in c["cols"])
    rows = []
    for i, name in enumerate(c["rows"]):
        cells = []
        for j, v in enumerate(c["values"][i]):
            if v is None:
                cells.append('<td class="empty" data-tip="No data"></td>')
                continue
            step = int(round((v - lo) / ((hi - lo) or 1) * (len(RAMP) - 1)))
            dev = flags.get((i, j))
            tip = f"{name} · {c['cols'][j]}: {num(v)}" + (f" · {pct(dev)} vs expected" if dev is not None else "")
            ink = "#ffffff" if step >= 7 else "#0b0b0b"
            cls = ' class="flag up"' if dev is not None and dev > 0 else ' class="flag down"' if dev is not None else ""
            cells.append(f'<td{cls} style="background:{RAMP[step]};color:{ink}" data-tip="{_e(tip)}">{_e(num(v)) if show_text else ""}</td>')
        total = sum(v for v in c["values"][i] if v is not None)
        rows.append(f'<tr><th class="rowh">{_e(name)}</th>{"".join(cells)}<td class="tot">{_e(num(total))}</td></tr>')
    scale = "".join(f'<i style="background:{col}"></i>' for col in RAMP)
    legend = (f'<div class="legend"><span class="ramp">{_e(num(lo))} {scale} {_e(num(hi))}</span>'
              f'<span><i class="sw flagsw"></i>Far from expected (marked ▲ high, ▼ low)</span></div>')
    return (legend + f'<div class="hmwrap"><table class="heat{"" if show_text else " dense"}"><thead><tr><th class="rowh">{_e(dim)}</th>{head}<th class="tot">Total</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


# ── Small multiples ──────────────────────────────────────────────────────────

def multiples(c: dict, measure: str) -> str:
    panels = c["panels"]
    vals = [v for pn in panels for v in pn["values"] if v is not None]
    lo, hi = (min(vals + [0]), max(vals)) if vals else (0, 1)
    n = len(c["labels"])
    cards = []
    for pn in panels:
        pts = [(i / max(n - 1, 1) * 200, 58 - (v - lo) / ((hi - lo) or 1) * 52 if v is not None else None) for i, v in enumerate(pn["values"])]
        last = next(((x, yv) for x, yv in reversed(pts) if yv is not None), None)
        ch = pn["change"]
        cls = "up" if ch is not None and ch > 0 else "down" if ch is not None and ch < 0 else ""
        arrow = "▲" if cls == "up" else "▼" if cls == "down" else ""
        tip = f"{pn['label']}: last {num(pn['last'] or 0)}" + (f", {pct(ch)} from {c['labels'][0]}" if ch is not None else "")
        dot = f'<circle class="dot s1" cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="3"/>' if last else ""
        area = _path(pts)
        cards.append(f'<div class="mini" data-tip="{_e(tip)}"><div class="mhead"><b>{_e(pn["label"])}</b>'
                     f'<span class="mch {cls}">{arrow} {_e(pct(ch) if ch is not None else "")}</span></div>'
                     f'<div class="mval">{_e(num(pn["last"] or 0))}</div>'
                     f'<svg viewBox="-4 0 208 62" preserveAspectRatio="none"><line class="grid" x1="0" x2="200" y1="58" y2="58"/>'
                     f'<path class="line s1 thin" d="{area}"/>{dot}</svg></div>')
    note = f'<p class="axisnote">Every panel shares one scale ({_e(num(lo))} to {_e(num(hi))}), {_e(c["labels"][0])} to {_e(c["labels"][-1])}, so sizes compare fairly.</p>'
    return f'<div class="minis">{"".join(cards)}</div>' + note


# ── Relationship (scatter) ───────────────────────────────────────────────────

def scatter(c: dict) -> str:
    pts = c["points"]
    xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
    y = _Y(min(ys), max(ys))
    xt = nice_ticks(min(xs), max(xs))
    x0, x1 = xt[0], xt[-1]

    def X(v):
        return L + (v - x0) / ((x1 - x0) or 1) * (W - L - R)

    parts = [_grid(y)]
    parts += [f'<text class="tick" x="{X(t):.1f}" y="{H - B + 20}" text-anchor="middle">{_e(num(t))}</text>' for t in xt]
    parts.append(f'<text class="axislab" x="{W - R}" y="{H - 6}" text-anchor="end">{_e(c["x"])} →</text>')
    parts.append(f'<text class="axislab" x="{L}" y="{T - 12}" text-anchor="start">↑ {_e(c["y"])}</text>')
    for p in pts:
        tip = f"{p['name'] + ': ' if p['name'] else ''}{c['x']} {num(p['x'])}, {c['y']} {num(p['y'])}"
        parts.append(f'<circle class="pt{" flagged" if p["flag"] else ""}" cx="{X(p["x"]):.1f}" cy="{y(p["y"]):.1f}" r="{5 if p["flag"] else 4}" data-tip="{_e(tip)}"/>')
    a, b = c["slope"], c["intercept"]
    parts.append(f'<line class="line fit" x1="{X(x0):.1f}" x2="{X(x1):.1f}" y1="{y(min(max(a * x0 + b, y.lo), y.hi)):.1f}" y2="{y(min(max(a * x1 + b, y.lo), y.hi)):.1f}"/>')
    placed = []
    for p in pts:
        if p["flag"]:
            anchor = "end" if X(p["x"]) > W - 140 else "start"
            dx = -8 if anchor == "end" else 8
            lx, ly = X(p["x"]) + dx, y(p["y"]) - 8
            if any(abs(lx - qx) < 90 and abs(ly - qy) < 16 for qx, qy in placed):
                continue  # the hover tip still names it
            placed.append((lx, ly))
            parts.append(f'<text class="ann" x="{X(p["x"]) + dx:.1f}" y="{y(p["y"]) - 8:.1f}" text-anchor="{anchor}">{_e(p["name"] or num(p["y"]))}</text>')
    legend = _legend([("s1", "Each row"), ("s2", "Furthest from the fit"), ("fit", f"Fit line (r {c['r']:.2f})")])
    return legend + _svg("".join(parts), f"{c['y']} against {c['x']}")


# ── Distribution ─────────────────────────────────────────────────────────────

def distribution(c: dict, measure: str) -> str:
    counts, edges = c["counts"], c["edges"]
    y = _Y(0, max(counts) or 1)
    x0, x1 = edges[0], edges[-1]

    def X(v):
        return L + (v - x0) / ((x1 - x0) or 1) * (W - L - R)

    parts = [_grid(y)]
    parts.append(f'<rect class="iqr" x="{X(c["q1"]):.1f}" y="{T}" width="{max(X(c["q3"]) - X(c["q1"]), 1):.1f}" height="{H - B - T}"/>')
    for i, n in enumerate(counts):
        xa, xb = X(edges[i]) + 1, X(edges[i + 1]) - 1
        cls = "s2" if edges[i] >= c["fence"] else "s1"
        tip = f"{num(edges[i])} to {num(edges[i + 1])}: {n:,} rows"
        parts.append(f'<rect class="hbar {cls}" x="{xa:.1f}" y="{y(n):.1f}" width="{max(xb - xa, 1):.1f}" height="{max(y(0) - y(n), 0):.1f}" rx="2" data-tip="{_e(tip)}"/>')
    for key, cls, word in (("median", "medl", "Median"), ("mean", "meanl", "Mean")):
        xv = X(c[key])
        parts.append(f'<line class="{cls}" x1="{xv:.1f}" x2="{xv:.1f}" y1="{T}" y2="{H - B}"/>'
                     f'<text class="ann" x="{xv + 5:.1f}" y="{T + (12 if key == "median" else 28)}">{word} {_e(num(c[key]))}</text>')
    ticks = nice_ticks(x0, x1)
    parts += [f'<text class="tick" x="{X(t):.1f}" y="{H - B + 20}" text-anchor="middle">{_e(num(t))}</text>' for t in ticks if x0 <= t <= x1]
    legend = _legend([("s1", "Rows per range"), ("s2", "Unusually high"), ("iqr", "Middle half of rows")])
    return legend + _svg("".join(parts), f"Distribution of {measure}")


def draw(a) -> str:
    c = a.chart
    t = c["type"]
    if t == "explore":
        return explorer.render(a)
    if t == "trend":
        return trend(c, a.measure)
    if t == "pareto":
        return pareto(c, a.measure, a.dim)
    if t == "bridge":
        return bridge(c, a.measure)
    if t == "heatmap":
        return heatmap(c, a.measure, a.dim)
    if t == "multiples":
        return multiples(c, a.measure)
    if t == "scatter":
        return scatter(c)
    return distribution(c, a.measure)
