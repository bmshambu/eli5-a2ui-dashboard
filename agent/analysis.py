"""The numbers behind every chart and insight, computed here in Python (never by the model).

analyze() takes one tidy tab, a chart style and the column choices, and returns:
  - facts: short sentences, each with exact figures (trend, forecast, drivers, concentration,
    anomalies, relationships, data quality). The model only writes the story around these, and
    llm.grounded() drops any claim whose numbers are not in them.
  - kpis: the four tiles at the top of the page.
  - chart: the series the chosen style draws (charts.py).
  - table: the aggregated rows behind the chart (the "View data" table).
This is the part Excel does not do: forecast bands, 80/20 cuts, change bridges, anomaly flags
against an expected level, mix shift and correlation, all in one pass.
"""
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .sheets import Profile, Sheet, is_rate, label, profile

# style id: (name, Material icon, what it shows)
STYLES = {
    "auto": ("Auto pick", "auto_awesome", "I pick the view that tells the clearest story"),
    "explore": ("Interactive explorer", "dashboard", "Power BI-style: bubble chart and heatmap with slicers, click-to-filter and a sortable table"),
    "trend": ("Trend + forecast", "show_chart", "Trend line, rolling average and a 3-period forecast band, with peaks and jumps marked"),
    "pareto": ("Ranked drivers (80/20)", "bar_chart", "Ranked bars with running share; the few items that make 80% stand out"),
    "bridge": ("Change bridge", "waterfall_chart", "Waterfall from the first to the last period: which categories drove the change"),
    "heatmap": ("Heatmap + anomalies", "grid_on", "Category by period grid, with cells far from their expected level flagged"),
    "multiples": ("Small multiples", "view_module", "One mini trend per category on a shared scale, each with its change"),
    "scatter": ("Relationship", "scatter_plot", "Two numbers against each other: fit line, correlation and outliers labelled"),
    "distribution": ("Distribution", "equalizer", "Histogram with median, spread and outliers flagged"),
}
MAX_CATS = 12  # categories drawn before the rest fold into "Other"
FORECAST = 3


# ── Formatting (facts and the page use the same strings) ─────────────────────

def num(v: float) -> str:
    """1234567 -> '1.23M', 15320 -> '15.3K', 912.456 -> '912.46', 12 -> '12'."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "n/a"
    a = abs(v)
    for floor, size, suffix in ((1e9, 1e9, "B"), (1e6, 1e6, "M"), (1e4, 1e3, "K")):
        if a >= floor:
            digits = 2 if a < size * 10 else 1
            return f"{v / size:.{digits}f}".rstrip("0").rstrip(".") + suffix
    if a >= 100 or float(v).is_integer():
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def pct(v: float, signed: bool = True) -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


@dataclass
class Fact:
    id: str
    text: str
    kind: str  # summary, trend, forecast, driver, concentration, anomaly, relationship, distribution, quality
    weight: float = 1.0  # how headline-worthy, for the no-model fallback


@dataclass
class Analysis:
    sheet: str
    style: str  # the style drawn (auto resolved)
    asked: str  # the style asked for
    measure: str
    dim: str | None
    time: str | None
    agg: str  # "sum" or "mean"
    prof: Profile
    facts: list[Fact] = field(default_factory=list)
    kpis: list[dict] = field(default_factory=list)
    chart: dict = field(default_factory=dict)
    table: dict = field(default_factory=dict)  # {"columns": [...], "rows": [[...]]}
    notes: list[str] = field(default_factory=list)
    dft: object = None  # the rows the time-based views use (a partial last period left out)
    filters: dict | None = None  # {"dims": {column: [values]}, "from": iso date, "to": iso date}
    base: object = None  # all rows, unfiltered (the explorer's slicers can widen the view again)

    def fact_lines(self) -> str:
        return "\n".join(f"{f.id} [{f.kind}] {f.text}" for f in self.facts)

    def meta(self) -> dict:
        meta = {"sheet": self.sheet, "style": self.style, "measure": self.measure, "dim": self.dim}
        if self.filters:
            meta["filters"] = self.filters
        return meta


# ── Which styles fit a tab ───────────────────────────────────────────────────

def available(p: Profile) -> dict[str, bool]:
    t, d, m = bool(p.time), bool(p.dimensions), len(p.measures)
    return {"auto": m > 0, "explore": m > 0 and (d or t), "trend": t and m > 0, "pareto": d and m > 0, "bridge": t and d and m > 0,
            "heatmap": d and m > 0 and (t or len(p.dimensions) > 1), "multiples": t and d and m > 0,
            "scatter": m > 1, "distribution": m > 0}


def resolve(style: str, p: Profile) -> str:
    ok = available(p)
    if style in ok and ok[style] and style != "auto":
        return style
    for s in ("explore", "trend", "pareto", "scatter", "distribution"):
        if ok[s]:
            return s
    return "distribution"


# ── Aggregation ──────────────────────────────────────────────────────────────

_FREQ = {"month": "M", "quarter": "Q", "year": "Y"}


def _period(df: pd.DataFrame, time: str, grain: str) -> pd.Series:
    t = pd.to_datetime(df[time])
    return t.dt.normalize() if grain == "day" else t.dt.to_period(_FREQ[grain]).dt.start_time


def over_time(df, time, measure, grain, agg) -> pd.Series:
    d = df[[time, measure]].dropna()
    return d.groupby(_period(d, time, grain))[measure].agg(agg).sort_index()


def by_cat(df, dim, measure, agg) -> pd.Series:
    d = df[[dim, measure]].dropna()
    return d.groupby(d[dim].astype(str))[measure].agg(agg).sort_values(ascending=False)


def _fold(s: pd.Series, n: int = MAX_CATS) -> pd.Series:
    """Top n-1 items plus 'Other' when there are more than n."""
    if len(s) <= n:
        return s
    top = s.iloc[: n - 1]
    return pd.concat([top, pd.Series({f"Other ({len(s) - n + 1})": s.iloc[n - 1:].sum()})])


def _fit(y: np.ndarray) -> tuple[float, float, float, float]:
    """Least-squares line over 0..n-1: slope, intercept, r squared, residual std."""
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    ss_res = float(((y - fitted) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1e-12
    resid_sd = math.sqrt(ss_res / max(len(y) - 2, 1))
    return float(slope), float(intercept), max(0.0, 1 - ss_res / ss_tot), resid_sd


def _change(a: float, b: float) -> float | None:
    return (b - a) / abs(a) * 100 if a else None


def _robust_z(values: pd.Series) -> pd.Series:
    med = values.median()
    mad = (values - med).abs().median()
    if not mad:
        sd = values.std()
        return (values - values.mean()) / sd if sd else values * 0
    return 0.6745 * (values - med) / mad


# ── The analysis ─────────────────────────────────────────────────────────────

class _Facts:
    def __init__(self):
        self.items: list[Fact] = []

    def add(self, kind: str, text: str, weight: float = 1.0) -> str:
        fid = f"F{len(self.items) + 1}"
        self.items.append(Fact(fid, text, kind, weight))
        return fid


def apply_filters(df: pd.DataFrame, p: Profile, filters: dict | None) -> tuple[pd.DataFrame, dict | None, str | None]:
    """Rows matching the explorer's slicers and cross-filter, the filters actually applied, and
    how to describe them. Unknown columns and empty lists are ignored."""
    if not filters:
        return df, None, None
    out, used, words = df, {}, []
    for col, vals in (filters.get("dims") or {}).items():
        if col in df.columns and isinstance(vals, list) and vals:
            vals = [str(v) for v in vals][:200]
            out = out[out[col].astype(str).isin(vals)]
            used.setdefault("dims", {})[col] = vals
            words.append(f"{col} = {', '.join(vals[:4])}{f' and {len(vals) - 4} more' if len(vals) > 4 else ''}")
    if p.time:
        per = _period(out, p.time, p.grain)
        lo, hi = pd.to_datetime(filters.get("from"), errors="coerce"), pd.to_datetime(filters.get("to"), errors="coerce")
        if pd.notna(lo) or pd.notna(hi):
            keep = per.notna()
            if pd.notna(lo):
                keep &= per >= lo
                used["from"] = lo.strftime("%Y-%m-%d")
            if pd.notna(hi):
                keep &= per <= hi
                used["to"] = hi.strftime("%Y-%m-%d")
            out = out[keep]
            a, b = label(lo, p.grain) if pd.notna(lo) else "the start", label(hi, p.grain) if pd.notna(hi) else "the end"
            words.append(f"{p.time} {a}" if a == b else f"{p.time} {a} to {b}")
    if out.empty:
        raise ValueError("no rows match those filters")
    return out, used or None, "; ".join(words) or None


def analyze(sheet: Sheet, style: str = "auto", measure: str | None = None, dim: str | None = None,
            filters: dict | None = None) -> Analysis:
    p = profile(sheet)
    if not p.measures:
        raise ValueError(f"the {sheet.name} tab has no number columns to chart")
    df, used, described = apply_filters(sheet.df, p, filters)
    measure = measure if measure in p.measures else p.measures[0]
    dim = dim if dim in p.dimensions else (p.dimensions[0] if p.dimensions else None)
    drawn = resolve(style, p)
    agg = "mean" if is_rate(measure) else "sum"
    a = Analysis(sheet=sheet.name, style=drawn, asked=style, measure=measure, dim=dim, time=p.time, agg=agg, prof=p,
                 notes=list(sheet.notes))
    if style not in ("auto", drawn) and style in STYLES:
        a.notes.append(f"{STYLES[style][0]} needs {_needs(style)}, which this tab doesn't have, so I drew {STYLES[drawn][0]} instead.")
    a.filters, a.base = used, sheet.df
    f = _Facts()
    if described:
        f.add("summary", f"This view is filtered to {described} ({len(df):,} of {len(sheet.df):,} rows).", 0.4)
        a.notes.append(f"Filtered to {described}.")
    word = "average" if agg == "mean" else "total"
    vals = df[measure].dropna()

    # Summary
    overall = float(vals.mean() if agg == "mean" else vals.sum())
    f.add("summary", f"{measure} {word} across {len(vals):,} rows is {num(overall)}.", 0.5)
    a.kpis.append({"label": f"{measure} ({word})", "value": num(overall), "note": f"{len(vals):,} rows"})

    dft = df
    partial = _partial_period(df, p) if p.time else None
    if partial is not None:
        cut, last_day = partial
        dft = df[pd.to_datetime(df[p.time]) < cut]
        f.add("quality", f"{label(cut, p.grain)} is only partly in the data (it stops on {last_day:%d %b %Y}), so trends, changes and the "
                         f"forecast stop at {label(cut - _STEP[p.grain], p.grain)} to avoid a false drop.", 2.5)
    a.dft = dft
    series = over_time(dft, p.time, measure, p.grain, agg) if p.time else None
    cats = by_cat(df, dim, measure, agg) if dim else None

    if series is not None and len(series) >= 2:
        _time_facts(f, a, series, p, measure, dft)
    if cats is not None and len(cats) >= 2:
        _cat_facts(f, a, cats, dim, measure, agg, df)
    if series is not None and cats is not None and len(series) >= 2:
        _mix_facts(f, a, dft, p, measure, dim, agg)
    _anomaly_facts(f, a, df, p, measure, dim)
    _relationship_facts(f, a, df, p, measure)
    _spread_fact(f, vals, measure)
    _quality_facts(f, a, df, measure)

    builder = {"trend": _trend, "pareto": _pareto, "bridge": _bridge, "heatmap": _heatmap, "explore": _explore,
               "multiples": _multiples, "scatter": _scatter, "distribution": _distribution}[drawn]
    builder(a, dft if drawn in ("trend", "bridge", "heatmap", "multiples") and p.time else df, p, f)
    # Facts that explain the chart on screen come first in the no-model fallback.
    for x in f.items:
        if x.kind in _STYLE_KINDS.get(drawn, ()):
            x.weight += 1.5
    a.facts = f.items
    if len(a.kpis) < 3:
        a.kpis.append({"label": f"Median {measure} per row", "value": num(float(vals.median())),
                       "note": f"middle half {num(float(vals.quantile(.25)))} to {num(float(vals.quantile(.75)))}"})
    a.kpis = a.kpis[:3] + [{"label": "Signals flagged", "value": str(sum(x.kind in ("anomaly", "quality") and x.weight >= 1 for x in a.facts)),
                            "note": "anomalies and data checks", "tone": "flag"}]
    return a


_STEP = {"day": pd.DateOffset(days=1), "month": pd.DateOffset(months=1), "quarter": pd.DateOffset(months=3), "year": pd.DateOffset(years=1)}
_STYLE_KINDS = {"explore": ("driver", "concentration", "anomaly"), "trend": ("trend", "forecast"), "pareto": ("concentration", "driver"), "bridge": ("driver",),
                "heatmap": ("anomaly",), "multiples": ("driver",), "scatter": ("relationship",), "distribution": ("distribution",)}


def _partial_period(df: pd.DataFrame, p: Profile) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(start of the last period, last date) when daily data stops well before that period ends.
    A part-month looks like a collapse in every trend; Excel charts it as one."""
    if p.grain not in ("month", "quarter", "year"):
        return None
    t = pd.to_datetime(df[p.time]).dropna()
    last = t.max()
    if (t.dt.day == 1).all():
        return None  # one row per period, not daily data
    start = _period(pd.DataFrame({"t": [last]}), "t", p.grain).iloc[0]
    end = start + _STEP[p.grain] - pd.Timedelta(days=1)
    if (end - last).days > 5 and t[t < start].nunique() >= 2:
        return start, last
    return None


def _needs(style: str) -> str:
    return {"trend": "a date or period column", "pareto": "a category column", "bridge": "a date and a category column",
            "heatmap": "a category column and a date (or a second category)", "multiples": "a date and a category column",
            "scatter": "two number columns", "distribution": "a number column",
            "explore": "a category or a date column"}.get(style, "other columns")


def _time_facts(f: _Facts, a: Analysis, s: pd.Series, p: Profile, measure: str, df: pd.DataFrame) -> None:
    g = p.grain
    labels = [label(t, g) for t in s.index]
    first, last = float(s.iloc[0]), float(s.iloc[-1])
    ch = _change(first, last)
    if ch is not None:
        f.add("trend", f"{measure} went from {num(first)} in {labels[0]} to {num(last)} in {labels[-1]} ({pct(ch)}).", 3)
        a.kpis.append({"label": f"Change {labels[0]} to {labels[-1]}", "value": pct(ch), "note": f"{num(first)} to {num(last)}",
                       "tone": "up" if ch > 0 else "down" if ch < 0 else None})
    imax, imin = int(np.argmax(s.values)), int(np.argmin(s.values))
    f.add("trend", f"The peak was {labels[imax]} at {num(s.iloc[imax])}; the low point was {labels[imin]} at {num(s.iloc[imin])}.", 2)
    if len(s) >= 3:
        d = s.diff().iloc[1:]
        j, k = int(np.argmax(d.values)) + 1, int(np.argmin(d.values)) + 1
        if d.max() > 0:
            f.add("trend", f"The biggest rise was from {labels[j - 1]} to {labels[j]}: +{num(d.iloc[j - 1])} ({pct(_change(s.iloc[j - 1], s.iloc[j]) or 0)}).", 1.5)
        if d.min() < 0:
            f.add("trend", f"The biggest fall was from {labels[k - 1]} to {labels[k]}: {num(d.iloc[k - 1])} ({pct(_change(s.iloc[k - 1], s.iloc[k]) or 0)}).", 1.5)
    if len(s) >= 4:
        slope, intercept, r2, sd = _fit(s.values.astype(float))
        mean = float(s.mean()) or 1e-12
        per = slope / abs(mean) * 100
        direction = "flat" if abs(per) < 0.5 else "rising" if slope > 0 else "falling"
        f.add("trend", f"The underlying trend is {direction}: about {'+' if slope >= 0 else ''}{num(slope)} per {g} "
                       f"({pct(per)} of the average {g}), and the trend line explains {r2 * 100:.0f}% of the ups and downs.", 2.5)
        n = len(s)
        fc = [slope * (n + i) + intercept for i in range(FORECAST)]
        lo, hi = fc[-1] - 1.96 * sd, fc[-1] + 1.96 * sd
        f.add("forecast", f"If the trend holds, {measure} would be about {num(fc[-1])} {FORECAST} {g}s after {labels[-1]} "
                          f"(likely range {num(lo)} to {num(hi)}). This is a straight-line projection, not a model of seasons or events.", 2)
        cv = float(s.pct_change().iloc[1:].replace([np.inf, -np.inf], np.nan).dropna().std() * 100) if n > 2 else None
        if cv is not None and math.isfinite(cv):
            f.add("trend", f"{g.capitalize()}-to-{g} swings are typically {cv:.1f}%, so moves smaller than that are normal noise.", 1)
    if len(s) >= 6:
        recent, before = float(s.iloc[-3:].mean()), float(s.iloc[-6:-3].mean())
        m = _change(before, recent)
        if m is not None:
            f.add("trend", f"Momentum: the last 3 {g}s averaged {num(recent)}, {pct(m)} against the 3 before.", 2)
    if g == "month" and len(s) >= 20:
        by_m = s.groupby(s.index.month).mean()
        hi_m, lo_m = int(by_m.idxmax()), int(by_m.idxmin())
        if by_m.min() > 0:
            ratio = by_m.max() / by_m.min()
            if ratio > 1.15:
                f.add("trend", f"There is a seasonal pattern: {pd.Timestamp(2000, hi_m, 1):%B} is usually the strongest month and "
                               f"{pd.Timestamp(2000, lo_m, 1):%B} the weakest ({ratio:.1f}x apart on average).", 2)


def _cat_facts(f: _Facts, a: Analysis, c: pd.Series, dim: str, measure: str, agg: str, df: pd.DataFrame | None = None) -> None:
    if agg == "sum" and df is not None:
        per_row = by_cat(df, dim, measure, "mean")
        if len(per_row) >= 2 and per_row.iloc[-1] > 0 and per_row.iloc[0] / per_row.iloc[-1] >= 1.5:
            f.add("driver", f"Per row, {per_row.index[0]} averages {num(per_row.iloc[0])} of {measure} against {num(per_row.iloc[-1])} "
                            f"for {per_row.index[-1]} ({per_row.iloc[0] / per_row.iloc[-1]:.1f}x).", 1.5)
    if agg == "mean":
        f.add("driver", f"By {dim}, {c.index[0]} has the highest average {measure} ({num(c.iloc[0])}) and {c.index[-1]} the lowest ({num(c.iloc[-1])}).", 2)
        a.kpis.append({"label": f"Highest {dim}", "value": str(c.index[0]), "note": num(c.iloc[0])})
        return
    pos = c[c > 0]
    total = float(pos.sum())
    if total <= 0:
        return
    share = pos / total * 100
    cum = share.cumsum()
    k80 = int((cum < 80).sum()) + 1
    f.add("driver", f"{c.index[0]} is the largest {dim} with {num(c.iloc[0])}, {pct(share.iloc[0], False)} of the total.", 3)
    a.kpis.append({"label": f"Top {dim}", "value": str(c.index[0]), "note": f"{pct(share.iloc[0], False)} of total"})
    if len(pos) >= 4:
        f.add("concentration", f"{k80} of {len(pos)} {dim} values ({k80 / len(pos) * 100:.0f}%) make up 80% of {measure}; "
                               f"the top 3 alone are {pct(float(share.iloc[:3].sum()), False)}.", 2.5)
        hhi = float(((pos / total) ** 2).sum())
        level = "highly concentrated" if hhi > 0.25 else "moderately concentrated" if hhi > 0.15 else "spread out"
        f.add("concentration", f"Concentration by {dim} is {level} (HHI {hhi:.2f}; 1.00 would be a single item).", 1.5)
        half = len(pos) // 2
        f.add("concentration", f"The smallest {half} {dim} values together are only {pct(float(share.iloc[-half:].sum()), False)} of the total.", 1)


def _mix_facts(f: _Facts, a: Analysis, df: pd.DataFrame, p: Profile, measure: str, dim: str, agg: str) -> None:
    d = df[[p.time, dim, measure]].dropna()
    d = d.assign(_p=_period(d, p.time, p.grain), **{dim: d[dim].astype(str)})
    pv = d.pivot_table(index="_p", columns=dim, values=measure, aggfunc=agg).sort_index()
    if len(pv) < 2:
        return
    g = p.grain
    l0, l1 = label(pv.index[0], g), label(pv.index[-1], g)
    first, last = pv.iloc[0].fillna(0), pv.iloc[-1].fillna(0)
    delta = (last - first).sort_values()
    pcts = {k: _change(first[k], last[k]) for k in pv.columns if first[k] > 0}
    big = {k: v for k, v in pcts.items() if v is not None and first[k] >= 0.05 * first[first > 0].sum() / max(len(first), 1)}
    if big:
        up = max(big, key=big.get)
        down = min(big, key=big.get)
        f.add("driver", f"Fastest-growing {dim} from {l0} to {l1}: {up} ({pct(big[up])}).", 2.5)
        if big[down] < 0:
            f.add("driver", f"Biggest decline: {down} ({pct(big[down])}, {num(first[down])} to {num(last[down])}).", 2.5)
    if agg == "sum":
        total_change = float(last.sum() - first.sum())
        if total_change:
            lead = delta.index[-1] if total_change > 0 else delta.index[0]
            share = float(delta[lead]) / total_change * 100
            f.add("driver", f"{lead} drove {share:.0f}% of the {'+' if total_change > 0 else ''}{num(total_change)} change in total {measure} "
                            f"between {l0} and {l1}.", 3)
            against = [k for k in delta.index if delta[k] * total_change < 0 and abs(delta[k]) > 0.02 * abs(total_change)]
            if against:
                f.add("driver", f"Moving against the overall direction: {', '.join(against[:4])}.", 1.5)
            s0, s1 = first / (first.sum() or 1) * 100, last / (last.sum() or 1) * 100
            shift = (s1 - s0).sort_values()
            if abs(shift.iloc[-1]) >= 2:
                f.add("driver", f"Mix shift: {shift.index[-1]} gained {shift.iloc[-1]:.1f} points of share ({s0[shift.index[-1]]:.1f}% to "
                                f"{s1[shift.index[-1]]:.1f}%), while {shift.index[0]} lost {abs(shift.iloc[0]):.1f} points.", 2)


def _anomaly_facts(f: _Facts, a: Analysis, df: pd.DataFrame, p: Profile, measure: str, dim: str | None) -> None:
    """Rows far from what is typical for their own category (robust z within the group), so a big
    region's normal day is not flagged just for being bigger than a small region's."""
    # Peers = rows sharing every category (Region and Product together) when groups are big enough.
    keys = [k for k in p.dimensions if df[k].nunique() <= 30][:3]
    while keys and len(df) / max(df.groupby([df[k].astype(str) for k in keys]).ngroups, 1) < 8:
        keys.pop()
    d = df[[c for c in dict.fromkeys([measure, *keys, p.time]) if c]].dropna(subset=[measure])
    if len(d) < 8:
        return
    z = d.groupby([d[k].astype(str) for k in keys])[measure].transform(_robust_z) if keys else _robust_z(d[measure])
    med = d.groupby([d[k].astype(str) for k in keys])[measure].transform("median") if keys else d[measure].median()
    ratio = (d[measure] / med).where(med != 0)
    out = z[(z.abs() > 5) & ((ratio >= 2) | (ratio <= 0.4))].abs().sort_values(ascending=False)
    if out.empty:
        f.add("anomaly", f"No single row of {measure} is far out of line with its peers (none beyond 5 robust standard deviations).", 0.5)
        return
    where = []
    for i in out.index[:3]:
        bits = [num(d.at[i, measure])] + [str(d.at[i, k]) for k in keys if pd.notna(d.at[i, k])]
        if p.time and pd.notna(d.at[i, p.time]):
            bits.append(label(pd.Timestamp(d.at[i, p.time]), "day"))
        peers = d
        for k in keys:
            peers = peers[peers[k].astype(str) == str(d.at[i, k])]
        typical = peers[measure].median()
        where.append(f"{' · '.join(bits)} (typical {num(typical)})")
    n = len(out)
    f.add("anomaly", f"{n} row{'s' if n > 1 else ''} of {measure} {'are' if n > 1 else 'is'} far out of line with {'their' if n > 1 else 'its'} "
                     f"peers{'' if n <= 3 else ', the largest'}: {'; '.join(where)}. Worth checking for errors or one-offs.", 2.5)


def _relationship_facts(f: _Facts, a: Analysis, df: pd.DataFrame, p: Profile, measure: str) -> None:
    best = None
    for other in p.measures:
        if other == measure:
            continue
        pair = df[[measure, other]].dropna()
        if len(pair) < 10 or pair[other].nunique() < 3:
            continue
        r = float(pair[measure].corr(pair[other]))
        if math.isfinite(r) and (best is None or abs(r) > abs(best[1])):
            best = (other, r, len(pair))
    if best and abs(best[1]) >= 0.4:
        other, r, n = best
        strength = "strongly" if abs(r) >= 0.7 else "moderately"
        f.add("relationship", f"{measure} moves {strength} {'with' if r > 0 else 'against'} {other} (correlation {r:.2f} over {n:,} rows); "
                              f"related, not proof that one causes the other.", 2)
    a.chart.setdefault("partner", best[0] if best else next((m for m in p.measures if m != measure), None))


def _spread_fact(f: _Facts, v: pd.Series, measure: str) -> None:
    if len(v) < 8:
        return
    q1, med, q3, mean = float(v.quantile(0.25)), float(v.median()), float(v.quantile(0.75)), float(v.mean())
    skew = ("pulled up by a few large values" if mean > med * 1.15 else "pulled down by a few small values" if mean < med * 0.85
            else "close to it, so values are fairly balanced") if med > 0 else "shown for reference"
    f.add("distribution", f"Half the rows of {measure} sit between {num(q1)} and {num(q3)} (median {num(med)}); the mean of {num(mean)} is {skew}.", 1.2)


def _quality_facts(f: _Facts, a: Analysis, df: pd.DataFrame, measure: str) -> None:
    miss = int(df[measure].isna().sum())
    if miss:
        f.add("quality", f"{miss:,} rows ({miss / len(df) * 100:.1f}%) have no {measure} value and are left out.", 1.2)
    neg = int((df[measure] < 0).sum())
    if neg and not is_rate(measure):
        f.add("quality", f"{neg:,} rows have a negative {measure} (returns, credits or corrections?).", 1)
    for note in a.notes:
        f.add("quality", note, 0.3)


# ── Chart data per style ─────────────────────────────────────────────────────

def _labels(index, grain) -> list[str]:
    return [label(pd.Timestamp(t), grain) for t in index]


def _trend(a: Analysis, df, p: Profile, f: _Facts) -> None:
    s = over_time(df, p.time, a.measure, p.grain, a.agg)
    y = [float(v) for v in s.values]
    labels = _labels(s.index, p.grain)
    chart = {"type": "trend", "labels": labels, "values": y, "grain": p.grain}
    if len(y) >= 6:
        chart["rolling"] = [None, None] + [sum(y[i - 2:i + 1]) / 3 for i in range(2, len(y))]
    if len(y) >= 4:
        slope, intercept, _, sd = _fit(np.array(y))
        chart["fit"] = [slope * i + intercept for i in range(len(y))]
        n = len(y)
        nxt = s.index[-1]
        step = _STEP[p.grain]
        chart["forecast"] = [{"label": label(nxt + step * (i + 1), p.grain), "value": slope * (n + i) + intercept,
                              "lo": slope * (n + i) + intercept - 1.96 * sd * math.sqrt(1 + (i + 1) / n),
                              "hi": slope * (n + i) + intercept + 1.96 * sd * math.sqrt(1 + (i + 1) / n)} for i in range(FORECAST)]
    if len(y) >= 2:
        chart["peak"], chart["low"] = int(np.argmax(y)), int(np.argmin(y))
        if len(y) >= 3:
            d = np.diff(y)
            chart["jump"] = int(np.argmax(np.abs(d))) + 1
    a.chart.update(chart)
    a.table = {"columns": [p.time, a.measure], "rows": [[l, num(v)] for l, v in zip(labels, y)]}


def _pareto(a: Analysis, df, p: Profile, f: _Facts) -> None:
    c = by_cat(df, a.dim, a.measure, a.agg)
    shown = c.iloc[:20]
    total = float(c[c > 0].sum()) or 1.0
    cum = (c.clip(lower=0).cumsum() / total * 100).iloc[:20]
    items = [{"label": str(k), "value": float(v), "share": float(max(v, 0) / total * 100), "cum": float(cum[k])} for k, v in shown.items()]
    if len(c) > 20:
        items.append({"label": f"Other ({len(c) - 20})", "value": float(c.iloc[20:].sum()), "share": float(c.iloc[20:].clip(lower=0).sum() / total * 100), "cum": 100.0, "other": True})
    k80 = next((i for i, it in enumerate(items) if it["cum"] >= 80), len(items) - 1)
    a.chart.update({"type": "pareto", "items": items, "cut": k80, "mean": a.agg == "mean"})
    a.table = {"columns": [a.dim, a.measure, "Share", "Running share"],
               "rows": [[it["label"], num(it["value"]), pct(it["share"], False), pct(it["cum"], False)] for it in items]}


def _bridge(a: Analysis, df, p: Profile, f: _Facts) -> None:
    d = df[[p.time, a.dim, a.measure]].dropna()
    d = d.assign(_p=_period(d, p.time, p.grain), **{a.dim: d[a.dim].astype(str)})
    pv = d.pivot_table(index="_p", columns=a.dim, values=a.measure, aggfunc=a.agg).sort_index().fillna(0)
    first, last = pv.iloc[0], pv.iloc[-1]
    start, end = float(first.sum() if a.agg == "sum" else d[d["_p"] == pv.index[0]][a.measure].mean()), \
        float(last.sum() if a.agg == "sum" else d[d["_p"] == pv.index[-1]][a.measure].mean())
    delta = (last - first)
    order = delta.abs().sort_values(ascending=False)
    steps = [{"label": str(k), "value": float(delta[k])} for k in order.index[:10]]
    if len(order) > 10:
        rest = float(delta[order.index[10:]].sum())
        steps.append({"label": f"Other ({len(order) - 10})", "value": rest})
    if a.agg == "mean":  # averages don't add up across categories: show the start and end only
        steps = []
        a.notes.append(f"{a.measure} is averaged, so the bridge shows the start and end levels, not per-category steps.")
    l0, l1 = label(pv.index[0], p.grain), label(pv.index[-1], p.grain)
    a.chart.update({"type": "bridge", "start": {"label": l0, "value": start}, "end": {"label": l1, "value": end}, "steps": steps})
    a.table = {"columns": ["Step", a.measure], "rows": [[l0, num(start)], *[[s["label"], ("+" if s["value"] > 0 else "") + num(s["value"])] for s in steps], [l1, num(end)]]}


def _heatmap(a: Analysis, df, p: Profile, f: _Facts) -> None:
    if p.time:
        col_key, col_name = "_p", p.time
        d = df[[p.time, a.dim, a.measure]].dropna()
        d = d.assign(_p=_period(d, p.time, p.grain))
    else:
        col_name = next(x for x in p.dimensions if x != a.dim)
        col_key = col_name
        d = df[[col_name, a.dim, a.measure]].dropna()
    d = d.assign(**{a.dim: d[a.dim].astype(str)})
    pv = d.pivot_table(index=a.dim, columns=col_key, values=a.measure, aggfunc=a.agg)
    pv = pv.loc[pv.sum(axis=1).sort_values(ascending=False).index[:MAX_CATS]]
    if p.time:
        pv = pv[sorted(pv.columns)[-24:]]
        cols = _labels(pv.columns, p.grain)
    else:
        pv = pv[pv.sum(axis=0).sort_values(ascending=False).index[:MAX_CATS]]
        cols = [str(c) for c in pv.columns]
    m = pv.to_numpy(dtype=float)
    # Expected level of a cell = its row level + its column level (two-way additive fit);
    # a cell far from that is unusual for that category at that time.
    filled = np.where(np.isnan(m), np.nanmean(m), m)
    grand = filled.mean()
    expected = filled.mean(axis=1, keepdims=True) + filled.mean(axis=0, keepdims=True) - grand
    resid = filled - expected
    sd = resid.std() or 1.0
    flags = []
    for i, j in zip(*np.where(np.abs(resid) > 2.2 * sd)):
        if not np.isnan(m[i, j]) and expected[i, j]:
            flags.append({"r": int(i), "c": int(j), "dev": float((m[i, j] - expected[i, j]) / abs(expected[i, j]) * 100)})
    flags.sort(key=lambda x: -abs(x["dev"]))
    flags = flags[:6]
    rows = [str(x) for x in pv.index]
    for fl in flags[:3]:
        f.add("anomaly", f"{rows[fl['r']]} in {cols[fl['c']]} was {num(m[fl['r'], fl['c']])}, {pct(fl['dev'])} against what its usual level "
                         f"and that {'period' if p.time else col_name} would predict ({num(expected[fl['r'], fl['c']])}).", 2.2)
    a.chart.update({"type": "heatmap", "rows": rows, "cols": cols, "values": [[None if np.isnan(v) else float(v) for v in r] for r in m],
                    "flags": flags, "col_name": col_name})
    a.table = {"columns": [a.dim, *cols], "rows": [[rows[i], *[num(v) if not np.isnan(v) else "" for v in m[i]]] for i in range(len(rows))]}


def _multiples(a: Analysis, df, p: Profile, f: _Facts) -> None:
    d = df[[p.time, a.dim, a.measure]].dropna()
    d = d.assign(_p=_period(d, p.time, p.grain), **{a.dim: d[a.dim].astype(str)})
    pv = d.pivot_table(index="_p", columns=a.dim, values=a.measure, aggfunc=a.agg).sort_index()
    order = pv.sum().sort_values(ascending=False).index[:MAX_CATS]
    labels = _labels(pv.index, p.grain)
    panels = []
    for k in order:
        col = pv[k]
        vals = [None if pd.isna(v) else float(v) for v in col.values]
        known = [v for v in vals if v is not None]
        ch = _change(known[0], known[-1]) if len(known) >= 2 else None
        panels.append({"label": str(k), "values": vals, "last": known[-1] if known else None, "change": ch})
    a.chart.update({"type": "multiples", "labels": labels, "panels": panels})
    a.table = {"columns": [a.dim, f"First ({labels[0]})", f"Last ({labels[-1]})", "Change"],
               "rows": [[pn["label"], num(next((v for v in pn["values"] if v is not None), 0)), num(pn["last"] or 0), pct(pn["change"]) if pn["change"] is not None else ""] for pn in panels]}


def _scatter(a: Analysis, df, p: Profile, f: _Facts) -> None:
    x = a.chart.get("partner") or next(m for m in p.measures if m != a.measure)
    cols = [x, a.measure] + ([a.dim] if a.dim else [])
    d = df[cols].dropna(subset=[x, a.measure])
    if len(d) > 600:
        d = d.sample(600, random_state=7)
    xs, ys = d[x].astype(float).to_numpy(), d[a.measure].astype(float).to_numpy()
    slope, intercept = np.polyfit(xs, ys, 1) if len(d) >= 3 and np.ptp(xs) else (0.0, float(ys.mean()))
    r = float(np.corrcoef(xs, ys)[0, 1]) if len(d) >= 3 and np.ptp(xs) and np.ptp(ys) else 0.0
    resid = ys - (slope * xs + intercept)
    top = np.argsort(-np.abs(resid))[:3] if len(d) >= 8 else []
    names = d[a.dim].astype(str).tolist() if a.dim else [""] * len(d)
    pts = [{"x": float(xs[i]), "y": float(ys[i]), "name": names[i], "flag": bool(i in set(top))} for i in range(len(d))]
    a.chart.update({"type": "scatter", "x": x, "y": a.measure, "points": pts, "slope": float(slope), "intercept": float(intercept), "r": r})
    if len(d) >= 8 and abs(r) >= 0.3:
        f.add("relationship", f"Each 1 more {x} goes with about {num(float(slope))} more {a.measure} on average (fit over {len(d):,} points, r {r:.2f}).", 1.5)
    a.table = {"columns": [a.dim or "Row", x, a.measure], "rows": [[pt["name"] or str(i + 1), num(pt["x"]), num(pt["y"])] for i, pt in enumerate(pts[:200])]}


def _distribution(a: Analysis, df, p: Profile, f: _Facts) -> None:
    v = df[a.measure].dropna().astype(float)
    q1, med, q3 = (float(v.quantile(q)) for q in (0.25, 0.5, 0.75))
    iqr = q3 - q1
    width = 2 * iqr / (len(v) ** (1 / 3)) if iqr else 0
    bins = int(min(30, max(6, math.ceil(np.ptp(v.to_numpy()) / width)))) if width else 10
    counts, edges = np.histogram(v, bins=bins)
    hi_fence = q3 + 1.5 * iqr
    lo_fence = q1 - 1.5 * iqr
    n_out = int(((v > hi_fence) | (v < lo_fence)).sum())
    mean = float(v.mean())
    if n_out:
        f.add("distribution", f"{n_out:,} rows fall outside the usual range ({num(lo_fence)} to {num(hi_fence)}).", 1.5)
    a.chart.update({"type": "distribution", "counts": [int(c) for c in counts], "edges": [float(e) for e in edges],
                    "median": med, "mean": mean, "q1": q1, "q3": q3, "fence": hi_fence, "outliers": n_out})
    a.table = {"columns": ["From", "To", "Rows"], "rows": [[num(edges[i]), num(edges[i + 1]), str(int(counts[i]))] for i in range(len(counts))]}


# ── Interactive explorer (Power BI-style) ────────────────────────────────────

MAX_CUBE_ROWS = 6000
MAX_ID_ROWS = 1500  # below this, each row can be its own bubble (e.g. one per customer)


def _id_column(df: pd.DataFrame, p: Profile) -> str | None:
    """A text column that names each row (Customer ID, Project), for one bubble per row."""
    if len(df) > MAX_ID_ROWS:
        return None
    for c in df.columns:
        if c in p.measures or c in p.dimensions or c == p.time or pd.api.types.is_numeric_dtype(df[c]):
            continue
        if df[c].notna().all() and df[c].is_unique:
            return c
    return None


def cube(base: pd.DataFrame, p: Profile) -> dict:
    """The tab pre-aggregated by its categories and periods, small enough to embed in the page.
    Rows are [category index..., period index or -1, row count, sum and count per measure]; the
    page filters and regroups them in the browser, so slicers and clicks need no round trip."""
    dims = [d for d in p.dimensions if base[d].nunique() <= 30][:3]
    ident = _id_column(base, p)
    keys = dims + ([ident] if ident else [])
    measures = p.measures[:6]
    d = base.copy()
    if p.time:
        d["_p"] = _period(d, p.time, p.grain)
        d = d[d["_p"].notna()]
    def size(ks):
        return d.groupby([d[k].astype(str) for k in ks] + ([d["_p"]] if p.time else []), dropna=False).ngroups if ks or p.time else 1
    while keys and size(keys) > MAX_CUBE_ROWS:
        keys.pop()
    for k in keys:
        d[k] = d[k].fillna("(blank)").astype(str)
    values = {k: sorted(d[k].unique(), key=lambda v: -float(d.loc[d[k] == v, measures[0]].abs().sum())) for k in keys}
    index = {k: {v: i for i, v in enumerate(values[k])} for k in keys}
    periods = sorted(d["_p"].unique()) if p.time else []
    pidx = {t: i for i, t in enumerate(periods)}
    by = keys + (["_p"] if p.time else [])
    agg = {"_n": (measures[0], "size")}
    for i, m in enumerate(measures):
        agg[f"_s{i}"] = (m, "sum")
        agg[f"_c{i}"] = (m, "count")
    g = d.groupby(by, dropna=False).agg(**agg).reset_index() if by else d.agg(**agg).T.reset_index(drop=True)
    rows = []
    for rec in g.to_dict("records"):
        row = [index[k][rec[k]] for k in keys] + [pidx[rec["_p"]] if p.time else -1, int(rec["_n"])]
        for i in range(len(measures)):
            row += [round(float(rec[f"_s{i}"]), 4), int(rec[f"_c{i}"])]
        rows.append(row)
    return {"dims": [{"name": k, "values": values[k], "id": k == ident} for k in keys],
            "time": {"name": p.time, "grain": p.grain, "labels": [label(t, p.grain) for t in periods],
                     "iso": [t.strftime("%Y-%m-%d") for t in periods]} if p.time else None,
            "measures": [{"name": m, "rate": is_rate(m)} for m in measures], "rows": rows}


def _explore(a: Analysis, df, p: Profile, f: _Facts) -> None:
    base = a.base
    partial = _partial_period(base, p) if p.time else None
    if partial is not None:
        base = base[pd.to_datetime(base[p.time]) < partial[0]]
    c = cube(base, p)
    dims, ms = c["dims"], c["measures"]
    names = [x["name"] for x in dims]
    plain = [i for i, x in enumerate(dims) if not x["id"]]
    m0 = ms.index(next(m for m in ms if m["name"] == a.measure)) if any(m["name"] == a.measure for m in ms) else 0
    m1 = next((i for i in range(len(ms)) if i != m0), None)
    detail = names.index(a.dim) if a.dim in names else (plain[0] if plain else 0)
    ident = next((i for i, x in enumerate(dims) if x["id"]), None)
    if ident is not None and len(dims[detail]["values"]) < 4:
        detail = ident  # three segments make a poor bubble chart; one bubble per row reads better
    m2 = next((i for i in range(len(ms)) if i not in (m0, m1)), None)
    if p.time:  # growth against size: the "where to look" view
        x, y, sz = f"m:{m0}:growth", f"m:{m0}:total", f"m:{m1}:total" if m1 is not None else "rows"
    else:  # no dates: two numbers against each other, a third as bubble size
        x = f"m:{m1}:avg" if m1 is not None else "rows"
        y = f"m:{m0}:avg" if detail == ident else f"m:{m0}:total"
        sz = f"m:{m2}:avg" if m2 is not None else "rows"
    color = plain[0] if detail == ident and plain else detail
    hm_row = plain[0] if plain else 0
    hm_col = "time" if p.time else (str(plain[1]) if len(plain) > 1 else None)
    init = {"dims": {}, "from": None, "to": None}
    for col, vals in ((a.filters or {}).get("dims") or {}).items():
        if col in names:
            idx = [dims[names.index(col)]["values"].index(v) for v in vals if v in dims[names.index(col)]["values"]]
            init["dims"][str(names.index(col))] = idx
    if c["time"]:
        iso = c["time"]["iso"]
        lo, hi = (a.filters or {}).get("from"), (a.filters or {}).get("to")
        init["from"] = next((i for i, t in enumerate(iso) if lo and t >= lo), None) if lo else None
        init["to"] = max((i for i, t in enumerate(iso) if hi and t <= hi), default=None) if hi else None
    a.chart.update({"type": "explore", "cube": c, "init": init,
                    "defaults": {"detail": detail, "color": color, "x": x, "y": y, "size": sz, "hmRow": hm_row, "hmCol": hm_col, "hmM": m0}})
    _quadrant_fact(f, a, df, p)
    if a.dim:
        cats = by_cat(df, a.dim, a.measure, a.agg)
        a.table = {"columns": [a.dim, a.measure], "rows": [[k, num(v)] for k, v in cats.items()][:50]}
    else:
        a.table = {"columns": ["Rows", a.measure], "rows": [[f"{len(df):,}", num(float(df[a.measure].sum()))]]}


def _quadrant_fact(f: _Facts, a: Analysis, df: pd.DataFrame, p: Profile) -> None:
    """Size against growth per category (the bubble chart's default view), in words."""
    if not (a.dim and p.time and a.agg == "sum"):
        return
    d = (a.dft if a.dft is not None else df)
    d = d[d.index.isin(df.index)][[p.time, a.dim, a.measure]].dropna()
    d = d.assign(_p=_period(d, p.time, p.grain), **{a.dim: d[a.dim].astype(str)})
    pv = d.pivot_table(index="_p", columns=a.dim, values=a.measure, aggfunc="sum").sort_index()
    if len(pv) < 2 or pv.shape[1] < 4:
        return
    first, last, total = pv.iloc[0], pv.iloc[-1], pv.sum()
    growth = ((last - first) / first.abs() * 100).where(first > 0).dropna()
    if len(growth) < 4:
        return
    big, fast = total[growth.index] >= total[growth.index].median(), growth > growth.median()
    stars = list(growth[big & fast].sort_values(ascending=False).index)
    rising = list(growth[~big & fast].sort_values(ascending=False).index)
    slowing = list(growth[big & ~fast].sort_values().index)
    g0, g1 = label(pv.index[0], p.grain), label(pv.index[-1], p.grain)
    bits = []
    if stars:
        bits.append(f"big and growing fastest: {', '.join(f'{k} ({pct(growth[k])})' for k in stars[:3])}")
    if rising:
        bits.append(f"smaller but growing fast: {', '.join(f'{k} ({pct(growth[k])})' for k in rising[:3])}")
    if slowing:
        bits.append(f"big but growing slowest: {', '.join(f'{k} ({pct(growth[k])})' for k in slowing[:3])}")
    if bits:
        f.add("driver", f"Size against growth by {a.dim} ({g0} to {g1}): " + "; ".join(bits) + ".", 2.6)
